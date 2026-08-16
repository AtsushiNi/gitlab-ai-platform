"""GitLab とのやりとりを一手に引き受ける唯一の窓口(`docs/architecture.md`)。"""

from __future__ import annotations

from .errors import GitLabAdapterError, GitLabApiError, ProtectedBranchError
from .protocol import GitLabAdapter, GitLabReader, GitLabWriter
from .rest import GitLabRestAdapter
from .types import (
    Branch,
    CommitAction,
    CommitActionType,
    Discussion,
    Issue,
    MergeRequest,
    MergeRequestDiff,
    Note,
)

__all__ = [
    "Branch",
    "CommitAction",
    "CommitActionType",
    "Discussion",
    "GitLabAdapter",
    "GitLabAdapterError",
    "GitLabApiError",
    "GitLabReader",
    "GitLabRestAdapter",
    "GitLabWriter",
    "Issue",
    "MergeRequest",
    "MergeRequestDiff",
    "Note",
    "ProtectedBranchError",
]
