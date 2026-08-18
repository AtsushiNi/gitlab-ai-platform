"""無人実行パイプラインのフェーズ間の状態遷移を扱うOrchestrator(`docs/architecture.md`)。

M4-4時点では「質問する / 仮定して進める」の判断ロジック(`judgment.py`)のみを持つ。
フェーズ間の状態遷移そのもの(M4-1〜M4-6, M4-9〜M4-10)は将来ここに追加していく。
"""

from .errors import MissingAssumptionError, OrchestratorError
from .judgment import (
    DEFAULT_POLICY,
    JudgmentPolicy,
    ask_judgments,
    assume_judgments,
    judge_uncertainties,
    judge_uncertainty,
    requires_human,
)
from .types import JudgmentAction, Uncertainty, UncertaintyJudgment, UncertaintySeverity

__all__ = [
    "DEFAULT_POLICY",
    "JudgmentAction",
    "JudgmentPolicy",
    "MissingAssumptionError",
    "OrchestratorError",
    "Uncertainty",
    "UncertaintyJudgment",
    "UncertaintySeverity",
    "ask_judgments",
    "assume_judgments",
    "judge_uncertainties",
    "judge_uncertainty",
    "requires_human",
]
