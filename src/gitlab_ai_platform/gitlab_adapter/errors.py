"""GitLab Adapter が送出する例外の基底クラス。

具体的なリトライ・エラー分類(429ハンドリング等)は実装(REST, M1-2)の責務。
ここではインターフェースとして呼び出し側が握れる型だけを定義する。
"""

from __future__ import annotations


class GitLabAdapterError(Exception):
    """Adapter経由のGitLab操作が失敗したことを表す基底例外。"""


class GitLabApiError(GitLabAdapterError):
    """GitLab APIがエラーレスポンスを返したことを表す。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProtectedBranchError(GitLabAdapterError):
    """protected branchへの書き込みをAdapter層で拒否したことを表す(APIには到達しない)。"""


__all__ = ["GitLabAdapterError", "GitLabApiError", "ProtectedBranchError"]
