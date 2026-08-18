"""無人実行パイプラインのフェーズ間の状態遷移を扱うOrchestrator(`docs/architecture.md`)。

M4-4時点で「質問する / 仮定して進める」の判断ロジック(`judgment.py`)を実装済み。
M4-10([#116](https://github.com/AtsushiNi/gitlab-ai-platform/issues/116)、
[ADR-0035](../../../docs/adr/0035-pipeline-orchestration.md))で、`issue-analysis → design →
plan → implement → push`の連鎖を担う`advance_pipeline`(`pipeline.py`)を追加した。
M4-11([#117](https://github.com/AtsushiNi/gitlab-ai-platform/issues/117)、
[ADR-0036](../../../docs/adr/0036-self-review-connection.md))で、`push`完了後に`review`
Jobを自動投入する接続を同じ`advance_pipeline`に追加し、`issue-analysis → design → plan →
implement → push → review`という6フェーズの連鎖になった。

`pipeline.py`は**この`__init__.py`から再エクスポートしない**(意図的)。`pipeline.py`は
`design`/`plan`/`implement`/`push`/`review`各パッケージに依存するが、それらのうち
`design`/`plan`/`implement`/`push`は`from ..orchestrator import UncertaintyJudgment, ...`
という形で本パッケージに依存しており、`__init__.py`が`pipeline`をimportすると循環importに
なりうる(`pipeline.py`のモジュールdocstring参照)。呼び出し側は
`from gitlab_ai_platform.orchestrator.pipeline import advance_pipeline`と明示的に
サブモジュールをimportする。
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
