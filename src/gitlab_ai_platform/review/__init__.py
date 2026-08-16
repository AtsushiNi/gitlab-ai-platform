"""レビュープロンプトの設計と結果スキーマを担当する Review(`docs/architecture.md`)。"""

from __future__ import annotations

from .errors import ReviewError, ReviewOutputParseError
from .index import append_entry, read_index
from .markdown import render_markdown
from .parser import parse_review_output
from .prompts import build_review_instructions
from .storage import save_review
from .types import Finding, IndexEntry, ReviewPaths, ReviewResult, Severity

__all__ = [
    "Finding",
    "IndexEntry",
    "ReviewError",
    "ReviewOutputParseError",
    "ReviewPaths",
    "ReviewResult",
    "Severity",
    "append_entry",
    "build_review_instructions",
    "parse_review_output",
    "read_index",
    "render_markdown",
    "save_review",
]
