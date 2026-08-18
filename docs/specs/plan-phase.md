# 実装計画フェーズ(plan)

- 実装場所: `src/gitlab_ai_platform/plan/`(Job種別への配線は`cli/dispatcher.py`、
  `WAITING_HUMAN`後の再開は`cli/respond.py`)
- 対応Issue: [#113](https://github.com/AtsushiNi/gitlab-ai-platform/issues/113) (M4-7)
- 関連ADR: [ADR-0026](../adr/0026-job-waiting-human-transition.md)(`WAITING_HUMAN`遷移の設計)、
  [ADR-0027](../adr/0027-issue-analysis-runner-execution.md)(要求分析フェーズがworktreeを
  使わない設計、実装計画フェーズも同じ方針を踏襲)、
  [ADR-0028](../adr/0028-waiting-human-answer-integration.md)(`WAITING_HUMAN`後の回答取り込み)、
  [ADR-0029](../adr/0029-design-phase.md)(設計フェーズの出力先・Runner実行方式、実装計画
  フェーズも同じ方針を踏襲)、
  [ADR-0030](../adr/0030-implementation-plan-phase.md)(実装計画フェーズのJob種別設計)
- ステータス: 実装済み

## 責務

設計フェーズ(M4-6、`design`)が確定させた設計内容を元に、実装可能な粒度のタスクへ分解し、
実装順に並べた計画として出力するJobフェーズ。Job種別`plan`(`job/protocol.py`の`JobType.PLAN`)
として、`RunnerDispatcher`(M3-3、`cli/dispatcher.py`)の`JobHandler`(`build_plan_handler`)で
処理する。

**無人実行トラック限定の機能。** 対話型トラック(VS Code拡張 + GitLab Adapter MCP Server、
M2-12)では、人間とClaude Codeが対話しながら実装を進める過程でタスクの分解が自然に行われる
ため、独立した実装計画フェーズは不要(`plan/__init__.py`のdocstring、ADR-0030)。このフェーズは
Issueに無人実行ラベルが付いた場合(M4-1、Issue Poller)にのみ実行される。

## 前提と非対象

- 前提:
  - 処理対象のJobは、設計フェーズ(M4-6)完了時の`Job.result`(`design.build_design_job_result`/
    `build_resolved_design_job_result`が組み立てたもの)を元に`plan.build_plan_job_payload`で
    組み立てて`JobRepository.enqueue`されたもの。投入者(「design完了 → plan投入」の橋渡し)
    自体はM4-10(Issue→MRパイプラインのオーケストレーション)のスコープで、本パッケージには
    含まない
  - Issue本体の取得は`GitLabReader.get_issue`(M2-10)で実行時に行う(`design`と同じ設計)。
    実装計画プロンプトの主要な入力は設計フェーズの結果(`PlanInput`)であり、Issue本文は
    見出し情報として追記される
  - 「不足情報」の判定(`ASK`で止めるか`ASSUME`で継続するか)は本パッケージの対象外。
    `PlanResult.uncertainties`を`orchestrator.judge_uncertainties`(M4-4、ADR-0024)に渡した
    結果を呼び出し側(`build_plan_handler`)が使う(`design`/`issue-analysis`と同じパターン)
- 非対象:
  - リポジトリの探索(既存実装の確認等)。実装計画フェーズはWorkspace Manager(worktree)を
    使わず、設計フェーズの結果とIssue本文のみを入力とする(ADR-0030「決定」。理由は
    ADR-0029が設計フェーズについて述べたものと同じ)
  - 対象プロジェクトへの実際のコミット(`GitLabAdapter`の`create_branch`/`push_file_changes`)。
    実装計画フェーズは`plan_document`/`tasks`を`Job.result`に構造化データとして持たせる
    ところまでで、実際のコミットは行わない(ADR-0030「決定」)
  - タスクの実際の実装。タスク一覧を生成するところまでが本フェーズの責務で、各タスクを
    元にbranch作成・実装・テスト実行・commitを行うのはM4-8(実装フェーズ、Job種別
    `implement`、未実装)の責務
  - `WAITING_HUMAN`への実際の状態遷移(`JobRepository.wait_for_human`の呼び出し)・
    `WAITING_HUMAN`からの再開(`update_status`呼び出し)。本パッケージは`Job.result`の構造
    (`build_plan_job_result`/`build_resolved_plan_job_result`)を組み立てるところまでで、
    実際の状態遷移は`cli/dispatcher.py`の`RunnerDispatcher._process`(ADR-0026)・
    `cli/respond.py`の`respond_to_job`(ADR-0028、M4-7で`plan`種別に対応拡張)が担う
  - 「design完了 → plan投入」「plan完了 → implement投入」の橋渡し(Job間の連携)。
    `plan.build_plan_job_payload`という組み立て関数は用意するが、実際にいつ・誰が呼ぶかは
    M4-10のスコープ

## 公開インターフェース

実装場所: `src/gitlab_ai_platform/plan/`(`prompts.py` / `parser.py` / `types.py` /
`errors.py` / `job.py`)。`src/gitlab_ai_platform/plan/__init__.py`から再エクスポート。

```python
def build_plan_instructions(plan_input: PlanInput) -> str:
    """実装計画用のinstructions文字列を返す。`plan_input`をテキストに埋め込む決定的な処理。"""


def parse_plan_output(run_result: RunResult) -> PlanResult:
    """`run_result`から結果スキーマを抽出する。`design.parser.parse_design_output`
    と同じ設計方針(```jsonフェンスの抽出・検証、is_errorの確認)。"""


def build_plan_job_payload(
    project: str, issue_iid: int, design_result: Mapping[str, Any]
) -> dict[str, Any]:
    """設計フェーズ完了時の`Job.result`から`plan`種別Jobのpayloadを組み立てる。"""


def plan_job_payload_to_args(
    payload: Mapping[str, Any],
) -> tuple[str, int, PlanInput]:
    """`plan`種別Jobのpayloadから`(project, issue_iid, PlanInput)`を取り出す。"""


def build_plan_job_result(
    project: str,
    issue_iid: int,
    plan: PlanResult,
    judgments: Sequence[UncertaintyJudgment],
) -> dict[str, Any]:
    """実装計画フェーズのJob resultを組み立てる(`complete`/`wait_for_human`共通)。"""


def build_resolved_plan_job_result(
    result: Mapping[str, Any], answers: Sequence[str]
) -> dict[str, Any]:
    """`WAITING_HUMAN`の`result`に人間の回答を統合した新しいresultを組み立てる(M4-7)。
    `cli/respond.py`の`respond_to_job`が呼び出す。"""
```

`JobHandler`本体(実際にRunnerを呼び出しJobとして処理する部分)は
`src/gitlab_ai_platform/cli/dispatcher.py`の`build_plan_handler`:

```python
def build_plan_handler(
    adapter: GitLabReader,
    runner: ClaudeCodeRunner,
    config: Config,
) -> JobHandler:
    """plan種別の`JobHandler`を組み立てる。"""
```

`build_job_handlers`(`cli/dispatcher.py`)が`JobType.PLAN`に対応付けてディスパッチテーブルへ
登録する。

## 入出力スキーマ

### `PlanInput`(`plan/types.py`)

実装計画フェーズの入力。設計フェーズ完了時の`Job.result`から組み立てる。

| フィールド | 型 | 補足 |
|---|---|---|
| `design_document` | `str` | 設計フェーズが確定させた設計内容(Markdown) |
| `assumed_uncertainties` | `tuple[str, ...]` | 設計フェーズのASSUME判定(および`respond`でのASK→回答解決)で確定した前提を`"question → assumption"`形式の文字列に整形したもの |

### `PlanTask`(`plan/types.py`)

実装可能な粒度に分解された1件のタスク。

| フィールド | 型 | 補足 |
|---|---|---|
| `title` | `str` | タスクの短い見出し |
| `description` | `str` | タスクの内容(何を実装するか、完了の目安) |

### `PlanResult`(`plan/types.py`)

Claude Codeの応答をパースした、1回の実装計画生成の結果。

| フィールド | 型 | 補足 |
|---|---|---|
| `plan_document` | `str` | 実装計画全体をまとめたMarkdown文書(タスク一覧・分解方針を含む) |
| `tasks` | `tuple[PlanTask, ...]` | 実装可能な粒度に分解したタスクの一覧。タプルの並び順がそのまま実装順序を表す(依存関係を表す専用フィールドは持たない) |
| `uncertainties` | `tuple[Uncertainty, ...]` | タスク分解にあたって生じた不足情報(不明点)。各要素の`phase`は`"plan"`固定(`orchestrator.types.Uncertainty`、ADR-0024) |

### Claude Codeへの出力指示(`build_plan_instructions`が指示するJSONスキーマ)

`design/prompts.py`と同じ設計パターン。応答の末尾に ```json フェンスで1つだけ、次の
スキーマのオブジェクトを出力させる:

| フィールド | 型 | 補足 |
|---|---|---|
| `plan_document` | `string` | 実装計画文書全文(Markdown)。概要・タスク一覧・前提と非対象の構成に従う |
| `tasks` | `object[]` | 実装順に並べたタスクの一覧。1件以上必須。各要素: `title: string`, `description: string` |
| `open_questions` | `object[]` | 不足情報の一覧。無ければ空配列。各要素: `question: string`, `severity: "critical" \| "minor"`, `assumption: string \| null`(`severity`が`"minor"`の場合は必須) |

`parse_plan_output`(`plan/parser.py`)は`tasks`の各要素を`types.PlanTask`に、`open_questions`の
各要素を`orchestrator.types.Uncertainty(question=..., severity=..., assumption=..., phase="plan")`に
変換する。`tasks`が空配列、または各要素の`title`/`description`が空でない文字列でない場合、
`severity="minor"`かつ`assumption`が空/欠落の場合は`PlanOutputParseError`を送出する
(`design/parser.py`と同じ理由)。

### Job payload/result(`plan`種別、`job/protocol.py`の`Job.payload`/`Job.result`)

payload(組み立ては`plan.build_plan_job_payload`、分解は`plan_job_payload_to_args`):

| フィールド | 型 | 補足 |
|---|---|---|
| `payload.project` | `str` | 対象プロジェクトパス |
| `payload.issue_iid` | `int` | 対象IssueのIID |
| `payload.design_document` | `string` | 設計フェーズの`result.design_document`を転記 |
| `payload.assumed_uncertainties` | `object[]` | 設計フェーズの`result.assumed_uncertainties`を転記(`{"question", "severity", "assumption"}`の配列) |

result(`build_plan_job_result`が組み立てる。`complete`・`wait_for_human`のどちらでも同じ
構造を使う、issue-analysis/designと同じ方針のADR-0026を踏襲):

| フィールド | 型 | 補足 |
|---|---|---|
| `result.project` | `str` | 対象プロジェクトパス |
| `result.issue_iid` | `int` | 対象IssueのIID |
| `result.plan_document` | `string` | `PlanResult.plan_document`をそのまま転記 |
| `result.tasks` | `object[]` | `PlanResult.tasks`を転記(`{"title", "description"}`の配列、実装順) |
| `result.assumed_uncertainties` | `object[]` | `orchestrator.assume_judgments`が返す`ASSUME`判定の不明点。各要素: `question`, `severity`(`"minor"`固定), `assumption` |
| `result.questions` | `object[]` | `orchestrator.ask_judgments`が返す`ASK`判定の不明点。各要素: `question`, `severity`(`"critical"`固定)。**`WAITING_HUMAN`のときのみ非空**になる |
| `result.resolved_questions` | `object[]` | `respond`が`questions`への回答を統合した後にのみ存在するフィールド。各要素: `question`, `severity`, `answer` |

`WAITING_HUMAN`への遷移そのもの(`JobRepository.wait_for_human`の呼び出し)は
`cli/dispatcher.py`の`RunnerDispatcher._process`が担う。`WAITING_HUMAN`後の再開
(`update_status`呼び出し・回答統合)は`cli/respond.py`の`respond_to_job`が担う
(`_RESULT_RESOLVERS`辞書で`job_type`ごとに`build_resolved_plan_job_result`を選択する)。

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/plan/errors.py`。

- `PlanError(Exception)` — 実装計画フェーズ経由の処理が失敗したことを表す基底例外
- `PlanOutputParseError(PlanError)` — Claude Codeの応答から結果スキーマを抽出できなかった
  ことを表す。`raw_text`に元の`result_text`を保持する(`design.errors.DesignOutputParseError`と
  同じ設計)。`run_result.is_error`が`True`の場合、`plan_document`が空でない文字列でない場合、
  `tasks`が1件以上を含む配列でない場合、`tasks`の各要素の`title`/`description`が空でない
  文字列でない場合、`open_questions`が配列でない場合、`severity`が`critical`/`minor`以外の
  場合、`severity="minor"`なのに`assumption`が欠落している場合等に送出する

`build_plan_handler`(`cli/dispatcher.py`)内で送出された例外は`RunnerDispatcher._process`
(ADR-0022)が捕捉する: `PlanOutputParseError`を含むそれ以外の例外は`fail(..., retry=True)`
(1件のJobの失敗は他のJobの処理を止めない)。実装計画フェーズ自身は`WaitingForHumanError`
(`cli/dispatcher.py`、ADR-0026)を意図的に送出することで`WAITING_HUMAN`への遷移を要求する
(`fail`とは異なる正常系の分岐)。

## テスト方針

実装場所: `tests/gitlab_ai_platform/plan/`・`tests/gitlab_ai_platform/cli/test_dispatcher.py`・
`tests/gitlab_ai_platform/cli/test_respond.py`(`src/`をミラー、ADR-0001)。`unittest.mock`は
使わず手書きフェイクを使う(CLAUDE.mdのテスト方針)。

- `test_types.py`: `PlanInput`/`PlanTask`/`PlanResult`が`frozen=True`であることを検証する
- `test_errors.py`: `PlanOutputParseError`が`PlanError`のサブクラスで`raw_text`を保持する
  ことを検証する
- `test_prompts.py`: `build_plan_instructions`が同じ`PlanInput`に対して決定的であること、
  `design_document`/`assumed_uncertainties`の内容が出力文字列に含まれること、
  `assumed_uncertainties`が空でも例外を送出せず既定文言になること、「無人実行トラック専用」
  「リポジトリを参照できません」の説明を含むこと、JSONスキーマのキーワード
  (`plan_document`/`tasks`/`open_questions`等)を含むことを検証する
- `test_parser.py`: `design/parser.py`の`test_parser.py`と同じ観点(フェンス抽出、末尾優先、
  複数ブロック時の扱い、`is_error`時の即エラー、`permission_denials`時は警告のみで継続)に加え、
  `plan_document`の型検証・`tasks`の非空検証・各`task`の`title`/`description`の型検証・
  `severity`の妥当性検証・`severity="minor"`時の`assumption`必須チェックを検証する
- `test_job.py`: `build_plan_job_payload`/`plan_job_payload_to_args`が設計フェーズの`result`から
  必要なフィールドを過不足なく往復できること、`assumed_uncertainties`が
  `"question → assumption"`形式に整形されること、`build_plan_job_result`が
  `assume_judgments`/`ask_judgments`の結果を正しく変換すること、複数タスクが実装順のまま
  `result.tasks`に反映されること、`build_resolved_plan_job_result`が`questions`を
  `resolved_questions`へ変換し`assumed_uncertainties`へ合流させること、`answers`の件数が
  `questions`と一致しない場合に`ValueError`を送出することを検証する
- `cli/test_dispatcher.py`: `build_plan_handler`が`GitLabReader.get_issue`→`IssueContext`→
  プロンプト組み立て→`ClaudeCodeRunner.run_prompt`→パース→判定という流れで結果辞書を
  組み立てること、`ASK`判定が1件でもあると`WaitingForHumanError`を送出すること、`MINOR`のみ
  (`ASSUME`判定)の場合は通常通り結果を返すことを検証する。`build_job_handlers`が
  `JobType.PLAN`を登録することも検証する
- `cli/test_respond.py`: `plan`種別の`WAITING_HUMAN`Jobに対して`respond_to_job`/`run_respond`
  が`build_resolved_plan_job_result`を使って正しく`DONE`まで遷移すること、`plan`以外の
  未対応種別(`implement`)を指定すると引き続き`InvalidJobTransitionError`を送出することを
  検証する

## 関連ドキュメント

- [architecture.md](../architecture.md) 「Orchestrator」の行(M4-1〜M4-6, M4-9〜M4-10)
- [ADR-0026: Job Queue経由での`WAITING_HUMAN`遷移の設計](../adr/0026-job-waiting-human-transition.md)
- [ADR-0027: 要求分析フェーズのRunner実行方式](../adr/0027-issue-analysis-runner-execution.md)
- [ADR-0028: `WAITING_HUMAN`後の回答取り込み・Job完了の設計](../adr/0028-waiting-human-answer-integration.md)
- [ADR-0029: 設計フェーズの出力先とRunner実行方式の設計](../adr/0029-design-phase.md)
- [ADR-0030: 実装計画フェーズのJob種別設計](../adr/0030-implementation-plan-phase.md)
- [specs/design-phase.md](design-phase.md) — 設計フェーズ(M4-6)の仕様。
  `payload.design_document`等の転記元、同じ設計パターンの先行実装
- [specs/claude-code-runner.md](claude-code-runner.md) — `IssueContext`/`build_issue_prompt`
  (M4-2)、`run_prompt`(M4-3、ADR-0027)
- [specs/orchestrator.md](orchestrator.md) — `judge_uncertainties`/`requires_human`/
  `ask_judgments`/`assume_judgments`(M4-4)
- [specs/job-model.md](job-model.md) — `JobType.PLAN`、`wait_for_human`(ADR-0026、ADR-0030)
- [specs/cli.md](cli.md) — `respond`サブコマンド(`plan`種別対応、M4-7)
- ソースコード: `src/gitlab_ai_platform/plan/`(`prompts.py` / `parser.py` / `types.py` /
  `errors.py` / `job.py` / `__init__.py`)、
  `src/gitlab_ai_platform/cli/dispatcher.py`(`build_plan_handler`)、
  `src/gitlab_ai_platform/cli/respond.py`(`_RESULT_RESOLVERS`)
