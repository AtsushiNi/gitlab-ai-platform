from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.client import HTTPConnection

import pytest

from gitlab_ai_platform.poller import DetectedReview
from gitlab_ai_platform.store import DuplicateReviewError, ReviewRecord, ReviewStatus
from gitlab_ai_platform.store.errors import StateStoreError
from gitlab_ai_platform.webhook.server import WebhookServer

_LABEL = "レビュー待ち"
_SECRET = "whsec-test-secret"
_PATH = "/webhook"


class _FakeStateStore:
    """`StateStore`を満たすテスト用フェイク(`tests/.../poller/test_poller.py`と同じ方針)。"""

    def __init__(
        self,
        *,
        existing: set[tuple[str, int, str]] | None = None,
        create_side_effects: dict[tuple[str, int, str], Exception] | None = None,
    ) -> None:
        self._records: set[tuple[str, int, str]] = set(existing or set())
        self._create_side_effects = create_side_effects or {}
        self.create_calls: list[tuple[str, int, str]] = []

    def find(self, project: str, mr_iid: int, commit_sha: str) -> ReviewRecord | None:
        key = (project, mr_iid, commit_sha)
        if key in self._records:
            return ReviewRecord(
                project=project,
                mr_iid=mr_iid,
                commit_sha=commit_sha,
                status=ReviewStatus.PENDING,
            )
        return None

    def create(
        self,
        project: str,
        mr_iid: int,
        commit_sha: str,
        *,
        status: ReviewStatus = ReviewStatus.PENDING,
    ) -> ReviewRecord:
        key = (project, mr_iid, commit_sha)
        self.create_calls.append(key)
        if key in self._create_side_effects:
            raise self._create_side_effects[key]
        self._records.add(key)
        return ReviewRecord(
            project=project, mr_iid=mr_iid, commit_sha=commit_sha, status=status
        )

    def update_status(self, *args, **kwargs) -> ReviewRecord:
        raise NotImplementedError

    def close(self) -> None:
        pass


def _merge_request_payload(
    *,
    state: str = "opened",
    iid: int = 1,
    project_path: str = "group/project",
    commit_sha: str = "sha-1",
    labels: list[dict] | None = None,
) -> dict:
    return {
        "object_kind": "merge_request",
        "object_attributes": {
            "iid": iid,
            "state": state,
            "last_commit": {"id": commit_sha},
        },
        "project": {"path_with_namespace": project_path},
        "labels": labels if labels is not None else [{"title": _LABEL}],
    }


class _RecordingServer:
    """`WebhookServer`を実際に起動し、`http.client`でリクエストを送るテスト用ラッパー。"""

    def __init__(
        self, store, *, review_label: str = _LABEL, secret_token: str = _SECRET
    ):
        self.detected: list[DetectedReview] = []
        self._lock = threading.Lock()
        self.server = WebhookServer(
            store,
            review_label=review_label,
            secret_token=secret_token,
            host="127.0.0.1",
            port=0,
            path=_PATH,
            on_detected=self._on_detected,
        )

    def _on_detected(self, review: DetectedReview) -> None:
        with self._lock:
            self.detected.append(review)

    def __enter__(self) -> _RecordingServer:
        self.server.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.server.stop()

    def post(
        self,
        *,
        body: bytes,
        event: str | None = "Merge Request Hook",
        token: str | None = _SECRET,
        path: str = _PATH,
    ) -> tuple[int, dict]:
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            headers = {"Content-Type": "application/json"}
            if event is not None:
                headers["X-Gitlab-Event"] = event
            if token is not None:
                headers["X-Gitlab-Token"] = token
            conn.request("POST", path, body=body, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
        finally:
            conn.close()


def test_valid_merge_request_event_tickets_review_and_calls_on_detected():
    store = _FakeStateStore()
    with _RecordingServer(store) as srv:
        status, body = srv.post(
            body=json.dumps(_merge_request_payload()).encode("utf-8")
        )

    assert status == HTTPStatus.ACCEPTED
    assert body == {"status": "ticketed"}
    assert srv.detected == [
        DetectedReview(project="group/project", mr_iid=1, commit_sha="sha-1")
    ]
    assert store.create_calls == [("group/project", 1, "sha-1")]


def test_invalid_secret_token_is_rejected_and_does_not_ticket():
    store = _FakeStateStore()
    with _RecordingServer(store) as srv:
        status, _ = srv.post(
            body=json.dumps(_merge_request_payload()).encode("utf-8"),
            token="wrong-token",
        )

    assert status == HTTPStatus.UNAUTHORIZED
    assert srv.detected == []
    assert store.create_calls == []


def test_missing_secret_token_header_is_rejected():
    store = _FakeStateStore()
    with _RecordingServer(store) as srv:
        status, _ = srv.post(
            body=json.dumps(_merge_request_payload()).encode("utf-8"), token=None
        )

    assert status == HTTPStatus.UNAUTHORIZED


def test_non_merge_request_event_header_is_ignored():
    # GitLab側でPush Hookも誤って有効化していた場合を想定。エラーにはしない
    store = _FakeStateStore()
    with _RecordingServer(store) as srv:
        status, body = srv.post(
            body=json.dumps({"object_kind": "push"}).encode("utf-8"),
            event="Push Hook",
        )

    assert status == HTTPStatus.OK
    assert body == {"status": "ignored"}
    assert srv.detected == []


def test_invalid_json_body_returns_bad_request():
    store = _FakeStateStore()
    with _RecordingServer(store) as srv:
        status, _ = srv.post(body=b"not json")

    assert status == HTTPStatus.BAD_REQUEST


def test_malformed_merge_request_payload_returns_bad_request():
    store = _FakeStateStore()
    with _RecordingServer(store) as srv:
        # object_attributesを欠いた不正なペイロード
        status, _ = srv.post(body=json.dumps({"object_kind": "merge_request"}).encode())

    assert status == HTTPStatus.BAD_REQUEST


def test_merge_request_without_review_label_is_ignored():
    store = _FakeStateStore()
    with _RecordingServer(store) as srv:
        status, body = srv.post(
            body=json.dumps(
                _merge_request_payload(labels=[{"title": "other-label"}])
            ).encode("utf-8")
        )

    assert status == HTTPStatus.OK
    assert body == {"status": "ignored"}
    assert store.create_calls == []


def test_closed_merge_request_is_ignored():
    store = _FakeStateStore()
    with _RecordingServer(store) as srv:
        status, body = srv.post(
            body=json.dumps(_merge_request_payload(state="closed")).encode("utf-8")
        )

    assert status == HTTPStatus.OK
    assert body == {"status": "ignored"}
    assert store.create_calls == []


def test_duplicate_event_for_already_ticketed_commit_is_ignored():
    # Pollerが既に起票済みの(project, mr_iid, commit_sha)をWebhookが検出した場合、
    # 二重起票せず"duplicate"として無視する(重複起票の防止、docs/adr/0017)
    store = _FakeStateStore(existing={("group/project", 1, "sha-1")})
    with _RecordingServer(store) as srv:
        status, body = srv.post(
            body=json.dumps(_merge_request_payload()).encode("utf-8")
        )

    assert status == HTTPStatus.OK
    assert body == {"status": "duplicate"}
    assert srv.detected == []
    assert store.create_calls == []


def test_concurrent_duplicate_review_error_from_create_is_treated_as_duplicate():
    store = _FakeStateStore(
        create_side_effects={
            ("group/project", 1, "sha-1"): DuplicateReviewError("既に存在します")
        }
    )
    with _RecordingServer(store) as srv:
        status, body = srv.post(
            body=json.dumps(_merge_request_payload()).encode("utf-8")
        )

    assert status == HTTPStatus.OK
    assert body == {"status": "duplicate"}
    assert srv.detected == []


def test_state_store_failure_returns_internal_server_error():
    store = _FakeStateStore(
        create_side_effects={
            ("group/project", 1, "sha-1"): StateStoreError("DBロック中")
        }
    )
    with _RecordingServer(store) as srv:
        status, _ = srv.post(body=json.dumps(_merge_request_payload()).encode("utf-8"))

    assert status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert srv.detected == []


def test_unknown_path_returns_not_found():
    store = _FakeStateStore()
    with _RecordingServer(store) as srv:
        status, _ = srv.post(
            body=json.dumps(_merge_request_payload()).encode("utf-8"),
            path="/other-path",
        )

    assert status == HTTPStatus.NOT_FOUND


def test_start_and_stop_are_idempotent_enough_to_call_stop_without_requests():
    store = _FakeStateStore()
    server = WebhookServer(
        store,
        review_label=_LABEL,
        secret_token=_SECRET,
        host="127.0.0.1",
        port=0,
        path=_PATH,
        on_detected=lambda review: None,
    )
    server.start()
    server.stop()


@pytest.mark.parametrize(
    "payload_labels",
    [[{"title": _LABEL}], [{"title": _LABEL}, {"title": "other"}]],
)
def test_review_label_match_is_order_independent(payload_labels):
    store = _FakeStateStore()
    with _RecordingServer(store) as srv:
        status, _ = srv.post(
            body=json.dumps(_merge_request_payload(labels=payload_labels)).encode(
                "utf-8"
            )
        )

    assert status == HTTPStatus.ACCEPTED
