import threading

import pytest

from gitlab_ai_platform.gitlab_adapter import GitLabApiError, MergeRequest
from gitlab_ai_platform.poller import DetectedReview, MrPoller, PollError
from gitlab_ai_platform.store import DuplicateReviewError, ReviewRecord, ReviewStatus, StateStoreError

_LABEL = "レビュー待ち"


def _mr(project: str, iid: int, sha: str) -> MergeRequest:
    return MergeRequest(
        project=project,
        iid=iid,
        title=f"MR {iid}",
        description="",
        state="opened",
        source_branch=f"feature-{iid}",
        target_branch="main",
        sha=sha,
        author="alice",
        labels=(_LABEL,),
    )


class _FakeGitLabReader:
    """`GitLabReader`を満たすテスト用フェイク。読み取り以外の操作は持たない。"""

    def __init__(
        self,
        merge_requests_by_project: dict[str, list[MergeRequest]] | None = None,
        *,
        fail_projects: dict[str, Exception] | None = None,
    ) -> None:
        self._merge_requests_by_project = merge_requests_by_project or {}
        self._fail_projects = fail_projects or {}
        self.list_calls: list[tuple[str, tuple[str, ...]]] = []

    def get_version(self) -> str:
        return "17.0.0"

    def list_merge_requests(
        self, project: str, *, labels: tuple[str, ...] = (), state: str = "opened"
    ) -> list[MergeRequest]:
        self.list_calls.append((project, tuple(labels)))
        if project in self._fail_projects:
            raise self._fail_projects[project]
        return list(self._merge_requests_by_project.get(project, []))

    def get_merge_request(self, project: str, mr_iid: int) -> MergeRequest:
        raise NotImplementedError

    def get_merge_request_diffs(self, project: str, mr_iid: int) -> list:
        raise NotImplementedError

    def list_merge_request_discussions(self, project: str, mr_iid: int) -> list:
        raise NotImplementedError


class _FakeStateStore:
    """`StateStore`を満たすテスト用フェイク。`create`の挙動を差し込んで競合等を再現できる。"""

    def __init__(
        self,
        *,
        existing: set[tuple[str, int, str]] | None = None,
        create_side_effects: dict[tuple[str, int, str], Exception] | None = None,
    ) -> None:
        self._records: set[tuple[str, int, str]] = set(existing or set())
        self._create_side_effects = create_side_effects or {}
        self.find_calls: list[tuple[str, int, str]] = []
        self.create_calls: list[tuple[str, int, str]] = []

    def find(self, project: str, mr_iid: int, commit_sha: str) -> ReviewRecord | None:
        key = (project, mr_iid, commit_sha)
        self.find_calls.append(key)
        if key in self._records:
            return ReviewRecord(
                project=project, mr_iid=mr_iid, commit_sha=commit_sha, status=ReviewStatus.PENDING
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
        return ReviewRecord(project=project, mr_iid=mr_iid, commit_sha=commit_sha, status=status)

    def update_status(self, *args, **kwargs) -> ReviewRecord:
        raise NotImplementedError

    def close(self) -> None:
        pass


def test_poll_once_tickets_unprocessed_mrs_across_multiple_projects():
    adapter = _FakeGitLabReader(
        {
            "group/project-a": [_mr("group/project-a", 1, "sha-a1")],
            "group/project-b": [_mr("group/project-b", 5, "sha-b5")],
        }
    )
    store = _FakeStateStore()
    poller = MrPoller(adapter, store, ["group/project-a", "group/project-b"], review_label=_LABEL)

    result = poller.poll_once()

    assert set(result.created) == {
        DetectedReview(project="group/project-a", mr_iid=1, commit_sha="sha-a1"),
        DetectedReview(project="group/project-b", mr_iid=5, commit_sha="sha-b5"),
    }
    assert result.errors == ()
    assert set(store.create_calls) == {("group/project-a", 1, "sha-a1"), ("group/project-b", 5, "sha-b5")}


def test_poll_once_passes_review_label_to_adapter():
    adapter = _FakeGitLabReader({"group/project": []})
    store = _FakeStateStore()
    poller = MrPoller(adapter, store, ["group/project"], review_label=_LABEL)

    poller.poll_once()

    assert adapter.list_calls == [("group/project", (_LABEL,))]


def test_poll_once_skips_mrs_already_recorded_in_store():
    adapter = _FakeGitLabReader({"group/project": [_mr("group/project", 1, "sha-1")]})
    store = _FakeStateStore(existing={("group/project", 1, "sha-1")})
    poller = MrPoller(adapter, store, ["group/project"], review_label=_LABEL)

    result = poller.poll_once()

    assert result.created == ()
    assert store.create_calls == []


def test_poll_once_tickets_new_commit_sha_as_separate_record():
    # 同一MRでも新しいpush(新commit_sha)があれば再レビュー対象として検出する
    adapter = _FakeGitLabReader({"group/project": [_mr("group/project", 1, "sha-2")]})
    store = _FakeStateStore(existing={("group/project", 1, "sha-1")})
    poller = MrPoller(adapter, store, ["group/project"], review_label=_LABEL)

    result = poller.poll_once()

    assert result.created == (DetectedReview(project="group/project", mr_iid=1, commit_sha="sha-2"),)


def test_poll_once_continues_after_one_project_scan_fails():
    adapter = _FakeGitLabReader(
        {"group/project-b": [_mr("group/project-b", 2, "sha-b2")]},
        fail_projects={"group/project-a": GitLabApiError("接続エラー", status_code=500)},
    )
    store = _FakeStateStore()
    poller = MrPoller(adapter, store, ["group/project-a", "group/project-b"], review_label=_LABEL)

    result = poller.poll_once()

    assert result.created == (DetectedReview(project="group/project-b", mr_iid=2, commit_sha="sha-b2"),)
    assert result.errors == (PollError(project="group/project-a", mr_iid=None, message="接続エラー"),)


def test_poll_once_ignores_duplicate_review_error_from_concurrent_ticketing():
    adapter = _FakeGitLabReader({"group/project": [_mr("group/project", 1, "sha-1")]})
    store = _FakeStateStore(
        create_side_effects={
            ("group/project", 1, "sha-1"): DuplicateReviewError("既に存在します")
        }
    )
    poller = MrPoller(adapter, store, ["group/project"], review_label=_LABEL)

    result = poller.poll_once()

    assert result.created == ()
    assert result.errors == ()


def test_poll_once_records_state_store_failure_and_continues():
    adapter = _FakeGitLabReader(
        {
            "group/project": [
                _mr("group/project", 1, "sha-1"),
                _mr("group/project", 2, "sha-2"),
            ]
        }
    )
    store = _FakeStateStore(
        create_side_effects={("group/project", 1, "sha-1"): StateStoreError("DBロック中")}
    )
    poller = MrPoller(adapter, store, ["group/project"], review_label=_LABEL)

    result = poller.poll_once()

    assert result.created == (DetectedReview(project="group/project", mr_iid=2, commit_sha="sha-2"),)
    assert result.errors == (
        PollError(project="group/project", mr_iid=1, message="DBロック中"),
    )


def test_run_stops_when_stop_event_is_set_and_runs_poll_once_at_least_once():
    adapter = _FakeGitLabReader({"group/project": []})
    store = _FakeStateStore()
    poller = MrPoller(adapter, store, ["group/project"], review_label=_LABEL)
    stop_event = threading.Event()

    call_count = 0
    original_poll_once = poller.poll_once

    def _poll_once_then_stop():
        nonlocal call_count
        call_count += 1
        stop_event.set()
        return original_poll_once()

    poller.poll_once = _poll_once_then_stop  # type: ignore[method-assign]

    poller.run(interval_seconds=0, stop_event=stop_event)

    assert call_count == 1


def test_run_calls_on_detected_for_each_created_review_then_stops():
    adapter = _FakeGitLabReader(
        {
            "group/project-a": [_mr("group/project-a", 1, "sha-a1")],
            "group/project-b": [_mr("group/project-b", 5, "sha-b5")],
        }
    )
    store = _FakeStateStore()
    poller = MrPoller(
        adapter, store, ["group/project-a", "group/project-b"], review_label=_LABEL
    )
    stop_event = threading.Event()

    detected: list[DetectedReview] = []

    def _on_detected(review: DetectedReview) -> None:
        detected.append(review)

    original_poll_once = poller.poll_once

    def _poll_once_then_stop():
        stop_event.set()
        return original_poll_once()

    poller.poll_once = _poll_once_then_stop  # type: ignore[method-assign]

    poller.run(interval_seconds=0, stop_event=stop_event, on_detected=_on_detected)

    assert set(detected) == {
        DetectedReview(project="group/project-a", mr_iid=1, commit_sha="sha-a1"),
        DetectedReview(project="group/project-b", mr_iid=5, commit_sha="sha-b5"),
    }


def test_run_without_on_detected_does_not_require_a_callback():
    # on_detected省略時(既定None)でも例外にならず、単に呼び出されないだけであることの確認
    adapter = _FakeGitLabReader({"group/project": [_mr("group/project", 1, "sha-1")]})
    store = _FakeStateStore()
    poller = MrPoller(adapter, store, ["group/project"], review_label=_LABEL)
    stop_event = threading.Event()

    original_poll_once = poller.poll_once

    def _poll_once_then_stop():
        stop_event.set()
        return original_poll_once()

    poller.poll_once = _poll_once_then_stop  # type: ignore[method-assign]

    poller.run(interval_seconds=0, stop_event=stop_event)


def test_run_propagates_exception_from_on_detected():
    # レビュー実行失敗を握りつぶすかどうかは呼び出し側(CLI watchモード)の判断に委ねるため、
    # on_detectedが送出した例外はrunの外へそのまま伝播する
    adapter = _FakeGitLabReader({"group/project": [_mr("group/project", 1, "sha-1")]})
    store = _FakeStateStore()
    poller = MrPoller(adapter, store, ["group/project"], review_label=_LABEL)

    def _on_detected(review: DetectedReview) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        poller.run(interval_seconds=0, on_detected=_on_detected)
