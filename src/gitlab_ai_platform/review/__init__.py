"""レビュープロンプトの設計と結果スキーマを担当する Review(`docs/architecture.md`)。"""

from __future__ import annotations

from .comparison import compare_findings
from .errors import ReviewError, ReviewOutputParseError
from .index import append_entry, read_index
from .job import (
    REVIEW_JOB_TYPE,
    build_review_job_payload,
    build_review_job_result,
    review_job_payload_to_args,
)
from .markdown import render_markdown
from .parser import parse_review_output
from .prompts import build_review_instructions
from .storage import load_review_result, save_review
from .types import (
    Finding,
    IndexEntry,
    ReviewComparison,
    ReviewPaths,
    ReviewResult,
    Severity,
)

__all__ = [
    "REVIEW_JOB_TYPE",
    "Finding",
    "IndexEntry",
    "ReviewComparison",
    "ReviewError",
    "ReviewOutputParseError",
    "ReviewPaths",
    "ReviewResult",
    "Severity",
    "append_entry",
    "build_review_instructions",
    "build_review_job_payload",
    "build_review_job_result",
    "compare_findings",
    "load_review_result",
    "parse_review_output",
    "read_index",
    "render_markdown",
    "review_job_payload_to_args",
    "save_review",
]
