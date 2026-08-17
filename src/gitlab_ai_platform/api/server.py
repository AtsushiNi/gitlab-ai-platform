"""Job Repositoryへの最小限のHTTP API(Job投入・状態参照・結果取得・一覧取得)。

方針(M3-7 [#97](https://github.com/AtsushiNi/gitlab-ai-platform/issues/97)、
`docs/adr/0023-http-api.md`):

- 標準ライブラリの`http.server.ThreadingHTTPServer`のみを使う(`webhook/server.py`と同じ、
  ADR-0001の依存最小化方針)。
- `JobRepository`の既存メソッド(`enqueue`/`get`/`list_by_status`/`list_dead_letters`)を
  そのまま呼ぶだけで、シグネチャは変更しない。`claim`/`heartbeat`/`complete`/`fail`
  (Runner Dispatcher専用、ADR-0017/ADR-0022)はこのAPIからは呼ばない(実行そのものは
  この層の責務ではない)。
- 認証は`X-Api-Token`ヘッダの`secrets.compare_digest`定数時間比較(ADR-0018のSecret Token
  方式と同じ考え方)。
- `start`/`stop`はバックグラウンドスレッドでの起動/停止を担う(`WebhookServer`と同じ形)。
"""

from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..job.errors import JobError
from ..job.protocol import Job, JobRepository, JobStatus, JobType
from ..logging_ import get_logger
from .errors import InvalidRequestError, InvalidTokenError

_logger = get_logger(__name__)

_TOKEN_HEADER = "X-Api-Token"
_JOBS_PATH = "/jobs"
_DEAD_LETTERS_PATH = "/jobs/dead-letters"

_INVALID_TOKEN_BODY = b'{"error": "invalid api token"}'
_NOT_FOUND_BODY = b'{"error": "not found"}'
_INTERNAL_ERROR_BODY = b'{"error": "job repository error"}'


class ApiServer:
    """Job Repositoryを操作する最小限のHTTP API(Job投入・状態参照・結果取得・一覧取得)。"""

    def __init__(
        self,
        job_repo: JobRepository,
        *,
        token: str,
        host: str,
        port: int,
    ) -> None:
        self._job_repo = job_repo
        self._token = token

        self._httpd = ThreadingHTTPServer((host, port), _make_handler_class(self))
        self._thread: threading.Thread | None = None

    @property
    def server_port(self) -> int:
        """実際にbindされたポート番号(`port=0`指定時のOS割り当てを含む、主にテスト用)。"""
        return self._httpd.server_port

    def start(self) -> None:
        """バックグラウンドスレッドでリクエスト処理を開始する。"""
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="api-server", daemon=True
        )
        self._thread.start()
        _logger.info(
            "api.server_started",
            extra={"host": self._httpd.server_address[0], "port": self.server_port},
        )

    def stop(self) -> None:
        """リクエスト処理を停止し、リソースを解放する(`start`前に呼んでも安全)。"""
        self._httpd.shutdown()
        if self._thread is not None:
            self._thread.join()
        self._httpd.server_close()
        _logger.info("api.server_stopped")

    def handle_get(
        self, path: str, query: str, token: str | None
    ) -> tuple[HTTPStatus, bytes]:
        """GETリクエスト1件分の処理本体(テスト容易化のためHTTPハンドラから分離)。"""
        try:
            self._authenticate(token)
        except InvalidTokenError:
            _logger.warning("api.invalid_token")
            return HTTPStatus.UNAUTHORIZED, _INVALID_TOKEN_BODY

        try:
            if path == _DEAD_LETTERS_PATH:
                jobs = self._job_repo.list_dead_letters()
                return HTTPStatus.OK, _dump({"jobs": [_job_to_dict(j) for j in jobs]})

            if path == _JOBS_PATH:
                status = _parse_status_query(query)
                jobs = self._job_repo.list_by_status(status)
                return HTTPStatus.OK, _dump({"jobs": [_job_to_dict(j) for j in jobs]})

            if path.startswith(_JOBS_PATH + "/"):
                job_id = path[len(_JOBS_PATH) + 1 :]
                if not job_id or "/" in job_id:
                    return HTTPStatus.NOT_FOUND, _NOT_FOUND_BODY
                job = self._job_repo.get(job_id)
                if job is None:
                    return HTTPStatus.NOT_FOUND, _NOT_FOUND_BODY
                return HTTPStatus.OK, _dump(_job_to_dict(job))
        except InvalidRequestError as exc:
            _logger.warning("api.invalid_request", extra={"error": str(exc)})
            return HTTPStatus.BAD_REQUEST, _dump({"error": str(exc)})
        except JobError as exc:
            _logger.error("api.job_repository_error", extra={"error": str(exc)})
            return HTTPStatus.INTERNAL_SERVER_ERROR, _INTERNAL_ERROR_BODY

        return HTTPStatus.NOT_FOUND, _NOT_FOUND_BODY

    def handle_post(
        self, path: str, token: str | None, body: bytes
    ) -> tuple[HTTPStatus, bytes]:
        """POSTリクエスト1件分の処理本体(テスト容易化のためHTTPハンドラから分離)。"""
        try:
            self._authenticate(token)
        except InvalidTokenError:
            _logger.warning("api.invalid_token")
            return HTTPStatus.UNAUTHORIZED, _INVALID_TOKEN_BODY

        if path != _JOBS_PATH:
            return HTTPStatus.NOT_FOUND, _NOT_FOUND_BODY

        try:
            job_type, payload, max_attempts = _parse_enqueue_request(body)
            if max_attempts is None:
                job = self._job_repo.enqueue(job_type, payload)
            else:
                job = self._job_repo.enqueue(
                    job_type, payload, max_attempts=max_attempts
                )
        except InvalidRequestError as exc:
            _logger.warning("api.invalid_request", extra={"error": str(exc)})
            return HTTPStatus.BAD_REQUEST, _dump({"error": str(exc)})
        except JobError as exc:
            _logger.error("api.job_repository_error", extra={"error": str(exc)})
            return HTTPStatus.INTERNAL_SERVER_ERROR, _INTERNAL_ERROR_BODY

        _logger.info(
            "api.job_enqueued", extra={"job_id": job.id, "job_type": job.job_type.value}
        )
        return HTTPStatus.CREATED, _dump(_job_to_dict(job))

    def _authenticate(self, token: str | None) -> None:
        if not secrets.compare_digest(token or "", self._token):
            raise InvalidTokenError(
                f"{_TOKEN_HEADER}が設定済みのトークンと一致しません"
            )


def _parse_status_query(query: str) -> JobStatus:
    values = parse_qs(query).get("status")
    if not values:
        raise InvalidRequestError("クエリパラメータstatusは必須です")
    try:
        return JobStatus(values[0])
    except ValueError as exc:
        raise InvalidRequestError(f"不正なstatusです: {values[0]!r}") from exc


def _parse_enqueue_request(
    body: bytes,
) -> tuple[JobType, dict[str, Any], int | None]:
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise InvalidRequestError(
            f"リクエストボディがJSONとして不正です: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise InvalidRequestError(
            "リクエストボディはJSONオブジェクトである必要があります"
        )

    job_type_value = raw.get("job_type")
    if not isinstance(job_type_value, str):
        raise InvalidRequestError("job_typeは文字列で必須です")
    try:
        job_type = JobType(job_type_value)
    except ValueError as exc:
        raise InvalidRequestError(f"不正なjob_typeです: {job_type_value!r}") from exc

    payload = raw.get("payload")
    if not isinstance(payload, dict):
        raise InvalidRequestError("payloadはJSONオブジェクトで必須です")

    max_attempts_value = raw.get("max_attempts")
    max_attempts: int | None = None
    if max_attempts_value is not None:
        if (
            isinstance(max_attempts_value, bool)
            or not isinstance(max_attempts_value, int)
            or max_attempts_value <= 0
        ):
            raise InvalidRequestError("max_attemptsは正の整数である必要があります")
        max_attempts = max_attempts_value

    return job_type, payload, max_attempts


def _job_to_dict(job: Job) -> dict[str, Any]:
    """`Job`をJSONレスポンス用の辞書へ変換する(datetime/enumをJSON互換の型にする)。"""
    return {
        "id": job.id,
        "job_type": job.job_type.value,
        "status": job.status.value,
        "payload": job.payload,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "lease_owner": job.lease_owner,
        "lease_expires_at": _isoformat_or_none(job.lease_expires_at),
        "dead_letter_at": _isoformat_or_none(job.dead_letter_at),
    }


def _isoformat_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dump(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _make_handler_class(server: ApiServer) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            # 標準の`BaseHTTPRequestHandler`アクセスログ(stderr直接出力)は構造化ログの方針
            # (`logging_/`)に合わないため、`get_logger`経由に差し替える(`webhook/server.py`と同じ)
            _logger.debug("api.access", extra={"message": format % args})

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            status, body = server.handle_get(
                parsed.path, parsed.query, self.headers.get(_TOKEN_HEADER)
            )
            self._respond(status, body)

        def do_POST(self) -> None:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = 0
            body = self.rfile.read(content_length) if content_length > 0 else b""

            parsed = urlsplit(self.path)
            status, response_body = server.handle_post(
                parsed.path, self.headers.get(_TOKEN_HEADER), body
            )
            self._respond(status, response_body)

        def _respond(self, status: HTTPStatus, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _Handler


__all__ = ["ApiServer"]
