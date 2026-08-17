"""HTTP API サーバーが送出する例外の基底クラス。

`webhook/errors.py`と同じ方針で、サーバー内部の判定にのみ使う型を定義する。HTTPレスポンスへの
変換(ステータスコード決定)は`api/server.py`の責務(`docs/adr/0023-http-api.md`)。
"""

from __future__ import annotations


class ApiError(Exception):
    """HTTP APIサーバーの処理が失敗したことを表す基底例外。"""


class InvalidTokenError(ApiError):
    """`X-Api-Token`ヘッダが設定済みのトークンと一致しないことを表す。"""


class InvalidRequestError(ApiError):
    """リクエストボディ/クエリパラメータが不正であることを表す。"""


__all__ = ["ApiError", "InvalidRequestError", "InvalidTokenError"]
