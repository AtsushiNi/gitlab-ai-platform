"""要求分析フェーズ(M4-3、Job種別`issue-analysis`)。

Issueを分析し、要求・受入条件・前提・不足情報を構造化して出力する。`review`パッケージの
「プロンプト設計→Runner実行→応答パース→保存」という流れと同じ設計パターンを、
Issue駆動開発の無人実行トラック向けに横展開したもの。実際にRunnerを呼び出しJobとして
処理する部分(`JobHandler`)は`cli/dispatcher.py`の`build_issue_analysis_handler`。
"""

from __future__ import annotations

from .errors import IssueAnalysisError, IssueAnalysisOutputParseError
from .job import (
    build_issue_analysis_job_result,
    build_resolved_issue_analysis_job_result,
)
from .parser import parse_issue_analysis_output
from .prompts import build_issue_analysis_instructions
from .types import RequirementAnalysis

__all__ = [
    "IssueAnalysisError",
    "IssueAnalysisOutputParseError",
    "RequirementAnalysis",
    "build_issue_analysis_instructions",
    "build_issue_analysis_job_result",
    "build_resolved_issue_analysis_job_result",
    "parse_issue_analysis_output",
]
