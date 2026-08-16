"""State Store が送出する例外の基底クラス。

具体的なエラー分類(SQLite/PostgreSQL固有のエラーの変換等)は実装側の責務。
ここではインターフェースとして呼び出し側が握れる型だけを定義する
(`gitlab_adapter/errors.py` と同じ方針)。
"""

from __future__ import annotations


class StateStoreError(Exception):
    """State Store経由の操作が失敗したことを表す基底例外。"""


class DuplicateReviewError(StateStoreError):
    """同一の`(project, mr_iid, commit_sha)`に対するレコードが既に存在することを表す。

    二重レビュー防止の一意制約に違反した場合に送出する。
    """


class RecordNotFoundError(StateStoreError):
    """指定した`(project, mr_iid, commit_sha)`のレコードが存在しないことを表す。"""


__all__ = ["DuplicateReviewError", "RecordNotFoundError", "StateStoreError"]
