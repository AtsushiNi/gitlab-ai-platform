"""レビュー結果として扱うデータの型。

`docs/architecture.md` の Review の責務のうち、結果スキーマ(M1-9, #37)を担当する。
Claude Codeの応答(`runner.RunResult.result_text`)をパースした結果(`parser.py`)、
それをMarkdownに整形した結果(`markdown.py`)、保存先パス(`storage.py`)、索引の1行分
(`index.py`)の型をここに集約する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


class Severity(str, Enum):
    """指摘1件の重要度。

    `docs/guide/reading-results.md`(D-15)・M2-6で使う分類(critical/major/minor)と揃える。
    """

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


@dataclass(frozen=True)
class Finding:
    """指摘1件。重要度・対象ファイル・行・根拠・改善案を持つ。"""

    severity: Severity
    file: str
    line: int | None
    rationale: str
    suggestion: str


@dataclass(frozen=True)
class ReviewResult:
    """Claude Codeの応答をパースした、1回のレビューの結果。

    `project`/`mr_iid`/`sha`のような対象の識別情報は持たない(`parser.parse_review_output`は
    `result_text`という自然文からしか結果を組み立てられず、識別情報を知らないため)。
    保存(`storage.save_review`)や索引(`index.IndexEntry`)側で、呼び出し側が別途持つ
    識別情報と組み合わせる。
    """

    summary: str
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class ReviewPaths:
    """`storage.save_review`が1回のレビューについて書き出したファイルパス一式。"""

    dir: Path
    result_json: Path
    result_md: Path
    input_path: Path
    log_path: Path


@dataclass(frozen=True)
class ReviewComparison:
    """前回レビューとの突き合わせ結果(M2-2, #81)。

    `comparison.py`の`compare_findings`が組み立てる。前回レビューが存在しない
    (初回レビューの)場合は`ReviewComparison`自体を作らず`None`で表す(呼び出し側は
    `is None`で「比較対象が無い」ことを判定する)。
    """

    new: tuple[Finding, ...]
    """今回のfindingsのうち、前回に対応する指摘が見つからなかったもの(新規)。"""

    unresolved: tuple[Finding, ...]
    """今回のfindingsのうち、前回にも対応する指摘があったもの(未対応)。"""

    resolved: tuple[Finding, ...]
    """前回のfindingsのうち、今回は対応する指摘が見つからなかったもの(修正済み)。"""


@dataclass(frozen=True)
class IndexEntry:
    """複数レビューを横断する索引(`index.jsonl`)の1行分。"""

    project: str
    mr_iid: int
    sha: str
    reviewed_at: datetime
    result_dir: str
    summary: str
    critical_count: int
    major_count: int
    minor_count: int


__all__ = [
    "Finding",
    "IndexEntry",
    "ReviewComparison",
    "ReviewPaths",
    "ReviewResult",
    "Severity",
]
