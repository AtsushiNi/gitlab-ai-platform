# 実装フェーズ(implement)

- 実装場所: `src/gitlab_ai_platform/implement/`(Job種別への配線は`cli/dispatcher.py`、
  `WAITING_HUMAN`後の再開は`cli/respond.py`)
- 対応Issue: [#114](https://github.com/AtsushiNi/gitlab-ai-platform/issues/114) (M4-8)
- 関連ADR: [ADR-0026](../adr/0026-job-waiting-human-transition.md)(`WAITING_HUMAN`遷移の設計)、
  ADR-0028(`WAITING_HUMAN`後の回答取り込み)、
  ADR-0030(実装計画フェーズ、本フェーズの入力元)、
  ADR-0031(Workspace ManagerのIssue単位worktree対応)、
  ADR-0032(GitLab Adapterのdefault branch取得)、
  ADR-0033(本フェーズのRunner実行方式・権限設計・
  テスト失敗時の扱い)
- ステータス: 実装済み

## 責務

実装計画フェーズ(M4-7、`plan`)が確定させたタスク一覧を元に、実際にファイルを編集し、
テストを実行し、変更をローカルにcommitするJobフェーズ。Job種別`implement`
(`job/protocol.py`の`JobType.IMPLEMENT`)として、`RunnerDispatcher`(M3-3、
`cli/dispatcher.py`)の`JobHandler`(`build_implement_handler`)で処理する。

**無人実行トラック限定の機能。** 対話型トラック(VS Code拡張 + GitLab Adapter MCP Server、
M2-12)では、人間とClaude Codeが対話しながら直接実装を進めるため、このフェーズ自体は
使われない。このフェーズはIssueに無人実行ラベルが付いた場合(M4-1、Issue Poller)にのみ
実行される。

`issue-analysis`/`design`/`plan`と異なり、このフェーズは無人実行パイプラインで初めて
実際のworktree(`Workspace Manager.prepare_for_issue`、ADR-0031)を使い、Claude Codeに
実際のファイル編集・シェルコマンド実行の権限(`Edit`/`Write`/`Bash`)を与える(ADR-0033)。

## 前提と非対象

- 前提:
  - 処理対象のJobは、実装計画フェーズ(M4-7)完了時の`Job.result`
    (`plan.build_plan_job_result`/`build_resolved_plan_job_result`が組み立てたもの)を元に
    `implement.build_implement_job_payload`で組み立てて`JobRepository.enqueue`されたもの。
    投入者(「plan完了 → implement投入」の橋渡し)自体はM4-10(Issue→MRパイプラインの
    オーケストレーション)のスコープで、本パッケージには含まない
  - Issue本体の取得は`GitLabReader.get_issue`(M2-10)で実行時に行う(`design`/`plan`と
    同じ設計)。実装プロンプトの主要な入力は実装計画フェーズの結果(`ImplementInput`)であり、
    Issue本文は見出し情報として追記される
  - 対象プロジェクトのdefault branchは`GitLabReader.get_default_branch`(ADR-0032)で
    実行時に解決する
  - 「不足情報」の判定(`ASK`で止めるか`ASSUME`で継続するか)は本パッケージの対象外。
    `ImplementResult.uncertainties`を`orchestrator.judge_uncertainties`(M4-4、ADR-0024)に
    渡した結果を呼び出し側(`build_implement_handler`)が使う(`design`/`plan`と同じパターン)
- 非対象:
  - **実際のGitLabへのpush**(`GitLabWriter.push_file_changes`の呼び出し)。本フェーズは
    ローカルworktree内でのgit commitまでで完結する。GitLabへの実際の反映はM4-9(push と
    MR 作成、未実装)の責務(ADR-0033「明確な非目標」)
  - **MRの作成**。M4-9のスコープ
  - **`WAITING_HUMAN`への実際の状態遷移**(`JobRepository.wait_for_human`の呼び出し)・
    `WAITING_HUMAN`からの再開(`update_status`呼び出し)。本パッケージは`Job.result`の構造
    (`build_implement_job_result`/`build_resolved_implement_job_result`)を組み立てるところ
    までで、実際の状態遷移は`cli/dispatcher.py`の`RunnerDispatcher._process`(ADR-0026)・
    `cli/respond.py`の`respond_to_job`(ADR-0028)が担う
  - **テスト失敗時の専用リトライ機構**。テストが最終的に通らない/commitされなかった場合は
    `ImplementationNotCommittedError`を送出し、`JobRepository`が既に持つJob再試行/
    デッドレター機構(ADR-0017)にそのまま乗せる。本フェーズ専用のリトライループや
    バックオフは実装しない(ADR-0033「決定」)
  - **worktreeの後片付け(GC)**。実装成功時、本フェーズは`Workspace Manager.discard_for_issue`
    を呼ばない(ローカルcommitをM4-9が参照できるようにするため)。worktreeの破棄は
    M4-9または別途のGCの仕組みが担う(ADR-0031「今後の課題」)
  - 「plan完了 → implement投入」「implement完了 → push投入」の橋渡し(Job間の連携)。
    `implement.build_implement_job_payload`という組み立て関数は用意するが、実際にいつ・
    誰が呼ぶかはM4-10のスコープ

## 公開インターフェース

実装場所: `src/gitlab_ai_platform/implement/`(`prompts.py` / `parser.py` / `types.py` /
`errors.py` / `git_ops.py` / `job.py`)。`src/gitlab_ai_platform/implement/__init__.py`から
再エクスポート。

```python
def build_implement_instructions(implement_input: ImplementInput) -> str:
    """実装用のinstructions文字列を返す。`implement_input`をテキストに埋め込む決定的な処理。"""


def parse_implement_output(run_result: RunResult) -> ImplementResult:
    """`run_result`から結果スキーマを抽出する。`plan.parser.parse_plan_output`
    と同じ設計方針(```jsonフェンスの抽出・検証、is_errorの確認)。"""


def read_worktree_state(
    worktree_path: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> WorktreeState:
    """worktreeの現在のHEAD commit shaと、作業ツリーが汚れていないかを返す。
    Claude Code実行の前後で呼び、実際にローカルcommitが行われたかを構造的に確認するために使う。"""


def build_implement_job_payload(
    project: str, issue_iid: int, plan_result: Mapping[str, Any]
) -> dict[str, Any]:
    """実装計画フェーズ完了時の`Job.result`から`implement`種別Jobのpayloadを組み立てる。"""


def implement_job_payload_to_args(
    payload: Mapping[str, Any],
) -> tuple[str, int, ImplementInput]:
    """`implement`種別Jobのpayloadから`(project, issue_iid, ImplementInput)`を取り出す。"""


def build_implement_job_result(
    project: str,
    issue_iid: int,
    *,
    implement_result: ImplementResult,
    commit_sha: str,
    remote_branch: str,
    local_branch: str,
    worktree_path: str,
    judgments: Sequence[UncertaintyJudgment],
) -> dict[str, Any]:
    """実装フェーズのJob resultを組み立てる(`complete`/`wait_for_human`共通)。"""


def build_resolved_implement_job_result(
    result: Mapping[str, Any], answers: Sequence[str]
) -> dict[str, Any]:
    """`WAITING_HUMAN`の`result`に人間の回答を統合した新しいresultを組み立てる。
    `cli/respond.py`の`respond_to_job`が呼び出す。"""
```

`JobHandler`本体(実際にRunnerを呼び出しJobとして処理する部分)は
`src/gitlab_ai_platform/cli/dispatcher.py`の`build_implement_handler`:

```python
def build_implement_handler(
    adapter: GitLabAdapter,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    config: Config,
    *,
    read_worktree_state: Callable[[Path], WorktreeState] = read_worktree_state,
) -> JobHandler:
    """implement種別の`JobHandler`を組み立てる。"""
```

`adapter`は`GitLabReader`(`get_issue`/`get_default_branch`)と`GitLabWriter`
(`create_branch`のみ)の両方を必要とするため、`design`/`plan`の`GitLabReader`単体とは異なり
`GitLabAdapter`型を受け取る(ADR-0033)。`build_job_handlers`(`cli/dispatcher.py`)が
`JobType.IMPLEMENT`に対応付けてディスパッチテーブルへ登録する。

## 入出力スキーマ

### `ImplementInput`(`implement/types.py`)

実装フェーズの入力。実装計画フェーズ完了時の`Job.result`から組み立てる。

| フィールド | 型 | 補足 |
|---|---|---|
| `plan_document` | `str` | 実装計画フェーズが確定させた実装計画文書(Markdown) |
| `tasks` | `tuple[PlanTask, ...]` | 実装順のタスク一覧(`plan.types.PlanTask`をそのまま再利用) |
| `assumed_uncertainties` | `tuple[str, ...]` | 各フェーズのASSUME判定で確定した前提を`"question → assumption"`形式に整形したもの |

### `ImplementResult`(`implement/types.py`)

Claude Codeの応答をパースした、1回の実装実行の結果。

| フィールド | 型 | 補足 |
|---|---|---|
| `summary` | `str` | 実装内容の要約(コミットメッセージ・MR説明の下書き用) |
| `commit_message` | `str \| None` | Claude Codeが実際にcommitした際のメッセージ。commitしなかった場合は`None` |
| `tests_passed` | `bool` | テストが通ったかどうかのClaude Code自身の自己申告。**唯一の判断根拠にはしない**(下記参照) |
| `uncertainties` | `tuple[Uncertainty, ...]` | 実装にあたって生じた不足情報。各要素の`phase`は`"implement"`固定(`orchestrator.types.Uncertainty`) |

### `WorktreeState`(`implement/git_ops.py`)

worktreeのある時点での状態。

| フィールド | 型 | 補足 |
|---|---|---|
| `head_sha` | `str` | `git rev-parse HEAD`の結果 |
| `is_clean` | `bool` | `git status --porcelain`が空(=未commitの差分が無い)かどうか |

`build_implement_handler`はClaude Code実行の前後でこの状態を比較し、`head_sha`が変化して
いない、または実行後に`is_clean`が`False`(未commitの差分が残っている)場合、
`ImplementResult.tests_passed`の自己申告に関わらず`ImplementationNotCommittedError`を
送出する(ADR-0005が確立した「自己申告だけで
成否判定しない」という方針をここでも踏襲、ADR-0033)。

### Claude Codeへの出力指示(`build_implement_instructions`が指示するJSONスキーマ)

`plan/prompts.py`と同じ設計パターン。応答の末尾に ```json フェンスで1つだけ、次の
スキーマのオブジェクトを出力させる:

| フィールド | 型 | 補足 |
|---|---|---|
| `summary` | `string` | 実装内容・テスト結果の要約 |
| `commit_message` | `string \| null` | 実際にcommitした場合のメッセージ、しなかった場合は`null` |
| `tests_passed` | `boolean` | 最終的にテストが通ったかどうか |
| `open_questions` | `object[]` | 不足情報の一覧。無ければ空配列。各要素: `question: string`, `severity: "critical" \| "minor"`, `assumption: string \| null`(`severity`が`"minor"`の場合は必須) |

`parse_implement_output`(`implement/parser.py`)は`open_questions`の各要素を
`orchestrator.types.Uncertainty(question=..., severity=..., assumption=..., phase="implement")`に
変換する。`summary`が空でない文字列でない場合、`commit_message`が文字列でもnullでもない場合、
`tests_passed`が真偽値でない場合、`open_questions`が配列でない場合、`severity="minor"`なのに
`assumption`が空/欠落の場合等に`ImplementOutputParseError`を送出する。

### Job payload/result(`implement`種別、`job/protocol.py`の`Job.payload`/`Job.result`)

payload(組み立ては`implement.build_implement_job_payload`、分解は`implement_job_payload_to_args`):

| フィールド | 型 | 補足 |
|---|---|---|
| `payload.project` | `str` | 対象プロジェクトパス |
| `payload.issue_iid` | `int` | 対象IssueのIID |
| `payload.plan_document` | `string` | 実装計画フェーズの`result.plan_document`を転記 |
| `payload.tasks` | `object[]` | 実装計画フェーズの`result.tasks`を転記(`{"title", "description"}`の配列) |
| `payload.assumed_uncertainties` | `object[]` | 実装計画フェーズの`result.assumed_uncertainties`を転記 |

result(`build_implement_job_result`が組み立てる。`complete`・`wait_for_human`のどちらでも
同じ構造を使う、issue-analysis/design/planと同じ方針のADR-0026を踏襲):

| フィールド | 型 | 補足 |
|---|---|---|
| `result.project` | `str` | 対象プロジェクトパス |
| `result.issue_iid` | `int` | 対象IssueのIID |
| `result.summary` | `string` | `ImplementResult.summary`をそのまま転記 |
| `result.commit_message` | `string \| null` | `ImplementResult.commit_message`をそのまま転記(Claude Codeの自己申告) |
| `result.commit_sha` | `string` | 実行後に確認したworktreeの実際のHEAD commit sha(`WorktreeState.head_sha`、構造的に確認済みの値) |
| `result.remote_branch` | `string` | GitLab上に作成した実装用branch名(`ai/issue-<issue_iid>`)。M4-9のpush対象 |
| `result.local_branch` | `string` | worktreeのローカルbranch名(`issue-<issue_iid>`、ADR-0031) |
| `result.worktree_path` | `string` | worktreeの絶対パス(デバッグ・追跡用。workerホストのローカルパスのため、他ホストからは参照できない点に注意) |
| `result.assumed_uncertainties` | `object[]` | `orchestrator.assume_judgments`が返す`ASSUME`判定の不明点 |
| `result.questions` | `object[]` | `orchestrator.ask_judgments`が返す`ASK`判定の不明点。**`WAITING_HUMAN`のときのみ非空**になる |
| `result.resolved_questions` | `object[]` | `respond`が`questions`への回答を統合した後にのみ存在するフィールド |

`WAITING_HUMAN`への遷移そのもの(`JobRepository.wait_for_human`の呼び出し)は
`cli/dispatcher.py`の`RunnerDispatcher._process`が担う。`WAITING_HUMAN`後の再開
(`update_status`呼び出し・回答統合)は`cli/respond.py`の`respond_to_job`が担う
(`_RESULT_RESOLVERS`辞書で`job_type`ごとに`build_resolved_implement_job_result`を選択する)。

## Claude Codeへの権限付与(ADR-0033)

`build_implement_handler`が`ClaudeCodeRunner.run_prompt`に渡す権限設定(モジュール定数、
`cli/dispatcher.py`):

| 設定 | 値 | 補足 |
|---|---|---|
| `allowed_tools` | `("Edit", "Write", "Bash")` | このリポジトリで初めて実際のファイル編集・シェルコマンド実行を許可する |
| `disallowed_tools` | `("Bash(git push:*)",)` | `git push`を明示的に禁止する多層防御の1つ(GitLab Adapterの構造的な保証とは強さが異なる、`docs/operations/security.md`参照) |
| `permission_mode` | `"acceptEdits"` | headless実行のためEdit/Write系ツールの確認を自動承認する。`--dangerously-skip-permissions`相当の全許可モード(`"bypassPermissions"`)は使わない |

`worktree_path`には`Workspace Manager.prepare_for_issue`(ADR-0031)が返す実際のworktreeの
パスを渡す(`issue-analysis`/`design`/`plan`が一時ディレクトリを渡していたのと異なる)。

## 処理の流れ

1. `implement_job_payload_to_args`でpayloadを分解(`project`/`issue_iid`/`ImplementInput`)
2. `GitLabReader.get_issue`でIssue取得、`GitLabReader.get_default_branch`でdefault branchを
   解決(ADR-0032)
3. `GitLabWriter.create_branch(project, f"ai/issue-{issue_iid}", default_branch)`で実装用
   branchをGitLab上に作成する(既に存在する場合はGitLab APIの400を検知して続行する、
   retry時の冪等性)
4. `Workspace Manager.prepare_for_issue(project, issue_iid, remote_branch)`(ADR-0031)で
   worktreeを用意する
5. `read_worktree_state(worktree.path)`で実行前の状態を記録する
6. `build_implement_instructions`と`build_issue_prompt`でプロンプトを組み立て、
   `ClaudeCodeRunner.run_prompt`で実際のworktree上でClaude Codeを実行する
7. `parse_implement_output`で応答を構造化し、`read_worktree_state`で実行後の状態を
   再度確認する
8. HEAD commit shaが変化していない、または作業ツリーが汚れている場合は
   `ImplementationNotCommittedError`を送出する(下記「エラー時の振る舞い」)
9. 検出した不明点を`judge_uncertainties`で判定し、`ASK`判定が1件でもあれば
   `WaitingForHumanError`を送出、無ければ`build_implement_job_result`の結果を返す

実装成功時、`Workspace Manager.discard_for_issue`は呼ばない(ADR-0031/0033)。

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/implement/errors.py`。

- `ImplementError(Exception)` — 実装フェーズ経由の処理が失敗したことを表す基底例外
- `ImplementOutputParseError(ImplementError)` — Claude Codeの応答から結果スキーマを
  抽出できなかったことを表す。`raw_text`に元の`result_text`を保持する
  (`plan.errors.PlanOutputParseError`と同じ設計)
- `ImplementationNotCommittedError(ImplementError)` — 実行後もworktreeのHEAD commit shaが
  変化しなかった(または作業ツリーが汚れたままだった)ことを表す。`ImplementResult`の自己申告
  だけでなく、worktreeの実際のgit状態を構造的に確認した結果として送出する(ADR-0033)

`build_implement_handler`(`cli/dispatcher.py`)内で送出された例外は`RunnerDispatcher._process`
(ADR-0022)が捕捉する: `ImplementOutputParseError`/`ImplementationNotCommittedError`を含む
それ以外の例外は`fail(..., retry=True)`となり、`JobRepository`が既に持つJob再試行/
デッドレター機構(ADR-0017、既定`max_attempts=3`)にそのまま乗る。**テストが通らない場合・
commitされなかった場合の扱いはこの経路(retry→最終的にデッドレター化)であり、専用の
リトライ機構は実装しない**(ADR-0033「決定」)。実装フェーズ自身は`WaitingForHumanError`
(`cli/dispatcher.py`、ADR-0026)を意図的に送出することで`WAITING_HUMAN`への遷移を要求する
(`fail`とは異なる正常系の分岐。commitは成功したが実装内容に関する不明点がある場合)。

## テスト方針

実装場所: `tests/gitlab_ai_platform/implement/`・`tests/gitlab_ai_platform/cli/test_dispatcher.py`・
`tests/gitlab_ai_platform/cli/test_respond.py`・`tests/gitlab_ai_platform/workspace/`・
`tests/gitlab_ai_platform/gitlab_adapter/`(`src/`をミラー、ADR-0001)。`unittest.mock`は
使わず手書きフェイクを使う(CLAUDE.mdのテスト方針)。実際のgit/subprocess/GitLab APIには
繋がない。

- `implement/test_types.py`: `ImplementInput`/`ImplementResult`が`frozen=True`であることを検証
- `implement/test_errors.py`: 各例外の継承関係・保持する属性を検証
- `implement/test_git_ops.py`: `read_worktree_state`が実際の一時gitリポジトリ(`tmp_path`)に
  対して正しい`head_sha`/`is_clean`を返すこと(未commitの変更・untrackedファイルの両方で
  `is_clean=False`になること)、新規commit後に`head_sha`が変化すること、gitが失敗した場合に
  `ImplementError`を送出すること、`run`引数を差し替えられることを検証する
- `implement/test_prompts.py`: `build_implement_instructions`が決定的であること、
  `plan_document`/`tasks`/`assumed_uncertainties`の内容を含むこと、タスクが空でも例外を
  送出せず既定文言になること、「無人実行トラック専用」の説明を含むこと、`git commit`を
  許可しつつ`git push`を明示的に禁止する記述を含むこと、JSONスキーマのキーワード
  (`summary`/`commit_message`/`tests_passed`/`open_questions`等)を含むことを検証する
- `implement/test_parser.py`: `plan/parser.py`の`test_parser.py`と同じ観点(フェンス抽出、
  末尾優先、複数ブロック時の扱い、`is_error`時の即エラー、`permission_denials`時は警告のみで
  継続)に加え、`summary`/`commit_message`/`tests_passed`の型検証、`open_questions`の
  `severity`/`assumption`検証を行う
- `implement/test_job.py`: `build_implement_job_payload`/`implement_job_payload_to_args`が
  実装計画フェーズの`result`から必要なフィールドを過不足なく往復できること、
  `build_implement_job_result`が`commit_sha`/`remote_branch`等の実装フェーズ固有フィールドを
  含むこと、`assume_judgments`/`ask_judgments`の結果を正しく変換すること、
  `build_resolved_implement_job_result`が`questions`を`resolved_questions`へ変換し
  他フィールドを保持したまま統合することを検証する
- `workspace/test_git_workspace.py`: `prepare_for_issue`/`discard_for_issue`が
  `mr-<iid>`/`issue-<iid>`という別々の名前空間を使うこと、**MRとIssueのIIDが同じ数値でも
  worktree・ローカルbranchが衝突しないこと**(ADR-0031の回帰テスト)、GCが現時点で
  issue用worktreeを対象にしないことを検証する
- `gitlab_adapter/test_rest.py`: `get_default_branch`がプロジェクト情報APIから
  `default_branch`フィールドを取り出すこと、フィールド欠落時に`GitLabApiError`を送出することを
  検証する
- `cli/test_dispatcher.py`: `build_implement_handler`が
  default branch解決→branch作成→worktree用意→Runner実行→commit確認→判定という流れで
  結果辞書を組み立てること、HEAD shaが変化しない場合・作業ツリーが汚れたままの場合に
  `ImplementationNotCommittedError`を送出すること、`ASK`判定が1件でもあると
  `WaitingForHumanError`を送出すること(commitは既に成功している前提で結果に残ること)、
  branch作成時のGitLab APIの400(既存branch)を許容し他のエラーは再送出すること、
  `allowed_tools`/`disallowed_tools`/`permission_mode`が期待通り渡ること、実装成功時に
  `discard_for_issue`を呼ばないことを検証する。`build_job_handlers`が`JobType.IMPLEMENT`を
  登録することも検証する
- `cli/test_respond.py`: `implement`種別の`WAITING_HUMAN`Jobに対して`respond_to_job`/
  `run_respond`が`build_resolved_implement_job_result`を使って正しく`DONE`まで遷移することを
  検証する

## 関連ドキュメント

- [architecture.md](../architecture.md) 「Orchestrator」の行(M4-1〜M4-6, M4-9〜M4-10)
- [ADR-0026: Job Queue経由での`WAITING_HUMAN`遷移の設計](../adr/0026-job-waiting-human-transition.md)
- [specs/plan-phase.md](plan-phase.md) — 実装計画フェーズ(M4-7)の仕様。
  `payload.plan_document`/`payload.tasks`等の転記元
- [specs/workspace-manager.md](workspace-manager.md) — `prepare_for_issue`/`discard_for_issue`
  (ADR-0031)
- [specs/gitlab-adapter.md](gitlab-adapter.md) — `get_default_branch`(ADR-0032)、
  `create_branch`(既存の許可リスト操作)
- [specs/claude-code-runner.md](claude-code-runner.md) — `run_prompt`(M4-3、ADR-0027)。
  本フェーズは`worktree_path`に実際のworktreeを渡す最初の利用者
- [specs/orchestrator.md](orchestrator.md) — `judge_uncertainties`/`requires_human`/
  `ask_judgments`/`assume_judgments`(M4-4)
- [specs/job-model.md](job-model.md) — `JobType.IMPLEMENT`、`wait_for_human`(ADR-0026)
- [specs/cli.md](cli.md) — `respond`サブコマンド(`implement`種別対応)
- [operations/security.md](../operations/security.md) — Claude Code Runnerへの権限付与、
  git push禁止の多層防御構成
- ソースコード: `src/gitlab_ai_platform/implement/`(`prompts.py` / `parser.py` / `types.py` /
  `errors.py` / `git_ops.py` / `job.py` / `__init__.py`)、
  `src/gitlab_ai_platform/cli/dispatcher.py`(`build_implement_handler`)、
  `src/gitlab_ai_platform/cli/respond.py`(`_RESULT_RESOLVERS`)
