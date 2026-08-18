# Orchestrator

- 実装場所: `src/gitlab_ai_platform/orchestrator/`
- 対応Issue: [#110](https://github.com/AtsushiNi/gitlab-ai-platform/issues/110) (M4-4)、
  [#116](https://github.com/AtsushiNi/gitlab-ai-platform/issues/116) (M4-10)
- 関連ADR: [ADR-0024](../adr/0024-ask-or-assume-judgment.md)、
  [ADR-0035](../adr/0035-pipeline-orchestration.md)
- ステータス: 実装中(「質問する / 仮定して進める」判断ロジック(M4-4)、フェーズ間の連鎖
  (M4-10)を実装済み。HTTP API/サーバ層による外部連携は未実装)

## 責務

無人実行パイプライン(Issue → 要求分析 → 設計 → 実装計画 → 実装 → push/MR作成、
`docs/architecture.md`の「Orchestrator」)のうち、本パッケージがカバーするのは以下の2つ:

- (M4-4、`judgment.py`)要求分析・設計・実装計画・実装の各フェーズが検出した不明点について、
  処理を止めて人間に確認すべきか、仮定を明示して継続してよいかを判定する
- (M4-10、`pipeline.py`)`issue-analysis → design → plan → implement → push`という5フェーズの
  連鎖(あるフェーズのJobが完了したら、その結果から次フェーズのJobを組み立てて投入する)を担う

HTTP API/サーバ層による外部連携は未実装。

## 前提と非対象

- 前提:
  - 処理対象のIssueは、M4-1(ラベルポーリング)で人間が事前に無人実行可能と判断してラベルを
    付けたものに限られる。そのため`WAITING_HUMAN`への遷移(=`ASK`判定)の発生頻度は低い想定
    (`references/タスク整理.md` M4-4、ADR-0024)
  - 呼び出し側(M4-3要求分析・M4-6設計・M4-8実装の各フェーズ)は、不明点を検出した時点で
    `Uncertainty`を組み立てて本モジュールへ渡す。不明点の**検出**自体(Issue本文やdiffを解析して
    「ここが曖昧」と判断する処理)は呼び出し側の責務であり、本モジュールの対象外
  - `Uncertainty.severity`(重要度)は呼び出し側が明示する。本モジュールはキーワード解析等による
    重要度の自動判定は行わない(ADR-0024「却下した選択肢」)
- 非対象:
  - `judgment.py`について: Jobの`update_status`呼び出しによる実際の状態遷移
    (`RUNNING → WAITING_HUMAN`等)。「遷移すべきか」の判定(`requires_human`)のみを提供し、
    `job/protocol.py`の`JobRepository`には依存しない(ADR-0024)
  - 人間への質問の実際の提示先(ローカル通知 or Issueコメント)と、回答を受けての`WAITING_HUMAN`
    からの再開処理そのもの。これはM4-5(`cli/respond.py`)のスコープ。`pipeline.py`は
    `respond_to_job`が`DONE`へ遷移させた**後**にフックとして呼ばれるだけで、回答の取り込み
    自体には関与しない
  - MR本文への「○○と仮定して実装した」という記述の実際の組み立て・整形。本モジュールは
    `UncertaintyJudgment.assumption_note`として仮定の文言を返すところまでで、MR本文への反映は
    M4-9(pushとMR作成、`push/mr_template.py`)のスコープ
  - 重要度のキーワードベース自動判定・LLMによる重要度推定。将来必要になれば、`Uncertainty`を
    生成する呼び出し側(または呼び出し側とこのモジュールの間の新しい層)に追加する
  - `pipeline.py`について: 各フェーズのpayload/result組み立てロジックそのもの(`design.job`等、
    各フェーズパッケージの責務)。`advance_pipeline`はそれらを正しい順序で呼び出すだけで、
    新しい変換ロジックは持たない(ADR-0035)
  - Issue単位の進捗を横断的に追跡する専用索引。既存のJob `payload`/`result`の`project`/
    `issue_iid`と`JobRepository`の一覧機能で足りると判断した(ADR-0035「論点3」)

## 公開インターフェース

実装場所: `src/gitlab_ai_platform/orchestrator/judgment.py`(型は`types.py`、例外は`errors.py`)。

```python
from collections.abc import Sequence
from dataclasses import dataclass, field

from gitlab_ai_platform.orchestrator.types import (
    JudgmentAction,
    Uncertainty,
    UncertaintyJudgment,
    UncertaintySeverity,
)


@dataclass(frozen=True)
class JudgmentPolicy:
    """重要度→アクションの対応付け(判定基準そのもの)。呼び出し側が差し替え可能。"""

    ask_severities: frozenset[UncertaintySeverity] = field(
        default_factory=lambda: frozenset({UncertaintySeverity.CRITICAL})
    )


DEFAULT_POLICY: JudgmentPolicy  # ask_severities={CRITICAL} の既定ポリシー


def judge_uncertainty(
    uncertainty: Uncertainty, policy: JudgmentPolicy = DEFAULT_POLICY
) -> UncertaintyJudgment:
    """1件の`Uncertainty`を判定する。

    `ASSUME`判定になるのに`uncertainty.assumption`が`None`の場合は
    `MissingAssumptionError`を送出する。
    """


def judge_uncertainties(
    uncertainties: Sequence[Uncertainty], policy: JudgmentPolicy = DEFAULT_POLICY
) -> list[UncertaintyJudgment]:
    """複数の`Uncertainty`をまとめて判定する。順序は入力の順序を維持する。"""


def requires_human(judgments: Sequence[UncertaintyJudgment]) -> bool:
    """1件でも`ASK`判定があれば`True`。呼び出し側がJobを`WAITING_HUMAN`へ
    遷移させるべきかどうかの判断に使う。"""


def ask_judgments(
    judgments: Sequence[UncertaintyJudgment],
) -> list[UncertaintyJudgment]:
    """`ASK`判定のみを抽出する(人間へ提示する質問一覧の元、M4-5で使用予定)。"""


def assume_judgments(
    judgments: Sequence[UncertaintyJudgment],
) -> list[UncertaintyJudgment]:
    """`ASSUME`判定のみを抽出する(MRに残す仮定一覧の元、M4-9で使用予定)。"""
```

`pipeline.py`(M4-10, ADR-0035)。**`orchestrator/__init__.py`からは意図的に再エクスポートしない**
(`design`/`plan`/`implement`/`push`各パッケージへの依存と、それらパッケージの`orchestrator`本体
への依存が循環importになりうるため、ADR-0035「論点4」)。呼び出し側は
`from gitlab_ai_platform.orchestrator.pipeline import advance_pipeline_hook`と
サブモジュールを明示的にimportする。

```python
from collections.abc import Callable

from gitlab_ai_platform.job.protocol import Job, JobRepository


def advance_pipeline(job_repo: JobRepository, completed_job: Job) -> Job | None:
    """完了したJobを受け取り、パイプラインの次フェーズのJobを投入する。

    `completed_job.status`が`DONE`でない場合、次フェーズが無い場合(`review`/`push`)、
    次フェーズのpayload組み立てに必要なフィールドが`completed_job.result`に無い場合、
    次フェーズJobの`enqueue`が失敗した場合はいずれも例外を送出せず`None`を返す。
    """


def advance_pipeline_hook(job_repo: JobRepository) -> Callable[[Job], None]:
    """`job_repo`を束縛した`advance_pipeline`を、`RunnerDispatcher`/`respond_to_job`の
    `on_job_completed: Callable[[Job], None]`契約に合わせて返す。
    """
```

呼び出し元(`cli/dispatcher.py`の`RunnerDispatcher._process`、`cli/respond.py`の
`respond_to_job`)は、いずれもJobを`DONE`へ遷移させた**後**にのみ`advance_pipeline_hook`が
返すフックを呼ぶ。フェーズ対応表(`issue-analysis→design→plan→implement→push`)は
`pipeline.py`内の非公開の`_NEXT_JOB_TYPE`が持ち、呼び出し元には一切公開しない
(`RunnerDispatcher`/`respond_to_job`がフェーズ順序を知らずに済む、ADR-0022/ADR-0035)。

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/orchestrator/types.py`。

| 型 | フィールド | 補足 |
|---|---|---|
| `UncertaintySeverity` (Enum) | `CRITICAL` / `MINOR` | `CRITICAL`=重要な不明点、`MINOR`=軽微な疑問。呼び出し側が`Uncertainty`作成時に明示する |
| `JudgmentAction` (Enum) | `ASK` / `ASSUME` | `judge_uncertainty`/`judge_uncertainties`が返す判定結果 |
| `Uncertainty` (frozen dataclass) | `question: str`, `severity: UncertaintySeverity`, `assumption: str \| None = None`, `phase: str \| None = None` | `question`は人間へ提示する質問文相当。`assumption`は`MINOR`でASSUME判定にする場合に必須(無いと`MissingAssumptionError`)。`phase`は不明点が生じたフェーズ名(例: `"issue-analysis"`)の任意情報で、判定ロジック自体には使わない |
| `UncertaintyJudgment` (frozen dataclass) | `uncertainty: Uncertainty`, `action: JudgmentAction`, `assumption_note: str \| None = None` | `assumption_note`は`action=ASSUME`の場合のみ値を持ち、`uncertainty.assumption`の転記 |
| `JudgmentPolicy` (frozen dataclass) | `ask_severities: frozenset[UncertaintySeverity]` | この集合に含まれる重要度は`ASK`、含まれない重要度は`ASSUME`と判定する。既定値は`{CRITICAL}` |

`pipeline.py`は新しい型を持たない。`_NEXT_JOB_TYPE: dict[JobType, JobType]`(非公開)が
`issue-analysis → design → plan → implement → push`の対応を表す。

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/orchestrator/errors.py`(`judgment.py`用)。

- `OrchestratorError(Exception)` — Orchestratorパッケージ経由の操作が失敗したことを表す基底例外
- `MissingAssumptionError(OrchestratorError)` — `ASSUME`判定になるはずの`Uncertainty`に
  `assumption`が設定されていないことを表す。呼び出し側(要求分析等の各フェーズ)が、軽微な疑問を
  検出した時点で仮定の文言も一緒に用意すべきだったことを意味し、通常は呼び出し側のバグとして
  扱う(リトライで解決しない)

`pipeline.py`の`advance_pipeline`/`advance_pipeline_hook`は独自の例外を持たない。以下のいずれも
**例外を送出せず`None`を返す**(ADR-0035「論点2」):

- `completed_job.status`が`JobStatus.DONE`でない場合(`WAITING_HUMAN`・`FAILED`を連鎖させない
  境界。ただし呼び出し元はJobを`DONE`にした直後にのみ呼ぶ設計のため、通常この分岐には来ない)
- `completed_job.job_type`に次フェーズが無い場合(`review`/`push`)
- `completed_job.result`に次フェーズのpayload組み立てに必要なフィールドが無い場合
  (`project`/`issue_iid`が無い、または`push`投入に必要な`commit_sha`等が無い、`KeyError`を変換)
- 次フェーズJobの`enqueue`が`JobError`で失敗した場合(ログにのみ記録する)

## テスト方針

実装場所: `tests/gitlab_ai_platform/orchestrator/`(`src/`をミラー、ADR-0001)。`unittest.mock`は
使わず、`judgment.py`は外部依存を持たない純粋関数のみのためフェイクも不要。`pipeline.py`は
`JobRepository.enqueue`のみを満たす手書きフェイク(`test_pipeline.py`の`_FakeJobRepository`)を
使う。

- `test_types.py`: `UncertaintySeverity`/`JudgmentAction`の値、`Uncertainty`/`UncertaintyJudgment`
  が`frozen=True`であること、`Uncertainty`の`assumption`/`phase`が省略可能であることを検証する
- `test_judgment.py`:
  - `CRITICAL`な`Uncertainty`が`judge_uncertainty`で`ASK`判定になること
  - `assumption`付きの`MINOR`な`Uncertainty`が`ASSUME`判定になり、`assumption_note`に
    `assumption`がそのまま転記されること
  - `assumption`無しの`MINOR`な`Uncertainty`が`MissingAssumptionError`を送出すること
  - `judge_uncertainties`が入力の順序を維持すること
  - `requires_human`が、`ASK`判定を1件でも含む場合`True`、全て`ASSUME`または空リストの場合
    `False`を返すこと
  - `ask_judgments`/`assume_judgments`が判定結果を正しくフィルタすること
  - カスタムの`JudgmentPolicy`(例: `MINOR`も`ASK`対象にする、`CRITICAL`を`ASK`対象から外す)を
    渡すと判定基準が差し替わることを検証する(ADR-0024「判定基準は差し替え可能」の回帰テスト)
- `test_errors.py`: `MissingAssumptionError`が`OrchestratorError`のサブクラスであることを検証する
- `test_pipeline.py`:
  - `completed_job.status`が`DONE`以外(`PENDING`/`RUNNING`/`WAITING_HUMAN`/`FAILED`)の場合、
    `advance_pipeline`が`None`を返し`enqueue`を一切呼ばないこと
  - `review`/`push`種別(次フェーズが無い)の場合に`None`を返すこと
  - `issue-analysis`/`design`/`plan`/`implement`それぞれの完了Jobから、正しい次フェーズ
    (`design`/`plan`/`implement`/`push`)のJobが正しいpayloadで`enqueue`されること。
    `implement → push`のみ`completed_job.payload`/`result`の両方を使うこと(ADR-0034「論点2」)
  - `result`に`project`/`issue_iid`が無い場合、`push`投入に必要なフィールドが無い場合に
    `None`を返し`enqueue`しないこと
  - `enqueue`が`JobError`を送出した場合に`None`を返すこと(例外を伝播させない)
  - `advance_pipeline_hook`が`Job | None`ではなく`None`を返す(戻り値を握りつぶす)こと

`cli/dispatcher.py`(`test_dispatcher.py`)・`cli/respond.py`(`test_respond.py`)側でも、
`on_job_completed`フックが「`complete`/`DONE`遷移成功後にのみ呼ばれる」「`WAITING_HUMAN`/`fail`
経路では呼ばれない」「フック自体が例外を送出しても呼び出し元の処理結果に影響しない」ことを
検証する。`test_dispatcher.py`の`test_run_dispatcher_wires_advance_pipeline_hook_as_on_job_completed`、
`test_respond.py`の`test_run_respond_advances_pipeline_to_design_after_issue_analysis_done`は、
それぞれの合成ルート(`run_dispatcher`/`run_respond`)が実際に`advance_pipeline_hook`を配線して
いることを検証する結合寄りのテスト。

## 関連ドキュメント

- [architecture.md](../architecture.md) の「Orchestrator」の行(M3-7, M4-1〜M4-6, M4-9〜M4-10)
- [requirements.md](../requirements.md) の「B. Issue駆動開発(将来)」節 — 本モジュールが解決する
  「AIが分からないことを勝手に推測してしまう問題」の背景
- [ADR-0024: 「質問する / 仮定して進める」判断ロジックの設計](../adr/0024-ask-or-assume-judgment.md)
- [ADR-0035: Issue→MRパイプラインのオーケストレーション](../adr/0035-pipeline-orchestration.md)
- [specs/job-model.md](job-model.md) — `JobStatus.WAITING_HUMAN`と許可される状態遷移の定義。
  本モジュールが「いつ発動させるか」を判断する対象
- [specs/issue-analysis.md](issue-analysis.md) / [specs/design-phase.md](design-phase.md) /
  [specs/plan-phase.md](plan-phase.md) / [specs/implement-phase.md](implement-phase.md) /
  [specs/push-phase.md](push-phase.md) — `pipeline.py`が連鎖させる5フェーズそれぞれの仕様
- ソースコード: `src/gitlab_ai_platform/orchestrator/`(`types.py` / `judgment.py` / `errors.py` /
  `pipeline.py` / `__init__.py`)
