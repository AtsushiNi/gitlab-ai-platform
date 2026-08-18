# トラブルシューティング

- ステータス: 完了(初版。運用しながら育てる継続タスク。新しい失敗パターンに遭遇したら
  随時追記する)
- 対応Issue: [#14](https://github.com/AtsushiNi/gitlab-ai-platform/issues/14) (D-10)

`review`/`watch`サブコマンドでよくある失敗の「症状 → 原因 → 復旧手順」集。実装
(`src/gitlab_ai_platform/`の各`errors.py`・`cli/exit_codes.py`)を一次情報とし、
推測では書かない。役割分担:

- 終了コードの一覧そのものは[guide/cli-reference.md「終了コード」](../guide/cli-reference.md#終了コード)
  が正。ここでは「その終了コードが出たら何をするか」の復旧手順に重点を置く
- トークン・認証情報の管理方針(スコープ・ローテーション等の運用ルール)は
  [operations/security.md](security.md)が正。ここでは「PATが切れた/権限不足になった時に
  何をするか」の復旧手順だけを書く
- 短い疑問への即答は[guide/faq.md](../guide/faq.md)を参照。本ドキュメントは
  エラーに遭遇した後の切り分け・復旧手順に重点を置く

## 1. まず確認すること

1. **終了コードで失敗した段階を特定する。** `review`/`watch`はどちらもパイプラインの
   どの段階(GitLab Adapter/Workspace/Runner/Review/State Store)で失敗したかを
   終了コードと標準エラー出力のメッセージで示す
   ([cli/main.py](../../src/gitlab_ai_platform/cli/main.py)、詳細は
   [cli-reference.md「終了コード」](../guide/cli-reference.md#終了コード))。
2. **`--log-level DEBUG --log-dir <dir>` を付けて再実行する。** 共通オプションのため
   サブコマンド名より前に指定する(例:
   `gitlab-ai-platform --log-level DEBUG --log-dir logs review group/project-a 123`)。
   構造化ログ(JSON、日次ローテーション)が`<dir>`配下に残り、`cli.review_failed`/
   `watch.review_failed`等のイベント名で失敗段階・エラーメッセージを確認できる。
3. **`RunnerError`系は実行ログのパスが標準エラー出力に表示される。** `実行ログ: <path>`
   の行を確認する(下記2.3参照)。
4. **再現させたい場合は`review`サブコマンドで単発実行する。** `watch`は失敗したレビューを
   自動リトライしない([cli-reference.md](../guide/cli-reference.md)「`watch`実行中の
   1件のレビュー失敗は終了コードに現れない」)ため、原因調査には同一MRに対して
   `review`を繰り返し実行できる単発実行を使う。

## 2. 症状別の復旧手順

### 2.1 GitLab API認証切れ(PAT失効)— 終了コード`11`

**症状**: 標準エラー出力に`GitLab Adapterエラー: <メッセージ>`。メッセージはGitLab APIの
エラーレスポンス本文(`message`または`error`フィールド)がそのまま含まれる
([`gitlab_adapter/rest.py`](../../src/gitlab_ai_platform/gitlab_adapter/rest.py)の
`_error_message`)。`GitLabApiError`は`status_code`属性を持つ(現在の実装では
標準エラー出力には表示されないが、Pythonから直接呼び出す場合や構造化ログの
`error`フィールドで文面から判別できる)。

**原因**:

- PAT失効(有効期限切れ・手動失効)→ GitLab APIが`401 Unauthorized`を返す
- PATのスコープ不足・AI用アカウントのロール変更 → `403 Forbidden`
- `401`/`403`は[`_RETRYABLE_STATUS_CODES`](../../src/gitlab_ai_platform/gitlab_adapter/rest.py)
  (`429`/`500`/`502`/`503`/`504`)に含まれないため、リトライされず即座に失敗する
  (=詰まって遅くなるのではなく、すぐにエラーとして気づける)

**復旧手順**:

1. エラーメッセージに`401`/`403`相当の文言(GitLabのエラー本文)が含まれるか確認する
2. 社内GitLabの `User Settings > Access Tokens` で新しいPATを発行する
   ([setup-windows.md §2](setup-windows.md#2-gitlab-personal-access-tokenpatの発行))。
   `review`/`watch`/`worker`サブコマンドは自動実行系用アカウント(`api`スコープ・Developer
   ロール、[ADR-0037](../adr/0037-automated-token-scope-upgrade.md))を使う経路のため、
   通常は同じ用途・スコープで再発行する
3. `.env`の`GITLAB_AI_PLATFORM_GITLAB_TOKEN`を新しい値に更新する
   ([configuration.md](configuration.md)「シークレット」節)。OS環境変数で上書きしている
   場合はそちらも更新する。対話型GitLab Adapter MCP Serverが同様のエラーを返す場合は、
   代わりに`GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP`(未設定なら上記と同じ値)を確認・更新する
4. 同じコマンドを再実行する

トークンのスコープ設計・アカウント分離・ローテーション方針そのものは
[security.md §4.1](security.md#41-gitlab-pat)・[ADR-0019](../adr/0019-gitlab-token-scoping.md)を参照。

### 2.2 worktree破損・ディスク上限超過 — 終了コード`12`

**症状**: 標準エラー出力に`Workspace Managerエラー: <メッセージ>`。原因の例外は
`GitCommandError`(`command`/`returncode`/`stderr`属性を持つ)または
`DiskLimitExceededError`
([`workspace/errors.py`](../../src/gitlab_ai_platform/workspace/errors.py))。

**原因1: `GitCommandError`(gitコマンドが非ゼロ終了)**

- bare clone(`<workspace.root>/repos/<slug>.git`)やworktree
  (`<workspace.root>/worktrees/<slug>/mr-<iid>/`)がプロセスの異常終了・強制終了・
  手動削除等で中途半端な状態になっている
- なお、worktreeディレクトリだけが外部から削除された場合(bare repo側の
  `.git/worktrees/`管理情報は残っている)は`prepare`が毎回`git worktree prune`を
  実行するため自己復旧する([`git_workspace.py`](../../src/gitlab_ai_platform/workspace/git_workspace.py)
  のモジュールdocstring、Spike S-3 §5)。この復旧が効かないのは、bare repo自体が
  壊れている場合や、worktree内の`.git`ファイル(gitdir参照)が壊れている場合

**原因2: `DiskLimitExceededError`**

- `<workspace.root>/worktrees/`配下の合計サイズが`config.toml`の
  `workspace.max_disk_mb`(既定`5000`)を超えており、GC(`collect_garbage`、
  最終利用時刻が古いworktreeから破棄)を実行してもなお収まらない

**復旧手順**:

1. `GitCommandError`の場合、例外の`stderr`(構造化ログの`error`フィールドに含まれる)で
   gitコマンド自体のエラーメッセージを確認する
2. 該当worktreeを手動で削除する。bare repoは`<root>/repos/`配下、worktreeは
   `<root>/worktrees/<slug>/mr-<iid>/`配下(`slug`は`_paths.slugify_project`によるproject名の
   変換結果)。削除後、次回`review`/`watch`実行時に自動的に再clone・再作成される
   (`prepare`はbare repoが存在しなければ`clone --bare`からやり直す)
3. bare repoごと壊れている場合は`<root>/repos/<slug>.git`ディレクトリごと削除する
   (worktreeが残っていると削除できないため、先にworktree側を削除する)
4. `DiskLimitExceededError`の場合は、`config.toml`の`workspace.max_disk_mb`を引き上げるか、
   `<root>/worktrees/`配下の不要なworktreeを手動削除して空き容量を確保する。他のレビューが
   実行中の間はそのworktreeはGCの対象になる(最終利用時刻が更新されるため実行中に破棄
   されることはない)ので、実行中のレビューが完了するのを待つ選択肢もある

### 2.3 Claude Codeのタイムアウト・起動失敗 — 終了コード`13`

**症状**: 標準エラー出力に`Claude Code Runnerエラー: <メッセージ>`と、`log_path`属性が
あれば`実行ログ: <path>`が続けて表示される([cli/main.py](../../src/gitlab_ai_platform/cli/main.py))。
原因の例外は`ClaudeCodeTimeoutError`/`ClaudeCodeOutputError`/`ClaudeCodeNotFoundError`
([`runner/errors.py`](../../src/gitlab_ai_platform/runner/errors.py))で、いずれも
`log_path`を持つ。

**原因**:

- **`ClaudeCodeTimeoutError`**: `timeout_seconds`(既定`1800`秒、`config.toml`の
  `runner.timeout_seconds`または`review`の`--timeout`)以内にClaude Codeが終了せず、
  SIGTERM送出後の猶予期間(既定10秒)内にも終了しなかった(=ハングした)ため
  SIGKILLで強制終了し、それでも有効な結果を得られなかった場合に送出される
  ([`subprocess_runner.py`](../../src/gitlab_ai_platform/runner/subprocess_runner.py))。
  SIGTERM後に正常終了できた場合(`terminal_reason: aborted_*`)はこの例外にはならない点に
  注意(=このエラーが出るのは本当にハングしたケースに限られる)
- **`ClaudeCodeOutputError`**: プロセスは終了したが、標準出力が空、JSONとして解釈できない、
  または期待するJSONオブジェクト形式でなかった場合
- **`ClaudeCodeNotFoundError`**: `claude`コマンドが見つからない(未インストール・PATH未設定)
- タイムアウトの一因として、Bedrock認証情報のクレデンシャルチェーン解決が詰まると
  最大60秒程度余分にかかることがある
  ([setup-windows.md §3.2](setup-windows.md#32-amazon-bedrock認証の設定))

**復旧手順**:

1. 標準エラー出力に表示された実行ログのパス(`log_dir`配下のJSON、既定
   `logs/runner/<projectスラッグ>/mr-<iid>/<sha先頭12桁>-<timestamp>.json`)を開き、
   `stdout`/`stderr`/`timed_out`/`duration_seconds`を確認する
2. `ClaudeCodeNotFoundError`の場合は`claude --version`が通るか確認する
   ([setup-windows.md §3.1](setup-windows.md#31-claude-code-cliの導入))
3. Bedrock認証情報の詰まりが疑わしい場合は、`AWS_ACCESS_KEY_ID`等をOS環境変数として
   明示的に設定する(`.env`ではなくOS環境変数。`.env`はGitLab PAT専用の読み込み口のため
   `AWS_*`を書いても`claude`プロセスには渡らない)
4. 恒常的にdiffが大きいMR等でタイムアウトする場合は、`--timeout`(単発実行時)または
   `config.toml`の`runner.timeout_seconds`を引き上げる
5. プロンプトが大きすぎる場合はRunner側で自動的に切り詰められる
   (`_MAX_PROMPT_BYTES`、既定約100KB。切り詰められたことはログの`runner.prompt_truncated`
   イベントで分かる)ため、切り詰めが原因で結果が不自然な場合はこのログを確認する

### 2.4 レビュー結果の欠損・パース失敗 — 終了コード`14`

**症状**: 標準エラー出力に`レビュー結果の解析エラー: <メッセージ>`。原因の例外は
`ReviewOutputParseError`(`raw_text`属性にClaude Codeの応答全文を保持)
([`review/errors.py`](../../src/gitlab_ai_platform/review/errors.py))。標準エラー出力には
`raw_text`自体は表示されない点に注意。

**原因**([`review/parser.py`](../../src/gitlab_ai_platform/review/parser.py)):

- Claude Codeの実行自体が`is_error: true`で終了した(この場合`result_text`の中身は
  一切解釈せず即座に失敗させる。`is_error`が欠けている想定外の応答形式もエラー扱い)
- 応答に ```` ```json ... ``` ```` フェンスが見つからない、またはフェンス内・応答全文のいずれも
  JSONとして解釈できない
- JSONは取れたが結果スキーマを満たさない(`findings`が配列でない、`summary`が文字列でない、
  各`finding`の`severity`が`critical`/`major`/`minor`のいずれでもない、`file`/`rationale`/
  `suggestion`が空文字列、`line`が整数でもnullでもない、等)

**復旧手順**:

1. 対応する実行ログ(`RunnerError`と同じ`log_dir`配下のJSON)を開き、`stdout`フィールド
   (Claude Codeの生の標準出力)を確認する。この中の`result`フィールドが`raw_text`の元になる
2. `is_error: true`だった場合は、Claude Code自体が何かに失敗している(権限拒否・
   ツール呼び出し失敗等)ため、そちらの原因を先に切り分ける
   (`permission_denials`フィールドがあれば権限まわりが疑わしい)
3. JSON抽出・スキーマ検証で失敗した場合は、`result`フィールドを人間が読み、
   プロンプトの指示([`prompts.py`](../../src/gitlab_ai_platform/review/prompts.py)の
   `build_review_instructions`)にどう従わなかったかを確認する。プロンプト側の文言を
   調整して同じMRに対し`review`サブコマンドで再実行し、改善を確認する
   (`review`はデバッグ・プロンプト改善用の主要導線として、同一commitへの再実行を
   意図的に許可している)
4. 恒常的に特定パターン(コード例中の```がJSONフェンスと誤認される等)で失敗する場合は
   `_extract_trailing_json_fence`のロジック自体の見直しが必要になるため、Issueを起票する

### 2.5 `watch`の多重起動エラー — 終了コード`16`

**症状**: 標準エラー出力に`多重起動エラー: 別のwatchプロセスが既に実行中です
(ロックファイル: <path>)`。`watch`サブコマンド専用。

**原因**([`cli/lock.py`](../../src/gitlab_ai_platform/cli/lock.py)、
[ADR-0009](../adr/0009-cli-watch-design.md)):

- 同一`state_db_path`(`config.toml`の`store.db_path`)に対して2つ目の`watch`プロセスを
  起動しようとした。ロックファイルは`<db名>.lock`として`state_db_path`と同じディレクトリに
  作られ、OSのアドバイザリロック(POSIX: `fcntl.flock`、Windows: `msvcrt.locking`)で
  排他制御する

**復旧手順**:

1. ロックファイル(`<state_db_path>.lock`)の中身を開く。診断用にロック取得プロセスの
   PIDが書き込まれている(ロックの正当性自体はOSの機構に依存しており、この中身の
   正しさには依存しない)
2. タスクマネージャ(Windows)/`ps`(POSIX)でそのPIDのプロセスが本当に実行中か確認する。
   実行中であれば、それが想定していた`watch`プロセスかどうかを確認し、意図しない
   多重起動であれば片方を終了する
3. **ロックはファイルディスクリプタに紐づくOSレベルの機構のため、プロセスが異常終了すれば
   OSがプロセス終了時に自動的に解放する。** そのため通常、「PIDのプロセスは存在しないのに
   ロックが取得できない」という状態は起きない設計になっている
   ([ADR-0009](../adr/0009-cli-watch-design.md)「却下した選択肢」PIDファイル方式との比較)。
   この状態が実際に起きた場合(ネットワークドライブ等、advisory lockの解放保証が
   OSやファイルシステムによって弱いケース)は、プロセスが本当に存在しないことを
   慎重に確認した上でロックファイル自体を削除する
4. 単に前回の`watch`をCtrl+C/SIGTERMで終わらせずにターミナルごと閉じた場合は、
   プロセス自体がまだ生きていることが多い。該当ターミナル・プロセスを確認して
   graceful shutdown(Ctrl+C)させる

## 3. その他の終了コード

上記5パターン以外の終了コードも、切り分けの起点として簡単に触れる(詳細な復旧手順は
運用しながら追記する)。

| 終了コード | 意味 | 主な確認先 |
|---|---|---|
| `10` (`EXIT_CONFIG_ERROR`) | `config.toml`/`.env`読み込み失敗 | [configuration.md](configuration.md)の必須項目・型を確認 |
| `15` (`EXIT_STATE_STORE_ERROR`) | State Store(SQLite)操作失敗 | `store.db_path`のファイルが破損・ロックされていないか確認。`DuplicateReviewError`/`RecordNotFoundError`は通常アプリ側のロジックエラーであり、Issueを起票する |
| `130` (`EXIT_INTERRUPTED`) | Ctrl+Cによる中断 | 異常ではない。`watch`は通常SIGINTを`stop_event`経由のgraceful shutdown(終了コード`0`)に変換するため、この経路に来るのは`load_config`中などごく限られたタイミングのみ |

## 関連ドキュメント

- [guide/cli-reference.md](../guide/cli-reference.md) — 終了コード表の一次情報、コマンド全体の構文
- [operations/security.md](security.md) — トークン管理の運用ルール(スコープ・ローテーション)
- [operations/configuration.md](configuration.md) — `config.toml`/`.env`の全項目リファレンス
- [operations/setup-windows.md](setup-windows.md) — PAT発行手順・Claude Code/Bedrock導入手順
- [guide/faq.md](../guide/faq.md) — 短い疑問への即答集
- [ADR-0008: CLI 単発レビュー実行の設計](../adr/0008-cli-single-run-design.md)
- [ADR-0009: CLI 常駐(watch)モードの設計](../adr/0009-cli-watch-design.md)
- ソースコード: `src/gitlab_ai_platform/cli/exit_codes.py`、各コンポーネントの`errors.py`
  (`gitlab_adapter/`・`workspace/`・`runner/`・`review/`・`store/`)
