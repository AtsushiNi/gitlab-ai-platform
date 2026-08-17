"""GitLab Merge Request Hookを受信し、レビューJobを起票するWebhookサーバー(任意有効化)。

方針(M3-6 [#96](https://github.com/AtsushiNi/gitlab-ai-platform/issues/96)、
`docs/adr/0018-webhook-receiver.md`):

- 標準ライブラリの`http.server.ThreadingHTTPServer`のみを使う(ADR-0001が許可する外部依存は
  `requests`/`pytest`/`mcp`のみで、新規のWebフレームワークは追加しない)。
- 扱うイベントは`X-Gitlab-Event: Merge Request Hook`のみ。それ以外(Push Hook等、GitLab側の
  設定ミスで送られてきた場合)はエラーにせず`200 OK`で無視する(GitLabは4xx/5xxが続くと
  Webhookを自動無効化するため)。
- `X-Gitlab-Token`ヘッダを`secrets.compare_digest`で定数時間比較し、設定済みSecret Tokenと
  一致しない場合は`401 Unauthorized`を返す。
- 二重起票の防止は、MR Poller(`poller/poller.py`)と全く同じ`ticket_if_unprocessed`
  (State Storeの`find`→`create`ダンス)を呼ぶことで実現する。ロジックの複製をしない。
- 検出(`ticket_if_unprocessed`が`DetectedReview`を返した)後の実際のレビュー実行は、
  この層の責務ではない。`on_detected`コールバック(`cli/watch.py`が`MrPoller.run`と同じ
  `ReviewWorkerPool`への投入ラッパーを渡す)に委ねることで、Poller/Webhookの実行経路
  (並列数制御・エラー処理・ログ)を完全に共有する。
- `start`/`stop`はバックグラウンドスレッドでの起動/停止を担う。`webhook.enabled=false`
  (既定値)の場合、`cli/watch.py`はこのクラスを一切インスタンス化しない
  (「片方だけでも動く」という要件、`docs/adr/0017`)。
"""

from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..logging_ import get_logger
from ..poller.poller import ticket_if_unprocessed
from ..poller.types import DetectedReview, PollError
from ..store.protocol import StateStore
from .errors import InvalidSecretTokenError, WebhookPayloadError
from .parser import parse_merge_request_event
from .types import ParsedMergeRequestEvent

_logger = get_logger(__name__)

_GITLAB_EVENT_HEADER = "X-Gitlab-Event"
_GITLAB_TOKEN_HEADER = "X-Gitlab-Token"
_MERGE_REQUEST_EVENT_NAME = "Merge Request Hook"

_IGNORED_BODY = b'{"status": "ignored"}'
_DUPLICATE_BODY = b'{"status": "duplicate"}'
_TICKETED_BODY = b'{"status": "ticketed"}'
_INVALID_TOKEN_BODY = b'{"error": "invalid secret token"}'
_INVALID_PAYLOAD_BODY = b'{"error": "invalid payload"}'
_STATE_STORE_ERROR_BODY = b'{"error": "state store error"}'
_NOT_FOUND_BODY = b'{"error": "not found"}'


class WebhookServer:
    """GitLab Webhook(Merge Request Hook)を受信し、レビューJobを起票するHTTPサーバー。"""

    def __init__(
        self,
        store: StateStore,
        *,
        review_label: str,
        secret_token: str,
        host: str,
        port: int,
        path: str,
        on_detected: Callable[[DetectedReview], None],
    ) -> None:
        self._store = store
        self._review_label = review_label
        self._secret_token = secret_token
        self._path = path
        self._on_detected = on_detected

        self._httpd = ThreadingHTTPServer((host, port), _make_handler_class(self))
        self._thread: threading.Thread | None = None

    @property
    def path(self) -> str:
        return self._path

    @property
    def server_port(self) -> int:
        """実際にbindされたポート番号(`port=0`指定時のOS割り当てを含む、主にテスト用)。"""
        return self._httpd.server_port

    def start(self) -> None:
        """バックグラウンドスレッドでリクエスト処理を開始する。"""
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="webhook-server", daemon=True
        )
        self._thread.start()
        _logger.info(
            "webhook.server_started",
            extra={"host": self._httpd.server_address[0], "port": self.server_port},
        )

    def stop(self) -> None:
        """リクエスト処理を停止し、リソースを解放する(`start`前に呼んでも安全)。"""
        self._httpd.shutdown()
        if self._thread is not None:
            self._thread.join()
        self._httpd.server_close()
        _logger.info("webhook.server_stopped")

    def handle_event(
        self, event_name: str, token: str | None, body: bytes
    ) -> tuple[HTTPStatus, bytes]:
        """1リクエスト分の処理本体。HTTPハンドラから呼ばれる(テスト容易化のため分離)。"""
        try:
            parsed = self._authenticate_and_parse(event_name, token, body)
        except InvalidSecretTokenError:
            _logger.warning("webhook.invalid_secret_token")
            return HTTPStatus.UNAUTHORIZED, _INVALID_TOKEN_BODY
        except WebhookPayloadError as exc:
            _logger.warning("webhook.invalid_payload", extra={"error": str(exc)})
            return HTTPStatus.BAD_REQUEST, _INVALID_PAYLOAD_BODY

        if parsed is None or self._review_label not in parsed.labels:
            return HTTPStatus.OK, _IGNORED_BODY

        outcome = ticket_if_unprocessed(
            self._store, parsed.project, parsed.mr_iid, parsed.commit_sha
        )
        if isinstance(outcome, DetectedReview):
            self._on_detected(outcome)
            return HTTPStatus.ACCEPTED, _TICKETED_BODY
        if isinstance(outcome, PollError):
            _logger.error(
                "webhook.ticket_creation_failed", extra={"error": outcome.message}
            )
            return HTTPStatus.INTERNAL_SERVER_ERROR, _STATE_STORE_ERROR_BODY

        # 既に起票済み(PollerまたはWebhookの過去のリクエストが先に処理済み)
        return HTTPStatus.OK, _DUPLICATE_BODY

    def _authenticate_and_parse(
        self, event_name: str, token: str | None, body: bytes
    ) -> ParsedMergeRequestEvent | None:
        """Secret Token検証→イベント種別フィルタ→JSONパース→ペイロードパースの順に行う。

        `InvalidSecretTokenError`/`WebhookPayloadError`はそのまま送出し、呼び出し元
        (`handle_event`)がHTTPステータスへ変換する(`cli/main.py`の例外→終了コード変換と
        同じ「本体は例外を送出するだけ、変換は呼び出し側」という分離パターン)。
        """
        if not secrets.compare_digest(token or "", self._secret_token):
            raise InvalidSecretTokenError(
                "X-Gitlab-Tokenが設定済みのSecret Tokenと一致しません"
            )

        if event_name != _MERGE_REQUEST_EVENT_NAME:
            # Push Hook等、Merge Request Hook以外のイベント種別は対象外として無視する
            # (エラーにはしない。GitLabは4xx/5xxが続くとWebhookを自動無効化するため)
            _logger.debug("webhook.event_ignored", extra={"event": event_name})
            return None

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise WebhookPayloadError(
                f"リクエストボディがJSONとして不正です: {exc}"
            ) from exc

        return parse_merge_request_event(payload)


def _make_handler_class(server: WebhookServer) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            # 標準の`BaseHTTPRequestHandler`アクセスログ(stderr直接出力)は構造化ログの方針
            # (`logging_/`)に合わないため、`get_logger`経由に差し替える
            _logger.debug("webhook.access", extra={"message": format % args})

        def do_POST(self) -> None:
            if self.path != server.path:
                self._respond(HTTPStatus.NOT_FOUND, _NOT_FOUND_BODY)
                return

            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = 0
            body = self.rfile.read(content_length) if content_length > 0 else b""

            status, response_body = server.handle_event(
                self.headers.get(_GITLAB_EVENT_HEADER, ""),
                self.headers.get(_GITLAB_TOKEN_HEADER),
                body,
            )
            self._respond(status, response_body)

        def _respond(self, status: HTTPStatus, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _Handler


__all__ = ["WebhookServer"]
