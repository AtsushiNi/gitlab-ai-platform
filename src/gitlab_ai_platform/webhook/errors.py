"""Webhook受信サーバーが送出する例外の基底クラス。

`gitlab_adapter/errors.py`・`store/errors.py`と同じ方針で、呼び出し側が握れる型を定義する。
これらはサーバー内部の判定に使うのみで、HTTPレスポンスへの変換(ステータスコード決定)は
`webhook/server.py`の責務(`docs/adr/0018-webhook-receiver.md`)。
"""

from __future__ import annotations


class WebhookError(Exception):
    """Webhook受信処理が失敗したことを表す基底例外。"""


class InvalidSecretTokenError(WebhookError):
    """`X-Gitlab-Token`ヘッダが設定済みのSecret Tokenと一致しないことを表す。"""


class WebhookPayloadError(WebhookError):
    """リクエストボディがJSONとして不正、または必須フィールドを欠いていることを表す。"""


__all__ = ["InvalidSecretTokenError", "WebhookError", "WebhookPayloadError"]
