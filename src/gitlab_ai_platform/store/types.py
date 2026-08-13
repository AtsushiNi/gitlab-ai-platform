"""State Store が読み書きするデータの型。

`(project, mr_iid, commit_sha)` 単位でレビュー状態を記録するための型を定義する
(`docs/architecture.md` の State Store の責務)。SQLite/PostgreSQLどちらの実装を使っても
同じ形になるよう、DB固有の型(SQLiteの動的型付け等)を透過させない。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ReviewStatus(str, Enum):
    """レビューの進行状態。"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class ReviewRecord:
    """`(project, mr_iid, commit_sha)` 単位のレビュー状態レコード。"""

    project: str
    mr_iid: int
    commit_sha: str
    status: ReviewStatus
    reviewed_at: datetime | None = None
    result_path: str | None = None


__all__ = ["ReviewStatus", "ReviewRecord"]
