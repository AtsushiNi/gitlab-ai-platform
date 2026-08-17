"""タスク種別を横断してライフサイクル(状態機械)を管理するJob抽象(`docs/architecture.md`)。"""

from __future__ import annotations

from .errors import (
    InvalidJobTransitionError,
    JobError,
    JobNotFoundError,
    LeaseLostError,
)
from .protocol import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    Job,
    JobRepository,
    JobStatus,
    JobType,
)
from .sqlite import SqliteJobRepository

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_VISIBILITY_TIMEOUT_SECONDS",
    "InvalidJobTransitionError",
    "Job",
    "JobError",
    "JobNotFoundError",
    "JobRepository",
    "JobStatus",
    "JobType",
    "LeaseLostError",
    "SqliteJobRepository",
]
