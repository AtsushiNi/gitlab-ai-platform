"""`(project, mr_iid, commit_sha)`単位でレビュー状態を記録するState Store(`docs/architecture.md`)。"""

from __future__ import annotations

from .errors import DuplicateReviewError, RecordNotFoundError, StateStoreError
from .protocol import StateStore
from .sqlite import SqliteStateStore
from .types import ReviewRecord, ReviewStatus

__all__ = [
    "DuplicateReviewError",
    "RecordNotFoundError",
    "ReviewRecord",
    "ReviewStatus",
    "SqliteStateStore",
    "StateStore",
    "StateStoreError",
]
