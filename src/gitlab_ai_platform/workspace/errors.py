"""Workspace Manager が送出する例外の基底クラス。

具体的なgitコマンドの失敗理由・ディスク上限の判定ロジックは実装(`git_workspace.py`)の責務。
ここではインターフェースとして呼び出し側が握れる型だけを定義する(`store/errors.py`と同じ方針)。
"""

from __future__ import annotations


class WorkspaceError(Exception):
    """Workspace Manager経由の操作が失敗したことを表す基底例外。"""


class GitCommandError(WorkspaceError):
    """gitコマンドが非ゼロの終了コードで終了したことを表す。"""

    def __init__(
        self, message: str, *, command: list[str], returncode: int, stderr: str
    ) -> None:
        super().__init__(message)
        self.command = command
        self.returncode = returncode
        self.stderr = stderr


class DiskLimitExceededError(WorkspaceError):
    """GCで破棄可能なworktreeを全て破棄してもなお、ディスク上限を超過していることを表す。"""


__all__ = ["DiskLimitExceededError", "GitCommandError", "WorkspaceError"]
