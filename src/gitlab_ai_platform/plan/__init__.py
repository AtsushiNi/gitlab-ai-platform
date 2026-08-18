"""実装計画フェーズ(M4-7、Job種別`plan`)。

設計フェーズ(`design`)の結果を元に、実装可能な粒度のタスクへ具体化する。`design`パッケージの
「プロンプト設計→Runner実行→応答パース→保存」という流れと同じ設計パターンを踏襲する。
実際にRunnerを呼び出しJobとして処理する部分(`JobHandler`)は`cli/dispatcher.py`の
`build_plan_handler`。

**無人実行トラック限定の機能。** 対話型トラック(VS Code拡張)では対話の中で自然にタスクの
分解が行われるため、このフェーズ自体が不要(ADR-0030)。
"""

from __future__ import annotations

from .errors import PlanError, PlanOutputParseError
from .job import (
    build_plan_job_payload,
    build_plan_job_result,
    build_resolved_plan_job_result,
    plan_job_payload_to_args,
)
from .parser import parse_plan_output
from .prompts import build_plan_instructions
from .types import PlanInput, PlanResult, PlanTask

__all__ = [
    "PlanError",
    "PlanInput",
    "PlanOutputParseError",
    "PlanResult",
    "PlanTask",
    "build_plan_instructions",
    "build_plan_job_payload",
    "build_plan_job_result",
    "build_resolved_plan_job_result",
    "parse_plan_output",
    "plan_job_payload_to_args",
]
