"""レビュープロンプトの設計と結果スキーマを担当する Review(`docs/architecture.md`)。"""

from __future__ import annotations

from .prompts import build_review_instructions

__all__ = ["build_review_instructions"]
