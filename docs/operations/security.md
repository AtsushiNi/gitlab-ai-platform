# セキュリティ

- ステータス: 完了(初版。実装の拡張に追従して更新する)
- 対応Issue: [#13](https://github.com/AtsushiNi/gitlab-ai-platform/issues/13) (D-9)

> **このドキュメントは [X-1(セキュリティレビュー)](../../references/タスク整理.md)の判断基準になる。**
> 実装(コード)を正とし、記述が実態とずれていたらコード側が正しい。挙動を変える変更をしたら
> このファイルも同じPR/コミットで更新すること([docs/README.md](../README.md)更新ルール1)。

AIエージェント(ヘッドレスのClaude Code Runner、および対話型Claude Codeが呼び出す
GitLab Adapter MCP Server)に許可する操作・禁止する操作を定義し、それぞれが
実装上どう担保されているかをコードの参照付きでまとめる。あわせてGitLab PAT・
AWS(Bedrock)認証情報の管理方法を記載する。

## 1. 全体方針

**許可リスト方式**([ADR-0002](../adr/0002-gitlab-adapter-interface.md))。
「AIに危険な操作をしないよう指示する」というプロンプト上の約束事には頼らない。
GitLabへの書き込み操作は、コード上メソッドとして存在するものだけが呼び出せる形にし、
禁止したい操作(merge・protected branchへの直push・branch削除・管理操作・
Issue/MRの状態遷移)は**メソッド自体を実装しない/引数自体を持たせない**ことで、
実行時チェックではなく構造的に「そもそも呼び出しようがない」状態にする。

この方針は次の3層で一貫させている(詳細は各節を参照):

1. **GitLab Adapter**(`gitlab_adapter/`) — 許可リストの一次的な機構。Protocol定義と
   REST実装の両方が許可リストの外に出ない
2. **GitLab Adapter MCP Server**(`adapter_mcp_server/`) — 対話型Claude Codeにツールとして
   公開する層。GitLab Adapterの許可リストをそのまま透過するだけで、新しい権限を追加しない
3. **Claude Code Runner**(`runner/`) — ヘッドレスのClaude Codeを起動する層。
   `--dangerously-skip-permissions`のような全許可の近道を提供しない

## 2. AIに許可する操作・禁止する操作

### 2.1 読み取り操作(`GitLabReader`、8メソッド)— 全許可

`read_api`スコープのPATのみで動作する想定([`gitlab_adapter/protocol.py`](../../src/gitlab_ai_platform/gitlab_adapter/protocol.py))。

| メソッド | 内容 |
|---|---|
| `get_version` | GitLabのバージョン文字列取得 |
| `list_merge_requests` | MR一覧取得(ラベル・state絞り込み可) |
| `get_merge_request` | MR詳細取得 |
| `get_merge_request_diffs` | MRのファイル単位diff取得 |
| `list_merge_request_discussions` | MRコメント(スレッド単位)取得 |
| `list_issues` | Issue一覧取得(ラベル・state絞り込み可) |
| `get_issue` | Issue詳細取得 |
| `get_default_branch` | プロジェクトのdefault branch名取得(M4-8、ADR-0032) |

### 2.2 書き込み操作(`GitLabWriter`、7メソッド)— 許可リストのみ

| メソッド | 内容 | 備考 |
|---|---|---|
| `create_branch` | `ref`を起点にbranch作成 | M4-8(実装フェーズ)がdefault branchを起点に実装用branchを作成する際に使用(§3.3)。実装フェーズが呼ぶ書き込みメソッドはこれのみで、`push_file_changes`は呼ばない(M4-9のスコープ) |
| `push_file_changes` | branchへファイル変更をコミットしてpush | 対象branchがprotectedの場合は`ProtectedBranchError`で拒否(§3.1) |
| `create_merge_request` | MR作成 | |
| `create_merge_request_comment` | MRへのコメント投稿 | |
| `update_merge_request` | MRのタイトル・説明の更新 | `state_event`(close/reopen/merge相当)に対応する引数がメソッドシグネチャ自体に存在しない |
| `create_issue` | Issue作成 | |
| `update_issue` | Issueのタイトル・説明の更新 | `update_merge_request`と同様、状態遷移用の引数が存在しない |

### 2.3 禁止する操作(コード上メソッドとして存在しない)

- **merge**(MRのマージ)
- **protected branchへの直push**(`push_file_changes`は対象branchがprotectedなら拒否。§3.1)
- **branch削除**
- **プロジェクト作成・メンバー管理等の管理操作**
- **Issue/MRのクローズ・再オープン等の状態遷移**(`update_issue`/`update_merge_request`に
  `state_event`相当の引数がない)

これらは`GitLabReader`/`GitLabWriter`のいずれにもメソッドとして定義されておらず、
`GitLabRestAdapter`(具象実装)にも対応するメソッドが存在しない。GitLab REST API上は
可能な操作でも、Adapterを経由する限り呼び出す手段がない。

## 3. 実装上の担保方法

### 3.1 GitLab Adapter(`gitlab_adapter/`)

- [`protocol.py`](../../src/gitlab_ai_platform/gitlab_adapter/protocol.py): `GitLabReader`/
  `GitLabWriter`という`typing.Protocol`に、許可する操作だけをメソッドとして定義する。
  禁止操作(merge等)は意図的にメソッドとして持たせない
- [`rest.py`](../../src/gitlab_ai_platform/gitlab_adapter/rest.py)(`GitLabRestAdapter`):
  具象実装側でも許可リストの7メソッドのみを実装する。Protocolに無いメソッドを実装側だけで
  追加することもしない(=Adapterのpublicなメソッド集合が許可リストと一致する)
- `update_merge_request`/`update_issue`は`_build_update_body`が`title`/`description`
  のみをリクエストボディに詰める。`state_event`はこの関数の引数にも存在しないため、
  呼び出し元がどう呼んでも送信されようがない(構造的な制約であり、実行時のバリデーションではない)
- `push_file_changes`は、GitLab APIのCommits APIへ到達する**前**に
  `_reject_if_branch_protected`が対象branchのprotectedフラグを確認し、protectedなら
  `ProtectedBranchError`を送出して拒否する(protected branch判定の実行時チェック自体は
  Adapter実装側の責務。Protocol側のdocstringにその旨明記)
- 書き込み7メソッドはすべて、呼び出し結果(`success`/`rejected_protected_branch`/`error`)を
  `_record_write`で構造化ログに記録する。commit本文・コメント本文など機微・任意長になりうる
  内容は含めず、`project`/`branch`/`mr_iid`等の識別子のみを記録する
  (X-1セキュリティレビューの証跡)
- テスト: [`tests/gitlab_ai_platform/gitlab_adapter/test_protocol.py`](../../tests/gitlab_ai_platform/gitlab_adapter/test_protocol.py)、
  [`test_rest.py`](../../tests/gitlab_ai_platform/gitlab_adapter/test_rest.py)

### 3.2 GitLab Adapter MCP Server(`adapter_mcp_server/`)

対話型Claude Code(VS Code拡張・CLI)が実行中に能動的に呼び出す経路
([ADR-0010](../adr/0010-gitlab-mcp-tool-bridge.md)、
[specs/adapter-mcp-server.md](../specs/adapter-mcp-server.md))。**新しい権限を一切追加しない**、
GitLab Adapterの許可リストを透過するだけの層という設計。

- [`tools.py`](../../src/gitlab_ai_platform/adapter_mcp_server/tools.py)の`TOOL_FACTORIES`は、
  `GitLabAdapter`(読み取り8+書き込み7=15メソッド、M4-8で`get_default_branch`を追加)と
  1メソッド=1ツールで完全に対応する対応表。merge等はAdapter自体にメソッドが存在しないため、
  この対応表にも追加しようがない
- [`server.py`](../../src/gitlab_ai_platform/adapter_mcp_server/server.py)の
  `ALLOWED_TOOL_NAMES = frozenset(TOOL_FACTORIES)`がMCPサーバーに登録するツール名の全集合
- 未登録のツール名(`merge`等)を呼び出すと、MCP Python SDK側で「未知のツール」として
  `ToolError`になる(禁止操作が呼び出せないことの実装上の根拠)
- 認証情報(GitLab PAT)はどのツール関数の引数にも戻り値にも登場しない。
  `GitLabRestAdapter`がコンストラクタで受け取った時点で内部化されており、
  `tools.py`/`server.py`はAdapterのメソッド呼び出しを仲介するだけ
- テストで以下を担保:
  - `ALLOWED_TOOL_NAMES`が現時点の15メソッドという決め打ち集合と完全一致
  - `ALLOWED_TOOL_NAMES`が`GitLabReader`/`GitLabWriter`のProtocol由来の集合と完全一致
    (Adapter側にメソッドが増えた場合、このテストが先に落ちて追従漏れに気づける)
  - merge・branch削除等の禁止操作名を`call_tool`で呼び出すと`ToolError`になる
  - テスト: [`tests/gitlab_ai_platform/adapter_mcp_server/test_server.py`](../../tests/gitlab_ai_platform/adapter_mcp_server/test_server.py)、
    [`test_tools.py`](../../tests/gitlab_ai_platform/adapter_mcp_server/test_tools.py)

### 3.3 Claude Code Runner(`runner/`)・レビューパイプライン

ヘッドレスのClaude Codeを起動してMRレビューを行う経路(M1-7、
[ADR-0005](../adr/0005-claude-code-runner-design.md)、
[specs/claude-code-runner.md](../specs/claude-code-runner.md))。GitLab Adapter MCP Serverとは
別経路であり、対話型のようにAIがツールとして能動的にGitLab操作を呼び出す手段は提供しない
(`build_prompt`でMR情報を静的なプロンプト文字列に埋め込むだけ)。

- [`subprocess_runner.py`](../../src/gitlab_ai_platform/runner/subprocess_runner.py)は
  `--dangerously-skip-permissions`を`SubprocessClaudeCodeRunner`のインターフェース上
  どこにも公開しない。GitLab Adapterと同じ考え方で、危険な操作への近道をコード上用意しない
- `allowed_tools`/`disallowed_tools`/`permission_mode`は`run`の引数として公開されており、
  **呼び出し側(CLI)が用途に応じて権限を調整する**設計になっている
  (Adapter層の「メソッドとして存在しない」という構造的な保証とは強さが異なる点に注意。
  ここは呼び出し側の設定に依存する)
- 現状のレビュー実行経路([`cli/single_run.py`](../../src/gitlab_ai_platform/cli/single_run.py)の
  `execute_review`)は、GitLab Adapterを`GitLabReader`型(読み取り専用のProtocol)として
  受け取り、`GitLabWriter`のメソッドを一切呼び出さない。静的型としても書き込みメソッドに
  アクセスできない
- レビュー結果は`review/storage.py`の`save_review`によって常にローカルの`reviews/`配下
  (`config.toml`の`reviews.root`)に保存される。GitLabへの書き込み通信は発生しない
  (`docs/guide/getting-started.md`「何をしないか」も参照。同ページは書き込みメソッド数が
  M2-10追加前の「4メソッド」のままだが、レビューパイプラインが書き込み系を一切呼ばないという
  結論自体は現状も変わらない)
- `issue-analysis`/`design`/`plan`(M4-3〜M4-7)も`run_prompt`を使うが、いずれも`allowed_tools`を
  指定せずに呼び出しており(空タプル)、実際のファイル編集・シェルコマンド実行の権限は
  与えられていない(読み取り・分析結果のJSON出力のみ)。

### 3.4 実装フェーズ(`implement/`)— このリポジトリで初めてEdit/Write/Bashを許可する経路

実装フェーズ(M4-8 [#114](https://github.com/AtsushiNi/gitlab-ai-platform/issues/114)、
[ADR-0033](../adr/0033-implement-phase.md)、
[specs/implement-phase.md](../specs/implement-phase.md))は、無人実行パイプラインの中で
初めてClaude Codeに実際のファイル編集・シェルコマンド実行の権限を与えるフェーズである。
これまでの§3.3の「読み取り専用の経路」という前提がこのフェーズには当てはまらないため、
権限設計・多層防御の構成を独立した節として記録する。

**権限設定**(`cli/dispatcher.py`の`build_implement_handler`が`run_prompt`に渡す値):

| 設定 | 値 | 意図 |
|---|---|---|
| `allowed_tools` | `("Edit", "Write", "Bash")` | 実装・テスト実行に必要な最小限の権限。これより絞る余地は無い |
| `disallowed_tools` | `("Bash(git push:*)",)` | `git push`を明示的に禁止する。**Adapter層の「メソッドとして存在しない」という構造的な保証とは強さが異なる**多層防御の1つ(下記参照) |
| `permission_mode` | `"acceptEdits"` | headless実行のためEdit/Write系ツールの確認を自動承認する。`--dangerously-skip-permissions`相当の全許可モード(`"bypassPermissions"`)は使わない(既存方針、ADR-0005) |

**実際のGitLabへの書き込み(push)が発生しないことの3層の担保**(ADR-0033):

1. **Workspace Manager**([`workspace/git_workspace.py`](../../src/gitlab_ai_platform/workspace/git_workspace.py)):
   `git clone`/`git fetch`/`git worktree`/`git reset --hard`のみを実装しており、`git push`は
   どこにも実装していない(`grep -n "push" src/gitlab_ai_platform/workspace/git_workspace.py`は
   何もヒットしない)
2. **実装フェーズのJobHandler**([`cli/dispatcher.py`](../../src/gitlab_ai_platform/cli/dispatcher.py)の
   `build_implement_handler`): `GitLabWriter`のうち`create_branch`(branch作成)のみを呼び出す。
   `push_file_changes`(Commits API経由のファイル変更コミット)は一切呼び出さない(M4-9のスコープ)
3. **認証情報のスコープ・ロール**(§4.1): 自動実行系用のGitLab PAT
   (`GITLAB_AI_PLATFORM_GITLAB_TOKEN`)は`read_api`スコープかつアカウントロールがReporterの
   ままである(実装フェーズもこのトークンを使う。対話型MCP用のDeveloperロール・`api`スコープの
   トークンとは別)。仮にBash経由で`git push`相当の操作が試みられても、GitLab側のスコープ・
   ロールの両方で拒否される。加えて`GitWorkspaceManager`の`git_config`(credential.helper)は
   Workspace Manager自身のgit呼び出しにのみ`-c`引数として渡され、worktreeの`.git/config`には
   永続化されない(=Claude CodeがBash経由で素の`git push`を叩いても、認証情報がworktree内に
   残っていないため、そもそも認証できず失敗する可能性が高い)

**残存リスクと運用上の注意(このフェーズで新たに生じる考慮事項)**:

- `Bash`を許可している以上、`disallowed_tools`の`Bash(git push:*)`パターンは
  Claude Codeの協力的な振る舞いを前提とした一段の防御であり、Adapter層のように
  「そもそも呼び出しようがない」という構造的な保証ではない。実効的な安全性は上記3層のうち
  2・3(JobHandlerが`push_file_changes`を呼ばないこと、認証情報のスコープ・ロール)に
  最終的に依存している
- `SubprocessClaudeCodeRunner`は`claude`プロセスの環境変数として`os.environ`(Pythonプロセス
  自身の環境)をそのまま引き継ぐ(§4.2参照)。`Bash`が許可されたことで、Claude Codeがこの
  環境変数を`env`コマンド等で読み取ることが技術的に可能になる。影響を受けうる値:
  - **AWS/Bedrock認証情報**(`AWS_ACCESS_KEY_ID`等、§4.2): これらは元々OS環境変数として
    Runnerプロセスに渡される設計であり、本フェーズに限らず`claude`プロセスの環境には常に
    存在する。ただし`Bash`が許可されたのは本フェーズが初めてであり、実質的に読み取り可能に
    なったのは本フェーズからである。**Bedrockの認証情報は`bedrock:InvokeModel`相当の
    最小権限に絞ることを強く推奨する**(IAMポリシー側の対処。このリポジトリのコード側では
    スコープを絞れない)
  - **自動実行系用GitLab PAT**(`GITLAB_AI_PLATFORM_GITLAB_TOKEN`): `config/loader.py`は
    `.env`ファイルと実際にexportされたOS環境変数のどちらからでも読み込める(§4.1)。
    `.env`ファイルのみに書いた場合、この値は`os.environ`(Runnerプロセスの実際の環境変数)
    には現れない(`GitWorkspaceManager`はこの値を`config.gitlab_token`から明示的に
    `token_env`として自身のgit呼び出しにのみ注入しており、プロセス全体の環境変数を
    汚染しない)。**一方、この値を実際にOS環境変数としてexportする運用を選んだ場合、
    本フェーズ以降はClaude Codeからも読み取り可能になる**。このリスクを避けるため、
    実装フェーズを実行する`worker`プロセスについては、`GITLAB_AI_PLATFORM_GITLAB_TOKEN`を
    `.env`ファイル経由で供給する運用を推奨する(OS環境変数へのexportは不要。
    [operations/configuration.md](configuration.md)参照)
  - 上記いずれの場合も、漏洩した場合の影響は自動実行系用アカウントの権限
    (`read_api`スコープ、Reporterロール)に留まる。対話型MCP用の`api`スコープ・Developer
    ロールのトークン(§4.1)はこの経路には一切登場しない
- 将来的な追加の緩和策として、実装フェーズをコンテナ/サンドボックス環境で実行する
  ([operations/docker-runtime.md](docker-runtime.md)の実行環境をこのフェーズ専用に
  強化する等)ことが考えられるが、本Issue(M4-8)のスコープ外とし、今後の課題として残す

## 4. トークン・シークレットの管理

### 4.1 GitLab PAT

**AI用GitLabアカウント・トークンスコープの設計は[ADR-0019](../adr/0019-gitlab-token-scoping.md)
(M3-8)で決定した。** 以下はその実装を反映した現状。

- **アカウント分離**: 人間の個人アカウントは使わない。用途に応じて2つのAI用アカウントを使う
  ([ADR-0019](../adr/0019-gitlab-token-scoping.md)「決定 1」):
  - 自動実行系用アカウント(`review`単発実行・`watch`のPoller/Webhook経由レビュー実行) —
    構造的に読み取りしか行わない経路(§3.3)のため、ロールは**Reporter**に留める
  - 対話型GitLab Adapter MCP Server用アカウント — branch作成・push・MR/Issue作成等の
    書き込み(§2.2)を行うため、ロールは**Developer**(Maintainer以上は与えない)
- **用途別トークン**: 上記2アカウントに対応して、PATも2種類の環境変数で供給する
  (`config.toml`(リポジトリにコミットされうる)には**書かない**、`.env`ファイルまたは
  実際にexportされたOS環境変数経由。`.env`・`config.toml`はいずれも`.gitignore`対象):
  - `GITLAB_AI_PLATFORM_GITLAB_TOKEN`(`GITLAB_TOKEN_ENV_KEY`) — 自動実行系用。
    `cli/single_run.py`・`cli/watch.py`が使う
  - `GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP`(`GITLAB_MCP_TOKEN_ENV_KEY`) — 対話型MCP
    Server用。`adapter_mcp_server/main.py`が使う。**未設定の場合は`GITLAB_AI_PLATFORM_GITLAB_TOKEN`
    にフォールバックする**(用途別トークンの分離は必須ではなくオプション。
    [`config/models.py`](../../src/gitlab_ai_platform/config/models.py)の`Config.from_raw`)
- **スコープ最小化**([`.env.example`](../../.env.example)、
  [references/spike-S2-gitlab-rest-api.md](../../references/spike-S2-gitlab-rest-api.md) §3):
  - `GITLAB_AI_PLATFORM_GITLAB_TOKEN`(自動実行系)は`read_api`で足りる
  - `GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP`(対話型MCP)は書き込み操作(branch作成・push・
    MR/Issue作成等)を含むため`api`スコープが必要。`api`は常に読み書き全体を含み、
    GitLabのスコープ機構だけでは「コメントは許可するがmergeは禁止」という粒度の制御は
    できない。この粒度の制御は§3.1のAdapter層のコード側で機構的に絞り込んでいる
    (GitLabスコープに依存しない設計)
  - 上記のアカウントロール分離(自動実行系=Reporter)と組み合わせることで、
    仮にPATのスコープ設定を誤ってもGitLab側のロールが書き込みAPIを拒否する二重の防御になる
    ([ADR-0019](../adr/0019-gitlab-token-scoping.md)参照)
- **ログへの非露出**:
  - `Config.__repr__`は両トークンを`'***'`にマスクする([`config/models.py`](../../src/gitlab_ai_platform/config/models.py))
  - `ConfigError`のメッセージにはPATの値そのものを含めない
  - GitLab Adapter MCP Serverのツール引数・戻り値・エラーメッセージのいずれにもトークン文字列が
    含まれないことを[`test_secrets.py`](../../tests/gitlab_ai_platform/adapter_mcp_server/test_secrets.py)で担保
  - `SubprocessClaudeCodeRunner`は認証情報を`Popen`の`env`引数経由でのみ渡し、コマンド引数
    には含めない。実行ログ(`log_dir`配下のJSON)にも`env`の中身は記録しない
- **git操作でのPAT供給**: HTTPS経由のgit clone/fetchは、PATを`.git/config`やコマンド引数に
  残さないよう、gitのcredential helperプロトコル経由で都度供給する
  ([`cli/single_run.py`](../../src/gitlab_ai_platform/cli/single_run.py)の
  `_credential_helper`/`build_workspace_manager`)。トークンの値自体はcredential helperの
  コマンド文字列には埋め込まず、環境変数名だけを埋め込み、実際の値はsubprocessの環境変数として
  注入する(自動実行系用トークンのみを使う。MCP用トークンはこの経路では使われない)

### 4.2 AWS(Bedrock)認証情報

- `CLAUDE_CODE_USE_BEDROCK`/`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`等は
  **OS環境変数として設定する**(`.env`ファイルには書かない)。`config/loader.py`の`.env`読み込みは
  GitLab PAT専用の口であり、`AWS_*`をそこに書いても`claude`プロセスには渡らない
  ([setup-windows.md](setup-windows.md) §3.2)
- `SubprocessClaudeCodeRunner`はPythonプロセス自身の`os.environ`をそのまま引き継いで`claude`を
  起動するだけで、AWS認証情報を個別にパース・保管するロジックは持たない
- 実行ログにはコマンド(`["claude", "-p", <prompt>, ...]`)のみを保存し、`env`の中身はログ化対象に
  含めない設計のため、AWS認証情報はログへ出力されない

### 4.3 トークンの棚卸し(運用ガイドライン)

自動化された仕組みはまだない。X-2(コスト・使用量の記録)や運用しながら育てる
[D-10 運用・トラブルシューティングガイド](troubleshooting.md)と合わせて、
今後以下の観点を運用ルールとして整備することを推奨する:

- 発行済みPAT・AWSクレデンシャルの一覧。§4.1の2アカウント運用([ADR-0019](../adr/0019-gitlab-token-scoping.md))
  に合わせて、少なくとも次の列を記録する: アカウント名・**用途(自動実行系/対話型MCP)**・
  付与スコープ(`read_api`/`api`)・付与ロール(Reporter/Developer)・発行者・発行日・最終利用日
- 定期的なローテーション(特にPATは有効期限を設定し、無期限発行を避ける)。対話型MCP用トークン
  (`api`スコープ、書き込み可能)は自動実行系用トークン(`read_api`スコープ)より影響範囲が
  大きいため、優先してローテーション間隔を短くすることを推奨する
- 退職・異動・プロジェクト終了時のトークン失効
- AI用GitLabアカウントのロールが必要以上に強くなっていないかの定期確認
  (自動実行系=Reporter、対話型MCP=Developerを超えていないか。§4.1)

### 4.4 PAT漏洩時の対応手順

漏洩(誤コミット・チャットへの平文貼付等)に気づいた場合の対応手順。
「PATが切れた/権限不足になった」場合の復旧手順(通常の失効・再発行)は
[troubleshooting.md §2.1](troubleshooting.md#21-gitlab-api認証切れpat失効-終了コード11)を参照。
ここでは**漏洩**という異常事態への対応に絞る。

1. 漏洩したトークンがどちらのアカウント由来か特定する(§4.1の自動実行系用/対話型MCP用の
   どちらか)。特定できない場合は両方とも失効させる
2. 社内GitLabの`User Settings > Access Tokens`で該当PATを即座に**Revoke**する。
   新規発行と`.env`の更新は事後でよい。まず失効を優先する
3. 対話型MCP用トークン(`api`スコープ)が漏洩した場合、書き込み操作(branch作成・push・
   MR/Issue作成・コメント投稿)が第三者に悪用されうる。該当プロジェクトのAudit Events・
   最近のMR/Issue・pushされたcommitを確認し、意図しない変更が無いか確認する
4. 自動実行系用トークン(`read_api`スコープ、かつアカウントロールがReporter)が漏洩した
   場合の影響範囲は読み取りのみ(非公開プロジェクトのコード・Issue内容の閲覧)に限定される。
   とはいえ機微情報が漏洩しうるため、同様に失効を優先する
5. 新しいPATを発行し、`.env`(または実環境変数)の該当キー(`GITLAB_AI_PLATFORM_GITLAB_TOKEN`
   または`GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP`)を更新する
   ([configuration.md](configuration.md)「シークレット」節)
6. §4.3の棚卸し表の当該行を更新し、失効日・再発行日を記録する

## 5. X-1(セキュリティレビュー)での確認観点

このドキュメントを判断基準として、次を確認する:

1. §2で許可リストに載っていない操作(merge・protected branchへの直push・branch削除・
   管理操作・Issue/MRの状態遷移)が、GitLab Adapter・GitLab Adapter MCP Server・
   Claude Code Runnerのいずれの経路からも呼び出せないこと(§3の各テストが通っていること)
2. 書き込み操作を新規追加する変更が、許可リスト方式([ADR-0002](../adr/0002-gitlab-adapter-interface.md))
   に沿ってADR・仕様更新を伴って行われていること(新しい書き込みメソッドをAdapterに追加する場合、
   まずGitLab Adapter側の許可リストを拡張するIssueを起票し、そちらが決定されてから
   MCP Server等の他コンポーネントに反映する順序を守る)
3. GitLab PAT・AWS認証情報がログ・例外メッセージ・MCPツールの引数/戻り値のいずれにも
   含まれていないこと(§4.1・§4.2の各テストが通っていること)
4. §4.3の棚卸し観点に沿って、発行済みトークンが最小権限・有効期限管理されていること

## 関連ドキュメント

- [ADR-0002: GitLab Adapterインターフェース設計](../adr/0002-gitlab-adapter-interface.md) — 許可リスト方式の原点
- [ADR-0005: Claude Code Runner設計](../adr/0005-claude-code-runner-design.md) — `--dangerously-skip-permissions`を提供しない方針
- [ADR-0010: GitLab Adapter MCP Serverの設計](../adr/0010-gitlab-mcp-tool-bridge.md) — 「新しい権限を一切追加しない」ことの担保表
- [ADR-0019: AI用GitLabアカウントとトークンスコープの設計](../adr/0019-gitlab-token-scoping.md) — アカウント分離・用途別トークン・棚卸し・漏洩時対応
- [ADR-0031: Workspace ManagerのIssue単位worktree対応](../adr/0031-issue-workspace.md)
- [ADR-0032: GitLab Adapterへのdefault branch取得メソッドの追加](../adr/0032-default-branch-lookup.md)
- [ADR-0033: 実装フェーズ(Job種別`implement`)の設計](../adr/0033-implement-phase.md) — 本ドキュメント§3.4の元になった、Edit/Write/Bash権限付与とgit push禁止の多層防御設計
- [specs/gitlab-adapter.md](../specs/gitlab-adapter.md)
- [specs/adapter-mcp-server.md](../specs/adapter-mcp-server.md)
- [specs/claude-code-runner.md](../specs/claude-code-runner.md)
- [specs/implement-phase.md](../specs/implement-phase.md) — §3.4で扱う実装フェーズの仕様
- [operations/configuration.md](configuration.md) — シークレット関連の設定項目一覧
- [operations/setup-windows.md](setup-windows.md) — PAT発行手順・Bedrock認証設定手順
- [guide/getting-started.md](../guide/getting-started.md) 「何をしないか」節
- [requirements.md](../requirements.md) 「セキュリティ」節
- [references/spike-S2-gitlab-rest-api.md](../../references/spike-S2-gitlab-rest-api.md) — PATスコープの調査
- [references/タスク整理.md](../../references/タスク整理.md) — D-9・X-1の一次記述
