"""無人実行パイプラインのフェーズ間の状態遷移を扱うOrchestrator(`docs/architecture.md`)。

M4-4時点で「質問する / 仮定して進める」の判断ロジック(`judgment.py`)を実装済み。
M4-10([#116](https://github.com/AtsushiNi/gitlab-ai-platform/issues/116)、
[ADR-0035](../../../docs/adr/0035-pipeline-orchestration.md))で、`issue-analysis → design →
plan → implement → push`の連鎖を担う`advance_pipeline`(`pipeline.py`)を追加した。

`pipeline.py`は**この`__init__.py`から再エクスポートしない**(意図的)。`pipeline.py`は
`design`/`plan`/`implement`/`push`各パッケージに依存するが、それらのパッケージは
`from ..orchestrator import UncertaintyJudgment, ...`という形で本パッケージに依存して
おり、`__init__.py`が`pipeline`をimportすると循環importになりうる(`pipeline.py`の
モジュールdocstring参照)。呼び出し側は`from gitlab_ai_platform.orchestrator.pipeline
import advance_pipeline`と明示的にサブモジュールをimportする。
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
