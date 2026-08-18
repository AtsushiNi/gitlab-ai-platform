"""「質問する / 仮定して進める」判断ロジックが扱うデータの型。

要求分析(M4-3)・設計(M4-6)・実装(M4-8)など、無人実行パイプラインの各フェーズで生じた
不明点を表す `Uncertainty` と、それに対する判定結果 `UncertaintyJudgment` を定義する
(`docs/requirements.md` の「B. Issue駆動開発(将来)」節、ADR-0024)。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UncertaintySeverity(str, Enum):
    """不明点の重要度。

    呼び出し側(要求分析フェーズ等)が不明点を作成する時点で明示する
    (ADR-0024「重要度は呼び出し側が明示する」)。
    """

    # 重要な不明点。処理を止めて人間に確認する必要がある(WAITING_HUMAN)
    CRITICAL = "critical"
    # 軽微な疑問。仮定を明示した上で処理を継続してよい
    MINOR = "minor"


class JudgmentAction(str, Enum):
    """1件の不明点に対して取るべき行動。"""

    # 質問して停止する(呼び出し側がJobをWAITING_HUMANへ遷移させる根拠にする)
    ASK = "ask"
    # 仮定を明示して処理を継続する
    ASSUME = "assume"


@dataclass(frozen=True)
class Uncertainty:
    """処理中に生じた1件の不明点。

    `assumption` は `severity=MINOR` で仮定して継続する場合に採用する仮定の文言
    (例:「Aのケースを主対象と仮定した」)。`judge_uncertainty` はこれをそのまま
    `UncertaintyJudgment.assumption_note` へ転記する。MRへの実際の反映(「○○と仮定して
    実装した」という記述の組み立て)はM4-9の責務で、本モジュールの非対象。
    """

    question: str
    severity: UncertaintySeverity
    assumption: str | None = None
    # 不明点が生じたフェーズ(例: "issue-analysis" / "design" / "implement")。
    # 呼び出し元の識別・ログ用の任意情報で、判定ロジック自体には使わない
    phase: str | None = None


@dataclass(frozen=True)
class UncertaintyJudgment:
    """1件の `Uncertainty` に対する判定結果。"""

    uncertainty: Uncertainty
    action: JudgmentAction
    # action=ASSUME の場合のみ値を持つ(`uncertainty.assumption` の転記)
    assumption_note: str | None = None


__all__ = [
    "JudgmentAction",
    "Uncertainty",
    "UncertaintyJudgment",
    "UncertaintySeverity",
]
