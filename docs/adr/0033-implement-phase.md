# ADR-0033: 実装フェーズ(Job種別`implement`)の設計

- Issue: [#114](https://github.com/AtsushiNi/gitlab-ai-platform/issues/114) (M4-8)
- 状態: 決定

## 背景・制約

- M4-7(実装計画フェーズ、Job種別`plan`、[ADR-0030](0030-implementation-plan-phase.md))が
  実装計画(`plan_document`・実装順のタスク一覧`tasks`)を出力する仕組みを確立した。本Issueは
  その次のフェーズ: 実際にコードを書き、テストを実行し、ローカルにcommitする
- `issue-analysis`/`design`/`plan`はいずれも「Workspace Manager(worktree)を使わず、
  Claude Codeに読み取り・分析のみを行わせる」という設計だった(ADR-0027・0029・0030)。
  本フェーズは無人実行トラックで初めて実際のファイル編集・シェルコマンド実行が必要になる
  フェーズであり、この設計をそのまま踏襲できない
- ADR-0027は「M4-6(design)・M4-8(implement)はリポジトリの実装を参照する必要があるため、
  `run`(worktree前提)、または将来`run_prompt`にworktree_pathとして実際のworktreeを渡す形を
  再検討する余地がある」と予告していた。本Issueで実際にその判断を行う
- 本Issueが対処すべき設計課題は複数あり、それぞれ次の通り個別に解決した:
  - Workspace ManagerのIssue対応(worktreeのキー空間衝突) → [ADR-0031](0031-issue-workspace.md)
  - default branchの取得 → [ADR-0032](0032-default-branch-lookup.md)
  - 本ADRは残り: Runner実行方式、Claude Codeへの権限付与、branch作成の流れ、
    テスト失敗時の扱い

## 決定

### `ClaudeCodeRunner.run_prompt`に実際のworktree_pathを渡す(新規メソッドは追加しない)

ADR-0027が予告していた「`run_prompt`にworktree_pathとして実際のworktreeを渡す」を採用する。
`run_prompt(worktree_path, prompt, *, log_key, timeout_seconds, allowed_tools, disallowed_tools,
permission_mode)`のシグネチャは既に「組み立て済みプロンプト+任意のworktree_path」という
汎用的な契約になっており(`issue-analysis`/`design`/`plan`は一時ディレクトリを渡していた
だけで、Runner自身はその中身を解釈しない)、実装フェーズは`Workspace Manager.prepare_for_issue`
(ADR-0031)が返す実際のworktreeの`path`をそのまま渡すだけで済む。`run`(MRレビュー専用、
`ReviewContext`固定)・Runnerのインターフェース自体には一切変更を加えていない。

### Claude Codeに`Edit`/`Write`/`Bash`を許可し、`git push`のみ明示的に禁止する

`cli/dispatcher.py`の`build_implement_handler`が`run_prompt`に渡す権限設定:

```python
_IMPLEMENT_ALLOWED_TOOLS = ("Edit", "Write", "Bash")
_IMPLEMENT_DISALLOWED_TOOLS = ("Bash(git push:*)",)
_IMPLEMENT_PERMISSION_MODE = "acceptEdits"
```

- **`allowed_tools`**: このリポジトリで初めて`Edit`/`Write`/`Bash`を許可する
  (`review`/`issue-analysis`/`design`/`plan`はいずれも空の`allowed_tools`で、読み取り・分析
  のみだった)。実装・テスト実行という本フェーズの目的上、ファイル編集とシェルコマンド実行は
  必須の権限であり、絞り込む余地が無い
- **`disallowed_tools`**: `Bash(git push:*)`を明示的に禁止する。ただしこれは
  `docs/operations/security.md` §3.3が既に述べている通り、GitLab Adapterの
  「メソッドとして存在しない」という**構造的な保証とは強さが異なる**、呼び出し側の設定に
  依存する多層防御の1つに過ぎない。`Bash`を許可している以上、Claude Codeが何らかの手段で
  `git push`相当の操作を試みることを完全に技術的に防ぐものではない。この禁止事項の実効性は
  最終的に別の3つの独立した層で担保している(下記「実際のgit push/リモート書き込みに
  ついて」参照)
- **`permission_mode`**: `"acceptEdits"`を使う。headless実行(`-p`)では人間が確認プロンプトに
  答えられないため、`Edit`/`Write`の確認を自動承認するモードが必要になる。
  `--dangerously-skip-permissions`相当の全許可モード(`permission_mode="bypassPermissions"`)
  には**手を出さない**(既存方針、[ADR-0005](0005-claude-code-runner-design.md)
  「`--dangerously-skip-permissions`は提供しない」)。`acceptEdits`はEdit/Write系ツールの
  確認のみを自動化するモードであり、全ツール・全操作を無条件で許可するものではない

### 実際のgit push/リモートへの書き込みについて

このフェーズは、GitLabへの実際の反映(pushやMR作成)を一切行わない。以下の3層で担保する
(詳細は`docs/operations/security.md`を参照):

1. **Workspace Manager(`workspace/`)**: `git clone`/`git fetch`/`git worktree`/
   `git reset --hard`のみを実装しており、`git push`はどこにも実装していない
   (`grep -n "push" src/gitlab_ai_platform/workspace/git_workspace.py`は何もヒットしない)
2. **本フェーズのJobHandler(`build_implement_handler`)**: `GitLabWriter`のメソッドのうち
   `create_branch`(branch作成)のみを呼び出す。`push_file_changes`(Commits API経由の
   ファイル変更コミット)は一切呼び出さない(M4-9のスコープ)
3. **Claude Code Runner経由の実行**: 上記の`disallowed_tools`(`Bash(git push:*)`)に加え、
   自動実行系用のGitLab PAT(`docs/operations/security.md` §4.1)は`read_api`スコープかつ
   アカウントロールがReporterのため、仮にBash経由で`git push`相当の操作が試みられても
   GitLab側で拒否される(このPATにはこのフェーズのworktreeでの認証設定は注入されておらず
   ─ `GitWorkspaceManager`の`git_config`はWorkspace Manager自身のgit呼び出しにのみ
   `-c`引数として渡され、`.git/config`には永続化されない ─ 仮に認証情報が使えたとしても
   ロール・スコープの両方で書き込みが拒否される二重の防御になっている)

### GitLab上に実装用branchを作成し、そのbranch名でworktreeを用意する

処理の流れ:

1. `GitLabReader.get_default_branch(project)`(ADR-0032)でdefault branchを解決する
2. `GitLabWriter.create_branch(project, f"ai/issue-{issue_iid}", default_branch)`
   (許可リストの書き込み操作)で、default branchを起点にGitLab上へ実装用branchを作成する。
   branch名は`ai/issue-<issue_iid>`という規則にした(人間の`feature/<issue番号>-<slug>`
   規則、`CLAUDE.md`と紛れないよう`ai/`prefixを付けた。対象プロジェクトは本リポジトリとは
   別の任意のGitLabプロジェクトのため、本リポジトリ固有の規則を強制する必要はないが、
   由来がAIであることが一目でわかる名前にした)
3. `Workspace Manager.prepare_for_issue(project, issue_iid, branch_name)`(ADR-0031)で、
   その実装用branchのworktreeを用意する(ローカルbranch名は`issue-<issue_iid>`。GitLab上の
   branch名`ai/issue-<issue_iid>`とは別の名前空間で、必ずしも一致させる必要はない)
4. Job再試行(下記「テスト失敗時の扱い」)で同じbranch名を再度作成しようとした場合、
   GitLab APIは既存branch名に400を返す。これを検知した場合は「既に存在するbranchを
   そのまま使う」と判断して続行する(`_ensure_remote_branch`)

### テストが通らない場合の扱い: `ImplementationNotCommittedError`を送出し、既存のJob再試行機構に乗せる

Claude Codeの実行後、worktreeの実際の状態(`implement/git_ops.read_head_sha`→
`read_worktree_state`が返す`WorktreeState.head_sha`/`is_clean`)を実行前と比較する。

- **HEAD commit shaが実行前後で変化していない**、または**作業ツリーに未commitの差分が
  残っている**場合、`ImplementationNotCommittedError`を送出する
- この判定はClaude Codeの自己申告(`ImplementResult.tests_passed`)を唯一の根拠にしない。
  [ADR-0005](0005-claude-code-runner-design.md)が確立した「`result`(自然文/自己申告)
  だけで成否判定しない」という方針をここでも踏襲し、worktreeの実際のgit状態という
  構造的な事実で確認する
- `ImplementationNotCommittedError`は`RunnerDispatcher._process`の`except Exception`経路
  (`cli/dispatcher.py`)でそのまま捕捉され、`fail(..., retry=True)`となる。これは
  `JobRepository`(ADR-0017)が既に持つJobの再試行/デッドレター機構
  (`max_attempts`、既定3回)にそのまま乗る

この設計を選んだ理由:

- **シンプルな設計から始める**(Issue本文の指示)。専用のリトライループやバックオフ処理を
  このフェーズのために新設せず、既存のJob Queueインフラ(ADR-0017)を再利用するのが
  最小の実装コストで済む
- テストの失敗は「Claude Codeがもう一度試せば直る可能性がある」性質の失敗であることが多い
  (依存関係のインストール漏れ、一時的なタイムアウト、単純な実装ミス等)。要求分析・設計・
  実装計画フェーズが使う`WAITING_HUMAN`(人間の判断が本質的に必要な不明点)とは性質が異なり、
  即座に人間を呼ぶ必要はないと判断した
- 一方、実装内容そのものに関する不明点(`ImplementResult.uncertainties`)は、`design`/`plan`
  と同じ`judge_uncertainties`/`WaitingForHumanError`の枠組みをそのまま使う。「commitできたが
  判断に迷う点がある」場合は`WAITING_HUMAN`、「commitできなかった」場合は`FAILED`→retry、
  という2つの独立した失敗モードを区別した
- `max_attempts`(既定3回)を使い切って最終的にデッドレター化した場合、`list_dead_letters`/
  HTTP API(M3-7)経由で人間が気づける。専用の通知機構は本Issueのスコープ外

### 実装成功時、`discard_for_issue`は呼ばない

`build_implement_handler`は処理の最後で`Workspace Manager.discard_for_issue`を呼ばない
(ADR-0031「決定」で説明した通り)。理由: このフェーズはローカルcommitまでで完結し
(下記「明確な非目標」)、そのcommitをM4-9(push フェーズ)が後で参照してpushする必要がある。
worktreeを破棄すると、ローカルのみに存在するcommitオブジェクトが実質失われてしまう
(ローカルbranchのrefを削除するため、到達不能になりいずれGCされうる)。worktreeの後片付けは
M4-9がpush完了後に行う、または専用の運用上のGCとして別途検討する(ADR-0031「今後の課題」)。

### 明確な非目標

- **実際の`git push`をどこにも実装しない**。Workspace Manager・Runner・本フェーズの
  JobHandlerのいずれにも、リモートへの実際のpushを行うコードは一切追加していない
- **`push_file_changes`を呼ばない**。`GitLabWriter`の許可リストのうち、本フェーズが呼ぶのは
  `create_branch`のみ
- **MRの作成もこのフェーズでは行わない**(M4-9のスコープ)

## 却下した選択肢

- **`ClaudeCodeRunner`に実装フェーズ専用の新規メソッドを追加する**: 「決定」節に記載の通り、
  `run_prompt`は既に「組み立て済みプロンプト+任意のworktree_path」という汎用契約であり、
  実際のworktreeを渡すだけで実装フェーズの要件を満たせる。ADR-0027が既に「`run_prompt`に
  worktree_pathとして実際のworktreeを渡す形を再検討する余地がある」と予告していた通りの
  結論になった
- **`permission_mode="bypassPermissions"`(`--dangerously-skip-permissions`相当)を使う**:
  既存方針(ADR-0005)に反するため却下。`acceptEdits`で十分に運用可能と判断した
- **テストが通らない場合を`WAITING_HUMAN`にする**: 「決定」節に記載の通り、テスト失敗は
  人間の判断を要さず再試行で解決しうる性質の失敗であることが多く、即座に人間を呼ぶと
  無人実行の利点が薄れる。既存のJob再試行機構で十分と判断した
- **テストが通らない場合、専用のリトライ回数上限・バックオフを新設する**: 「決定」節に
  記載の通り、`JobRepository`が既に持つ`max_attempts`(ADR-0017)で十分であり、
  このフェーズのためだけに重複した仕組みを作る理由が無い
- **実装成功時に`discard_for_issue`を呼び、後片付けを徹底する**: 「決定」節に記載の通り、
  ローカルcommitがM4-9のpushフェーズに必要なため、成功時にworktreeを破棄すると
  実装成果そのものを失う危険がある
- **branch名を`feature/issue-<iid>`のように本リポジトリの規則(`CLAUDE.md`)に合わせる**:
  対象プロジェクトは本リポジトリではなく任意の社内GitLabプロジェクトであり、本リポジトリ
  固有の規則を強制する理由が無い。AIが作成したbranchであることが分かる`ai/`prefixを
  独自に採用した

## 影響

- `src/gitlab_ai_platform/implement/`(新規)に`types.py`(`ImplementInput`/`ImplementResult`)・
  `prompts.py`(`build_implement_instructions`)・`parser.py`(`parse_implement_output`)・
  `errors.py`(`ImplementError`/`ImplementOutputParseError`/`ImplementationNotCommittedError`)・
  `git_ops.py`(`read_worktree_state`)・`job.py`
  (`build_implement_job_payload`/`implement_job_payload_to_args`/`build_implement_job_result`/
  `build_resolved_implement_job_result`)を追加
- `src/gitlab_ai_platform/cli/dispatcher.py`に`build_implement_handler`を追加し、
  `build_job_handlers`が`JobType.IMPLEMENT`に対応付ける。`adapter`引数の型を`GitLabReader`
  から`GitLabAdapter`(読み取り+書き込み)へ広げた(`create_branch`が書き込み操作のため)
- `src/gitlab_ai_platform/cli/respond.py`の`_RESULT_RESOLVERS`に`JobType.IMPLEMENT`を追加
- `docs/operations/security.md`を更新し、本フェーズで初めてClaude Codeに`Edit`/`Write`/`Bash`
  を許可したことと、その多層防御構成を反映した
- M4-9(push フェーズ)は、本フェーズが`Job.result`に残す`commit_sha`/`remote_branch`/
  `local_branch`/`worktree_path`を入力として、`push_file_changes`経由の実際のpushとMR作成を
  行う想定(本Issueのスコープ外)
