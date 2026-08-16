"""タスク種別を横断してライフサイクル(状態機械)を管理するJob抽象(`docs/architecture.md`)。"""

from __future__ import annotations

from .errors import InvalidJobTransitionError, JobError, JobNotFoundError
from .protocol import Job, JobRepository, JobStatus, JobType
from .sqlite import SqliteJobRepository

__all__ = [
    "InvalidJobTransitionError",
    "Job",
    "JobError",
    "JobNotFoundError",
    "JobRepository",
    "JobStatus",
    "JobType",
    "SqliteJobRepository",
]
