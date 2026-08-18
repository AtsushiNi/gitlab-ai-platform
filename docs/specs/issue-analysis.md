# 要求分析フェーズ(issue-analysis)

- 実装場所: `src/gitlab_ai_platform/issue_analysis/`(Job種別への配線は`cli/dispatcher.py`)
- 対応Issue: [#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109) (M4-3)
- 関連ADR: [ADR-0026](../adr/0026-job-waiting-human-transition.md)(`WAITING_HUMAN`遷移の設計)、
  [ADR-0027](../adr/0027-issue-analysis-runner-execution.md)(Runner実行方式・worktreeを
  使わない設計)
- ステータス: 実装済み

## 責務

無人実行ラベルの付いたIssue(M4-1、Issue Poller)を分析し、要求・受入条件・前提・不足情報
(不明点)を構造化して出力するJobフェーズ。Job種別`issue-analysis`
(`job/protocol.py`の`JobType.ISSUE_ANALYSIS`)として、`RunnerDispatcher`(M3-3、
`cli/dispatcher.py`)の`JobHandler`(`build_issue_analysis_handler`)で処理する。「無人実行に
向くタスクかどうか」の判定はこのフェーズに持たせない(M4-1のラベル付与で完了済みの前提、
Issue #109本文)。

## 前提と非対象

- 前提:
  - 処理対象のJobは`IssuePoller`(M4-1、`poller/issue_poller.py`)が
    `build_issue_analysis_job_payload`で組み立てて`JobRepository.enqueue`済みのもの。
    payloadの組み立て・分解は`poller/issue_poller.py`の
    `build_issue_analysis_job_payload`/`issue_analysis_job_payload_to_args`を再利用する
    (本パッケージでは重複実装しない)
  - Issue本体の取得は`GitLabReader.get_issue`(M2-10)で実行時に行う(Poller検出時点の
    スナップショットは使わない、`poller/issue_poller.py`の設計をそのまま踏襲)
  - 「不足情報」の判定(`ASK`で止めるか`ASSUME`で継続するか)は本パッケージの対象外。
    `RequirementAnalysis.uncertainties`を`orchestrator.judge_uncertainties`(M4-4、ADR-0024)
    に渡した結果を呼び出し側(`build_issue_analysis_handler`)が使う
- 非対象:
  - リポジトリの探索(diffの参照、既存実装の確認等)。要求分析はIssue本文(タイトル・説明・
    ラベル)の読解のみを対象とし、Workspace Manager(worktree)を使わない(ADR-0027)
  - `WAITING_HUMAN`への実際の状態遷移(`JobRepository.wait_for_human`の呼び出し)。
    本パッケージ(`issue_analysis/`)は`Job.result`の構造(`build_issue_analysis_job_result`)を
    組み立てるところまでで、実際の状態遷移は`cli/dispatcher.py`の
    `RunnerDispatcher._process`が担う(ADR-0026)
  - 人間への質問の実際の提示・回答の取り込み(M4-5、[#111](https://github.com/AtsushiNi/gitlab-ai-platform/issues/111)のスコープ)

## 公開インターフェース

実装場所: `src/gitlab_ai_platform/issue_analysis/`(`prompts.py` / `parser.py` / `types.py` /
`errors.py` / `job.py`)。`src/gitlab_ai_platform/issue_analysis/__init__.py`から再エクスポート。

```python
def build_issue_analysis_instructions() -> str:
    """要求分析用のinstructions文字列を返す。引数を取らない純粋関数。"""


def parse_issue_analysis_output(run_result: RunResult) -> RequirementAnalysis:
    """`run_result`から結果スキーマを抽出する。`review.parser.parse_review_output`と同じ
    設計方針(```jsonフェンスの抽出・検証、is_errorの確認)。"""


def build_issue_analysis_job_result(
    project: str,
    issue_iid: int,
    analysis: RequirementAnalysis,
    judgments: Sequence[UncertaintyJudgment],
) -> dict[str, Any]:
    """要求分析フェーズのJob resultを組み立てる(`complete`/`wait_for_human`共通)。"""
```

`JobHandler`本体(実際にRunnerを呼び出しJobとして処理する部分)は
`src/gitlab_ai_platform/cli/dispatcher.py`の`build_issue_analysis_handler`:

```python
def build_issue_analysis_handler(
    adapter: GitLabReader,
    runner: ClaudeCodeRunner,
    config: Config,
) -> JobHandler:
    """issue-analysis種別の`JobHandler`を組み立てる。"""
```

`build_job_handlers`(`cli/dispatcher.py`)が`JobType.ISSUE_ANALYSIS`に対応付けて
ディスパッチテーブルへ登録する。

## 入出力スキーマ

### `RequirementAnalysis`(`issue_analysis/types.py`)

Claude Codeの応答をパースした、1回の要求分析の結果。

| フィールド | 型 | 補足 |
|---|---|---|
| `requirements` | `tuple[str, ...]` | Issueから読み取った要求事項 |
| `acceptance_criteria` | `tuple[str, ...]` | 受入条件 |
| `assumptions` | `tuple[str, ...]` | Claude Codeが分析にあたって置いた前提(Issue本文に明記されていない解釈等) |
| `uncertainties` | `tuple[Uncertainty, ...]` | 不足情報(不明点)。各要素の`phase`は`"issue-analysis"`固定(`orchestrator.types.Uncertainty`、ADR-0024) |

### Claude Codeへの出力指示(`build_issue_analysis_instructions`が指示するJSONスキーマ)

`review/prompts.md`の「出力」セクションと同じ設計パターン。応答の末尾に ```json フェンスで
1つだけ、次のスキーマのオブジェクトを出力させる:

| フィールド | 型 | 補足 |
|---|---|---|
| `requirements` | `string[]` | 要求の一覧。無ければ空配列 |
| `acceptance_criteria` | `string[]` | 受入条件の一覧。無ければ空配列 |
| `assumptions` | `string[]` | 前提の一覧。無ければ空配列 |
| `open_questions` | `object[]` | 不足情報の一覧。無ければ空配列。各要素: `question: string`, `severity: "critical" \| "minor"`, `assumption: string \| null`(`severity`が`"minor"`の場合は必須) |

`parse_issue_analysis_output`(`issue_analysis/parser.py`)は`open_questions`の各要素を
`orchestrator.types.Uncertainty(question=..., severity=..., assumption=..., phase="issue-analysis")`
に変換する。`severity="minor"`かつ`assumption`が空/欠落の場合は、プロンプトの指示違反として
`IssueAnalysisOutputParseError`を送出する(`orchestrator.judge_uncertainty`が同条件で送出する
`MissingAssumptionError`より早期に検知する)。

### Job payload/result(`issue-analysis`種別、`job/protocol.py`の`Job.payload`/`Job.result`)

payload(組み立て・分解は`poller/issue_poller.py`、本パッケージでは扱わない):

| フィールド | 型 | 補足 |
|---|---|---|
| `payload.project` | `str` | 対象プロジェクトパス |
| `payload.issue_iid` | `int` | 対象IssueのIID |

result(`build_issue_analysis_job_result`が組み立てる。`complete`・`wait_for_human`の
どちらでも同じ構造を使う、[ADR-0026](../adr/0026-job-waiting-human-transition.md)):

| フィールド | 型 | 補足 |
|---|---|---|
| `result.project` | `str` | 対象プロジェクトパス |
| `result.issue_iid` | `int` | 対象IssueのIID |
| `result.requirements` | `string[]` | `RequirementAnalysis.requirements`をそのまま転記 |
| `result.acceptance_criteria` | `string[]` | `RequirementAnalysis.acceptance_criteria`をそのまま転記 |
| `result.assumptions` | `string[]` | `RequirementAnalysis.assumptions`をそのまま転記(Claude Codeが分析時点で述べた前提) |
| `result.assumed_uncertainties` | `object[]` | `orchestrator.assume_judgments`が返す`ASSUME`判定の不明点。各要素: `question`, `severity`(`"minor"`固定), `assumption`。**`result.assumptions`とは別物**: 元は不明点だったが仮定を置いて処理を継続することにした項目を表す(M4-9でMR本文の「○○と仮定して実装した」という記述の元として使う想定) |
| `result.questions` | `object[]` | `orchestrator.ask_judgments`が返す`ASK`判定の不明点。各要素: `question`, `severity`(`"critical"`固定)。**`WAITING_HUMAN`のときのみ非空**になる(`requires_human`が`False`の場合のみ`complete`を呼ぶため、`complete`の`result.questions`は必ず空配列) |

`WAITING_HUMAN`への遷移そのもの(`JobRepository.wait_for_human`の呼び出し)は
`cli/dispatcher.py`の`RunnerDispatcher._process`が担う。詳細は
[job-model.md](job-model.md)の`wait_for_human`の節、[ADR-0026](../adr/0026-job-waiting-human-transition.md)を参照。

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/issue_analysis/errors.py`。

- `IssueAnalysisError(Exception)` — 要求分析フェーズ経由の処理が失敗したことを表す基底例外
- `IssueAnalysisOutputParseError(IssueAnalysisError)` — Claude Codeの応答から結果スキーマを
  抽出できなかったことを表す。`raw_text`に元の`result_text`を保持する
  (`review.errors.ReviewOutputParseError`と同じ設計)。`run_result.is_error`が`True`の場合、
  `open_questions`が配列でない場合、`severity`が`critical`/`minor`以外の場合、
  `severity="minor"`なのに`assumption`が欠落している場合等に送出する

`build_issue_analysis_handler`(`cli/dispatcher.py`)内で送出された例外は`RunnerDispatcher._process`
(ADR-0022)が捕捉する: `IssueAnalysisOutputParseError`を含むそれ以外の例外は
`fail(..., retry=True)`(1件のJobの失敗は他のJobの処理を止めない)。要求分析フェーズ自身は
`WaitingForHumanError`(`cli/dispatcher.py`、ADR-0026)を意図的に送出することで
`WAITING_HUMAN`への遷移を要求する(`fail`とは異なる正常系の分岐)。

## テスト方針

実装場所: `tests/gitlab_ai_platform/issue_analysis/`・`tests/gitlab_ai_platform/cli/test_dispatcher.py`
(`src/`をミラー、[ADR-0001](../adr/0001-repository-structure.md))。`unittest.mock`は使わず
手書きフェイクを使う(CLAUDE.mdのテスト方針)。

- `test_types.py`: `RequirementAnalysis`が`frozen=True`であることを検証する
- `test_errors.py`: `IssueAnalysisOutputParseError`が`IssueAnalysisError`のサブクラスで
  `raw_text`を保持することを検証する
- `test_prompts.py`: `build_issue_analysis_instructions`が決定的であること、「要求」「受入条件」
  「前提」「不足情報」の各要素・`critical`/`minor`の重要度指示・JSONスキーマのキーワード
  (`requirements`/`acceptance_criteria`/`assumptions`/`open_questions`等)を含むこと、
  `runner.build_issue_prompt`と組み合わせてもIssue情報が重複なく1回だけ現れることを検証する
- `test_parser.py`: `review/parser.py`の`test_parser.py`と同じ観点(フェンス抽出、末尾優先、
  複数ブロック時の扱い、`is_error`時の即エラー、`permission_denials`時は警告のみで継続)に
  加え、`severity`の妥当性検証・`severity="minor"`時の`assumption`必須チェックを検証する
- `test_job.py`: `build_issue_analysis_job_result`が`requirements`/`acceptance_criteria`/
  `assumptions`をそのまま転記すること、`assume_judgments`/`ask_judgments`の結果が
  `assumed_uncertainties`/`questions`にそれぞれ正しく変換されることを検証する
- `cli/test_dispatcher.py`: `build_issue_analysis_handler`が`GitLabReader.get_issue`→
  `IssueContext`→プロンプト組み立て→`ClaudeCodeRunner.run_prompt`→パース→判定という
  流れで結果辞書を組み立てること、`ASK`判定が1件でもあると`WaitingForHumanError`を送出する
  こと、`MINOR`のみ(`ASSUME`判定)の場合は通常通り結果を返すことを検証する。
  `RunnerDispatcher._process`が`WaitingForHumanError`を捕捉すると`fail`/`complete`ではなく
  `wait_for_human`を呼ぶことを検証する(ADR-0026)

## 関連ドキュメント

- [architecture.md](../architecture.md) 「Orchestrator」の行(M4-1〜M4-6, M4-9〜M4-10)
- [ADR-0026: Job Queue経由での`WAITING_HUMAN`遷移の設計](../adr/0026-job-waiting-human-transition.md)
- [ADR-0027: 要求分析フェーズのRunner実行方式](../adr/0027-issue-analysis-runner-execution.md)
- [specs/issue-poller.md](issue-poller.md) — payloadの組み立て・分解元(M4-1)
- [specs/claude-code-runner.md](claude-code-runner.md) — `IssueContext`/`build_issue_prompt`
  (M4-2)、`run_prompt`(M4-3、ADR-0027)
- [specs/orchestrator.md](orchestrator.md) — `judge_uncertainties`/`requires_human`/
  `ask_judgments`/`assume_judgments`(M4-4)
- [specs/job-model.md](job-model.md) — `JobType.ISSUE_ANALYSIS`、`wait_for_human`(M4-3、ADR-0026)
- [specs/prompts.md](prompts.md) / [specs/review-output.md](review-output.md) — 同じ設計パターン
  (プロンプトの「出力」セクション+```jsonフェンスの抽出・検証)の先行実装(`review`)
- ソースコード: `src/gitlab_ai_platform/issue_analysis/`(`prompts.py` / `parser.py` / `types.py` /
  `errors.py` / `job.py` / `__init__.py`)、
  `src/gitlab_ai_platform/cli/dispatcher.py`(`build_issue_analysis_handler`、`WaitingForHumanError`)
