"""設計フェーズ(M4-6、Job種別`design`)。

要求分析フェーズ(`issue_analysis`)の結果を元に、実装前の設計をレビュー可能な成果物として
出力する。`issue_analysis`パッケージの「プロンプト設計→Runner実行→応答パース→保存」という
流れと同じ設計パターンを踏襲する。実際にRunnerを呼び出しJobとして処理する部分
(`JobHandler`)は`cli/dispatcher.py`の`build_design_handler`。

**無人実行トラック限定の機能。** 対話型トラック(VS Code拡張)では対話の中で自然に設計内容が
確認されるため、このフェーズ自体が不要(ADR-0029)。
"""

from __future__ import annotations

from .errors import DesignError, DesignOutputParseError
from .job import (
    build_design_job_payload,
    build_design_job_result,
    build_resolved_design_job_result,
    design_job_payload_to_args,
)
from .parser import parse_design_output
from .prompts import build_design_instructions
from .types import DesignInput, DesignResult

__all__ = [
    "DesignError",
    "DesignInput",
    "DesignOutputParseError",
    "DesignResult",
    "build_design_instructions",
    "build_design_job_payload",
    "build_design_job_result",
    "build_resolved_design_job_result",
    "design_job_payload_to_args",
    "parse_design_output",
]
