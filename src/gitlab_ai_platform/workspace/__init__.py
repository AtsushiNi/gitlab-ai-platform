"""プロジェクトごとのbare clone、MR単位のworktree作成/更新/破棄を管理するWorkspace Manager
(`docs/architecture.md`)。"""

from __future__ import annotations

from .errors import DiskLimitExceededError, GitCommandError, WorkspaceError
from .git_workspace import GitWorkspaceManager
from .protocol import WorkspaceManager
from .types import IssueWorktreeHandle, WorktreeHandle

__all__ = [
    "DiskLimitExceededError",
    "GitCommandError",
    "GitWorkspaceManager",
    "IssueWorktreeHandle",
    "WorkspaceError",
    "WorkspaceManager",
    "WorktreeHandle",
]
