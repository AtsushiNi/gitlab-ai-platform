# Orchestrator

- 実装場所: `src/gitlab_ai_platform/orchestrator/`
- 対応Issue: [#110](https://github.com/AtsushiNi/gitlab-ai-platform/issues/110) (M4-4)
- 関連ADR: [ADR-0024](../adr/0024-ask-or-assume-judgment.md)
- ステータス: 実装中(「質問する / 仮定して進める」判断ロジックのみ実装済み。フェーズ間の
  状態遷移そのもの(M4-1〜M4-6, M4-9〜M4-10)は未実装)

## 責務

無人実行パイプライン(Issue → 要求分析 → 設計 → 実装 → MR作成、`docs/architecture.md`の
「Orchestrator」)のうち、本ファイルが現時点でカバーするのは「要求分析・設計・実装の各フェーズが
検出した不明点について、処理を止めて人間に確認すべきか、仮定を明示して継続してよいかを判定する」
部分のみ(M4-4)。フェーズ間の状態遷移そのもの・HTTP API/サーバ層による外部連携は将来の
M4-1〜M4-6, M4-9〜M4-10で同じ`orchestrator`パッケージに追加していく。

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
  - Jobの`update_status`呼び出しによる実際の状態遷移(`RUNNING → WAITING_HUMAN`等)。本モジュールは
    「遷移すべきか」の判定(`requires_human`)のみを提供し、`job/protocol.py`の`JobRepository`には
    依存しない(ADR-0024)
  - 人間への質問の実際の提示先(ローカル通知 or Issueコメント)と、回答を受けての`WAITING_HUMAN`
    からの再開処理。これはM4-5(人間への質問提示と回答の取り込み)のスコープ
  - MR本文への「○○と仮定して実装した」という記述の実際の組み立て・整形。本モジュールは
    `UncertaintyJudgment.assumption_note`として仮定の文言を返すところまでで、MR本文への反映は
    M4-9(pushとMR作成)のスコープ
  - 重要度のキーワードベース自動判定・LLMによる重要度推定。将来必要になれば、`Uncertainty`を
    生成する呼び出し側(または呼び出し側とこのモジュールの間の新しい層)に追加する

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

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/orchestrator/types.py`。

| 型 | フィールド | 補足 |
|---|---|---|
| `UncertaintySeverity` (Enum) | `CRITICAL` / `MINOR` | `CRITICAL`=重要な不明点、`MINOR`=軽微な疑問。呼び出し側が`Uncertainty`作成時に明示する |
| `JudgmentAction` (Enum) | `ASK` / `ASSUME` | `judge_uncertainty`/`judge_uncertainties`が返す判定結果 |
| `Uncertainty` (frozen dataclass) | `question: str`, `severity: UncertaintySeverity`, `assumption: str \| None = None`, `phase: str \| None = None` | `question`は人間へ提示する質問文相当。`assumption`は`MINOR`でASSUME判定にする場合に必須(無いと`MissingAssumptionError`)。`phase`は不明点が生じたフェーズ名(例: `"issue-analysis"`)の任意情報で、判定ロジック自体には使わない |
| `UncertaintyJudgment` (frozen dataclass) | `uncertainty: Uncertainty`, `action: JudgmentAction`, `assumption_note: str \| None = None` | `assumption_note`は`action=ASSUME`の場合のみ値を持ち、`uncertainty.assumption`の転記 |
| `JudgmentPolicy` (frozen dataclass) | `ask_severities: frozenset[UncertaintySeverity]` | この集合に含まれる重要度は`ASK`、含まれない重要度は`ASSUME`と判定する。既定値は`{CRITICAL}` |

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/orchestrator/errors.py`。

- `OrchestratorError(Exception)` — Orchestratorパッケージ経由の操作が失敗したことを表す基底例外
- `MissingAssumptionError(OrchestratorError)` — `ASSUME`判定になるはずの`Uncertainty`に
  `assumption`が設定されていないことを表す。呼び出し側(要求分析等の各フェーズ)が、軽微な疑問を
  検出した時点で仮定の文言も一緒に用意すべきだったことを意味し、通常は呼び出し側のバグとして
  扱う(リトライで解決しない)

## テスト方針

実装場所: `tests/gitlab_ai_platform/orchestrator/`(`src/`をミラー、ADR-0001)。`unittest.mock`は
使わず、外部依存を持たない純粋関数のみのためフェイクも不要。

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

## 関連ドキュメント

- [architecture.md](../architecture.md) の「Orchestrator」の行(M3-7, M4-1〜M4-6, M4-9〜M4-10)
- [requirements.md](../requirements.md) の「B. Issue駆動開発(将来)」節 — 本モジュールが解決する
  「AIが分からないことを勝手に推測してしまう問題」の背景
- [ADR-0024: 「質問する / 仮定して進める」判断ロジックの設計](../adr/0024-ask-or-assume-judgment.md)
- [specs/job-model.md](job-model.md) — `JobStatus.WAITING_HUMAN`と許可される状態遷移の定義。
  本モジュールが「いつ発動させるか」を判断する対象
- ソースコード: `src/gitlab_ai_platform/orchestrator/`(`types.py` / `judgment.py` / `errors.py` /
  `__init__.py`)
