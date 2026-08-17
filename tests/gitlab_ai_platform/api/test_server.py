from __future__ import annotations

import json
from http import HTTPStatus
from http.client import HTTPConnection

import pytest

from gitlab_ai_platform.api.server import ApiServer
from gitlab_ai_platform.job.errors import JobError
from gitlab_ai_platform.job.protocol import JobStatus, JobType
from gitlab_ai_platform.job.sqlite import SqliteJobRepository

_TOKEN = "test-api-token"


class _RecordingServer:
    """`ApiServer`を実際に起動し、`http.client`でリクエストを送るテスト用ラッパー。

    `webhook/test_server.py`の`_RecordingServer`と同じ方針。
    """

    def __init__(self, job_repo, *, token: str = _TOKEN):
        self.server = ApiServer(job_repo, token=token, host="127.0.0.1", port=0)

    def __enter__(self) -> _RecordingServer:
        self.server.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.server.stop()

    def post(
        self, path: str, *, body: dict | bytes, token: str | None = _TOKEN
    ) -> tuple[int, dict]:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        return self._request("POST", path, body=raw, token=token)

    def get(self, path: str, *, token: str | None = _TOKEN) -> tuple[int, dict]:
        return self._request("GET", path, body=None, token=token)

    def _request(
        self, method: str, path: str, *, body: bytes | None, token: str | None
    ) -> tuple[int, dict]:
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            headers = {"Content-Type": "application/json"}
            if token is not None:
                headers["X-Api-Token"] = token
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
        finally:
            conn.close()


class _FailingJobRepository:
    """`JobError`を送出することだけを目的にした最小限の手書きフェイク。"""

    def enqueue(self, job_type, payload, *, max_attempts=3):
        raise JobError("DB接続エラー")

    def get(self, job_id):
        raise JobError("DB接続エラー")

    def list_by_status(self, status):
        raise JobError("DB接続エラー")

    def list_dead_letters(self):
        raise JobError("DB接続エラー")

    def close(self) -> None:
        pass


@pytest.fixture
def job_repo():
    repo = SqliteJobRepository(":memory:")
    yield repo
    repo.close()


def test_post_jobs_enqueues_and_returns_created(job_repo):
    with _RecordingServer(job_repo) as srv:
        status, body = srv.post(
            "/jobs",
            body={"job_type": "review", "payload": {"project": "g/p", "mr_iid": 1}},
        )

    assert status == HTTPStatus.CREATED
    assert body["job_type"] == "review"
    assert body["status"] == "pending"
    assert body["payload"] == {"project": "g/p", "mr_iid": 1}
    assert body["max_attempts"] == 3
    assert job_repo.get(body["id"]) is not None


def test_post_jobs_respects_max_attempts(job_repo):
    with _RecordingServer(job_repo) as srv:
        status, body = srv.post(
            "/jobs",
            body={
                "job_type": "review",
                "payload": {"project": "g/p", "mr_iid": 1},
                "max_attempts": 7,
            },
        )

    assert status == HTTPStatus.CREATED
    assert body["max_attempts"] == 7


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"payload": {}},
        {"job_type": 123, "payload": {}},
        {"job_type": "not-a-real-type", "payload": {}},
        {"job_type": "review"},
        {"job_type": "review", "payload": "not-an-object"},
        {"job_type": "review", "payload": {}, "max_attempts": 0},
        {"job_type": "review", "payload": {}, "max_attempts": -1},
        {"job_type": "review", "payload": {}, "max_attempts": "3"},
        {"job_type": "review", "payload": {}, "max_attempts": True},
    ],
)
def test_post_jobs_rejects_invalid_request(job_repo, body):
    with _RecordingServer(job_repo) as srv:
        status, _ = srv.post("/jobs", body=body)

    assert status == HTTPStatus.BAD_REQUEST


def test_post_jobs_rejects_invalid_json(job_repo):
    with _RecordingServer(job_repo) as srv:
        status, _ = srv.post("/jobs", body=b"not json")

    assert status == HTTPStatus.BAD_REQUEST


def test_get_job_by_id_returns_job(job_repo):
    job = job_repo.enqueue(JobType.REVIEW, {"project": "g/p", "mr_iid": 1})
    with _RecordingServer(job_repo) as srv:
        status, body = srv.get(f"/jobs/{job.id}")

    assert status == HTTPStatus.OK
    assert body["id"] == job.id
    assert body["result"] is None
    assert body["error"] is None


def test_get_job_by_id_not_found(job_repo):
    with _RecordingServer(job_repo) as srv:
        status, _ = srv.get("/jobs/does-not-exist")

    assert status == HTTPStatus.NOT_FOUND


def test_get_jobs_by_status_filters(job_repo):
    first = job_repo.enqueue(JobType.REVIEW, {"project": "g/p", "mr_iid": 1})
    second = job_repo.enqueue(JobType.REVIEW, {"project": "g/p", "mr_iid": 2})
    claimed = job_repo.claim(
        "worker-1"
    )  # 作成順(created_at昇順)で1件をRUNNINGへ遷移させる
    assert claimed is not None and claimed.id == first.id

    with _RecordingServer(job_repo) as srv:
        status, body = srv.get("/jobs?status=pending")

    assert status == HTTPStatus.OK
    # PENDINGのまま残っているのはclaimされなかった2件目のみ
    assert [job["id"] for job in body["jobs"]] == [second.id]


def test_get_jobs_requires_status_query_param(job_repo):
    with _RecordingServer(job_repo) as srv:
        status, _ = srv.get("/jobs")

    assert status == HTTPStatus.BAD_REQUEST


def test_get_jobs_rejects_invalid_status_value(job_repo):
    with _RecordingServer(job_repo) as srv:
        status, _ = srv.get("/jobs?status=not-a-real-status")

    assert status == HTTPStatus.BAD_REQUEST


def test_get_dead_letters_empty_by_default(job_repo):
    with _RecordingServer(job_repo) as srv:
        status, body = srv.get("/jobs/dead-letters")

    assert status == HTTPStatus.OK
    assert body == {"jobs": []}


def test_get_dead_letters_returns_dead_lettered_jobs(job_repo):
    job = job_repo.enqueue(JobType.REVIEW, {"project": "g/p", "mr_iid": 1})
    claimed = job_repo.claim("worker-1")
    assert claimed is not None
    job_repo.fail(job.id, "worker-1", "恒久的なエラー", retry=False)

    with _RecordingServer(job_repo) as srv:
        status, body = srv.get("/jobs/dead-letters")

    assert status == HTTPStatus.OK
    assert [j["id"] for j in body["jobs"]] == [job.id]
    assert body["jobs"][0]["status"] == JobStatus.FAILED.value
    assert body["jobs"][0]["dead_letter_at"] is not None


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_missing_or_wrong_token_is_rejected(job_repo, token):
    with _RecordingServer(job_repo) as srv:
        get_status, _ = srv.get("/jobs/dead-letters", token=token)
        post_status, _ = srv.post(
            "/jobs", body={"job_type": "review", "payload": {}}, token=token
        )

    assert get_status == HTTPStatus.UNAUTHORIZED
    assert post_status == HTTPStatus.UNAUTHORIZED


def test_unknown_path_returns_not_found(job_repo):
    with _RecordingServer(job_repo) as srv:
        get_status, _ = srv.get("/unknown")
        post_status, _ = srv.post("/unknown", body={})

    assert get_status == HTTPStatus.NOT_FOUND
    assert post_status == HTTPStatus.NOT_FOUND


def test_job_repository_error_returns_internal_server_error():
    repo = _FailingJobRepository()
    with _RecordingServer(repo) as srv:
        get_status, _ = srv.get("/jobs?status=pending")
        dead_letter_status, _ = srv.get("/jobs/dead-letters")
        get_by_id_status, _ = srv.get("/jobs/some-id")
        post_status, _ = srv.post("/jobs", body={"job_type": "review", "payload": {}})

    assert get_status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert dead_letter_status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert get_by_id_status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert post_status == HTTPStatus.INTERNAL_SERVER_ERROR


def test_start_and_stop_are_idempotent_enough_to_call_stop_without_requests(job_repo):
    server = ApiServer(job_repo, token=_TOKEN, host="127.0.0.1", port=0)
    server.start()
    server.stop()
