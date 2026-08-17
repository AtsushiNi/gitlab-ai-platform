"""Issue Ticket Store が送出する例外の基底クラス。

具体的なエラー分類(SQLite固有のエラーの変換等)は実装側の責務。ここではインターフェースと
して呼び出し側が握れる型だけを定義する(`store/errors.py`と同じ方針)。
"""

from __future__ import annotations


class IssueTicketStoreError(Exception):
    """Issue Ticket Store経由の操作が失敗したことを表す基底例外。"""


class DuplicateIssueTicketError(IssueTicketStoreError):
    """同一の`(project, issue_iid)`に対するレコードが既に存在することを表す。

    二重投入防止の一意制約に違反した場合に送出する(`store.DuplicateReviewError`と同じ契約)。
    """


__all__ = ["DuplicateIssueTicketError", "IssueTicketStoreError"]
