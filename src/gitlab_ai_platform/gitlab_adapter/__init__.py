"""GitLab とのやりとりを一手に引き受ける唯一の窓口(`docs/architecture.md`)。"""

from __future__ import annotations

from .errors import GitLabAdapterError, GitLabApiError
from .protocol import GitLabAdapter, GitLabReader, GitLabWriter
from .rest import GitLabRestAdapter
from .types import (
    Branch,
    CommitAction,
    CommitActionType,
    Discussion,
    MergeRequest,
    MergeRequestDiff,
    Note,
)

__all__ = [
    "GitLabAdapter",
    "GitLabReader",
    "GitLabWriter",
    "GitLabRestAdapter",
    "GitLabAdapterError",
    "GitLabApiError",
    "Branch",
    "CommitAction",
    "CommitActionType",
    "Discussion",
    "MergeRequest",
    "MergeRequestDiff",
    "Note",
]
