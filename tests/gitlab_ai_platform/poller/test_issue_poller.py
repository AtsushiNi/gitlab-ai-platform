import threading
from datetime import UTC, datetime

import pytest

from gitlab_ai_platform.gitlab_adapter import GitLabApiError, Issue
from gitlab_ai_platform.issue_store import (
    DuplicateIssueTicketError,
    IssueTicketStoreError,
)
from gitlab_ai_platform.issue_store.types import IssueTicketRecord
from gitlab_ai_platform.job import (
    DEFAULT_MAX_ATTEMPTS,
    Job,
    JobError,
    JobStatus,
    JobType,
)
from gitlab_ai_platform.poller import (
    DetectedIssue,
    IssuePoller,
    IssuePollError,
    ticket_issue_if_unprocessed,
)

_LABEL = "AI実装"


def _issue(project: str, iid: int) -> Issue:
    return Issue(
        project=project,
        iid=iid,
        title=f"Issue {iid}",
        description="",
        state="opened",
        author="alice",
        labels=(_LABEL,),
    )


class _FakeGitLabReader:
    """`GitLabReader`を満たすテスト用フェイク。読み取り以外の操作は持たない。"""

    def __init__(
        self,
        issues_by_project: dict[str, list[Issue]] | None = None,
        *,
        fail_projects: dict[str, Exception] | None = None,
    ) -> None:
        self._issues_by_project = issues_by_project or {}
        self._fail_projects = fail_projects or {}
        self.list_calls: list[tuple[str, tuple[str, ...]]] = []

    def get_version(self) -> str:
        return "17.0.0"

    def list_merge_requests(self, project, *, labels=(), state="opened"):
        raise NotImplementedError

    def get_merge_request(self, project: str, mr_iid: int):
        raise NotImplementedError

    def get_merge_request_diffs(self, project: str, mr_iid: int) -> list:
        raise NotImplementedError

    def list_merge_request_discussions(self, project: str, mr_iid: int) -> list:
        raise NotImplementedError

    def list_issues(
        self, project: str, *, labels: tuple[str, ...] = (), state: str = "opened"
    ) -> list[Issue]:
        self.list_calls.append((project, tuple(labels)))
        if project in self._fail_projects:
            raise self._fail_projects[project]
        return list(self._issues_by_project.get(project, []))

    def get_issue(self, project: str, issue_iid: int) -> Issue:
        raise NotImplementedError


class _FakeIssueTicketStore:
    """`IssueTicketStore`を満たすテスト用フェイク。`create`の挙動を差し込んで競合等を再現できる。"""

    def __init__(
        self,
        *,
        existing: set[tuple[str, int]] | None = None,
        create_side_effects: dict[tuple[str, int], Exception] | None = None,
    ) -> None:
        self._records: set[tuple[str, int]] = set(existing or set())
        self._create_side_effects = create_side_effects or {}
        self.find_calls: list[tuple[str, int]] = []
        self.create_calls: list[tuple[str, int]] = []

    def find(self, project: str, issue_iid: int) -> IssueTicketRecord | None:
        key = (project, issue_iid)
        self.find_calls.append(key)
        if key in self._records:
            return IssueTicketRecord(
                project=project, issue_iid=issue_iid, ticketed_at=datetime.now()
            )
        return None

    def create(self, project: str, issue_iid: int) -> IssueTicketRecord:
        key = (project, issue_iid)
        self.create_calls.append(key)
        if key in self._create_side_effects:
            raise self._create_side_effects[key]
        self._records.add(key)
        return IssueTicketRecord(
            project=project, issue_iid=issue_iid, ticketed_at=datetime.now()
        )

    def close(self) -> None:
        pass


class _FakeJobRepository:
    """`JobRepository`を満たすテスト用フェイク。`enqueue`の挙動を差し込んで障害を再現できる。"""

    def __init__(
        self, *, enqueue_side_effects: dict[int, Exception] | None = None
    ) -> None:
        self._jobs: dict[str, Job] = {}
        self._next_id = 1
        self._enqueue_side_effects = enqueue_side_effects or {}
        self.enqueue_calls: list[tuple[JobType, dict]] = []

    def enqueue(
        self,
        job_type: JobType,
        payload: dict,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> Job:
        call_index = len(self.enqueue_calls)
        self.enqueue_calls.append((job_type, payload))
        if call_index in self._enqueue_side_effects:
            raise self._enqueue_side_effects[call_index]

        job_id = f"job-{self._next_id}"
        self._next_id += 1
        now = datetime.now(UTC)
        job = Job(
            id=job_id,
            job_type=job_type,
            status=JobStatus.PENDING,
            payload=payload,
            result=None,
            error=None,
            created_at=now,
            updated_at=now,
            max_attempts=max_attempts,
        )
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def update_status(self, job_id, status, result=None, error=None) -> Job:
        raise NotImplementedError

    def list_by_status(self, status: JobStatus) -> list[Job]:
        return [job for job in self._jobs.values() if job.status == status]

    def close(self) -> None:
        pass

    def claim(self, worker_id, *, job_types=None, visibility_timeout_seconds=600):
        raise NotImplementedError

    def heartbeat(self, job_id, worker_id, *, visibility_timeout_seconds=600) -> Job:
        raise NotImplementedError

    def complete(self, job_id, worker_id, result=None) -> Job:
        raise NotImplementedError

    def fail(self, job_id, worker_id, error, *, retry=True) -> Job:
        raise NotImplementedError

    def list_dead_letters(self) -> list[Job]:
        return []


def test_poll_once_tickets_and_enqueues_unprocessed_issues_across_multiple_projects():
    adapter = _FakeGitLabReader(
        {
            "group/project-a": [_issue("group/project-a", 1)],
            "group/project-b": [_issue("group/project-b", 5)],
        }
    )
    store = _FakeIssueTicketStore()
    job_repo = _FakeJobRepository()
    poller = IssuePoller(
        adapter,
        store,
        job_repo,
        ["group/project-a", "group/project-b"],
        issue_label=_LABEL,
    )

    result = poller.poll_once()

    assert {(d.project, d.issue_iid) for d in result.created} == {
        ("group/project-a", 1),
        ("group/project-b", 5),
    }
    assert result.errors == ()
    assert set(store.create_calls) == {("group/project-a", 1), ("group/project-b", 5)}
    assert {(jt, p["project"], p["issue_iid"]) for jt, p in job_repo.enqueue_calls} == {
        (JobType.ISSUE_ANALYSIS, "group/project-a", 1),
        (JobType.ISSUE_ANALYSIS, "group/project-b", 5),
    }


def test_poll_once_passes_issue_label_to_adapter():
    adapter = _FakeGitLabReader({"group/project": []})
    store = _FakeIssueTicketStore()
    job_repo = _FakeJobRepository()
    poller = IssuePoller(
        adapter, store, job_repo, ["group/project"], issue_label=_LABEL
    )

    poller.poll_once()

    assert adapter.list_calls == [("group/project", (_LABEL,))]


def test_poll_once_skips_issues_already_ticketed():
    adapter = _FakeGitLabReader({"group/project": [_issue("group/project", 1)]})
    store = _FakeIssueTicketStore(existing={("group/project", 1)})
    job_repo = _FakeJobRepository()
    poller = IssuePoller(
        adapter, store, job_repo, ["group/project"], issue_label=_LABEL
    )

    result = poller.poll_once()

    assert result.created == ()
    assert store.create_calls == []
    assert job_repo.enqueue_calls == []


def test_poll_once_continues_after_one_project_scan_fails():
    adapter = _FakeGitLabReader(
        {"group/project-b": [_issue("group/project-b", 2)]},
        fail_projects={
            "group/project-a": GitLabApiError("接続エラー", status_code=500)
        },
    )
    store = _FakeIssueTicketStore()
    job_repo = _FakeJobRepository()
    poller = IssuePoller(
        adapter,
        store,
        job_repo,
        ["group/project-a", "group/project-b"],
        issue_label=_LABEL,
    )

    result = poller.poll_once()

    assert [d.project for d in result.created] == ["group/project-b"]
    assert result.errors == (
        IssuePollError(project="group/project-a", issue_iid=None, message="接続エラー"),
    )


def test_poll_once_ignores_duplicate_ticket_error_from_concurrent_ticketing():
    adapter = _FakeGitLabReader({"group/project": [_issue("group/project", 1)]})
    store = _FakeIssueTicketStore(
        create_side_effects={
            ("group/project", 1): DuplicateIssueTicketError("既に存在します")
        }
    )
    job_repo = _FakeJobRepository()
    poller = IssuePoller(
        adapter, store, job_repo, ["group/project"], issue_label=_LABEL
    )

    result = poller.poll_once()

    assert result.created == ()
    assert result.errors == ()
    assert job_repo.enqueue_calls == []


def test_poll_once_records_ticket_store_failure_and_continues():
    adapter = _FakeGitLabReader(
        {
            "group/project": [
                _issue("group/project", 1),
                _issue("group/project", 2),
            ]
        }
    )
    store = _FakeIssueTicketStore(
        create_side_effects={("group/project", 1): IssueTicketStoreError("DBロック中")}
    )
    job_repo = _FakeJobRepository()
    poller = IssuePoller(
        adapter, store, job_repo, ["group/project"], issue_label=_LABEL
    )

    result = poller.poll_once()

    assert [d.issue_iid for d in result.created] == [2]
    assert result.errors == (
        IssuePollError(project="group/project", issue_iid=1, message="DBロック中"),
    )


def test_poll_once_records_job_enqueue_failure_but_leaves_ticket_recorded():
    # 起票(Issue Ticket Store)は成功したがJob投入だけが失敗したケース。モジュールdocstring
    # に記載の通り、レコードは残ったままになり次回以降は再試行されない(意図的なリスク)
    adapter = _FakeGitLabReader({"group/project": [_issue("group/project", 1)]})
    store = _FakeIssueTicketStore()
    job_repo = _FakeJobRepository(enqueue_side_effects={0: JobError("DB書き込み失敗")})
    poller = IssuePoller(
        adapter, store, job_repo, ["group/project"], issue_label=_LABEL
    )

    result = poller.poll_once()

    assert result.created == ()
    assert result.errors == (
        IssuePollError(project="group/project", issue_iid=1, message="DB書き込み失敗"),
    )
    assert store.create_calls == [("group/project", 1)]
    # 次のサイクルでも再試行されないことを確認する
    result2 = poller.poll_once()
    assert result2.created == ()
    assert result2.errors == ()


def test_run_stops_when_stop_event_is_set_and_runs_poll_once_at_least_once():
    adapter = _FakeGitLabReader({"group/project": []})
    store = _FakeIssueTicketStore()
    job_repo = _FakeJobRepository()
    poller = IssuePoller(
        adapter, store, job_repo, ["group/project"], issue_label=_LABEL
    )
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


def test_run_calls_on_detected_for_each_created_issue_then_stops():
    adapter = _FakeGitLabReader(
        {
            "group/project-a": [_issue("group/project-a", 1)],
            "group/project-b": [_issue("group/project-b", 5)],
        }
    )
    store = _FakeIssueTicketStore()
    job_repo = _FakeJobRepository()
    poller = IssuePoller(
        adapter,
        store,
        job_repo,
        ["group/project-a", "group/project-b"],
        issue_label=_LABEL,
    )
    stop_event = threading.Event()

    detected: list[DetectedIssue] = []

    def _on_detected(issue: DetectedIssue) -> None:
        detected.append(issue)

    original_poll_once = poller.poll_once

    def _poll_once_then_stop():
        stop_event.set()
        return original_poll_once()

    poller.poll_once = _poll_once_then_stop  # type: ignore[method-assign]

    poller.run(interval_seconds=0, stop_event=stop_event, on_detected=_on_detected)

    assert {(d.project, d.issue_iid) for d in detected} == {
        ("group/project-a", 1),
        ("group/project-b", 5),
    }


def test_run_without_on_detected_does_not_require_a_callback():
    adapter = _FakeGitLabReader({"group/project": [_issue("group/project", 1)]})
    store = _FakeIssueTicketStore()
    job_repo = _FakeJobRepository()
    poller = IssuePoller(
        adapter, store, job_repo, ["group/project"], issue_label=_LABEL
    )
    stop_event = threading.Event()

    original_poll_once = poller.poll_once

    def _poll_once_then_stop():
        stop_event.set()
        return original_poll_once()

    poller.poll_once = _poll_once_then_stop  # type: ignore[method-assign]

    poller.run(interval_seconds=0, stop_event=stop_event)


def test_run_propagates_exception_from_on_detected():
    adapter = _FakeGitLabReader({"group/project": [_issue("group/project", 1)]})
    store = _FakeIssueTicketStore()
    job_repo = _FakeJobRepository()
    poller = IssuePoller(
        adapter, store, job_repo, ["group/project"], issue_label=_LABEL
    )

    def _on_detected(issue: DetectedIssue) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        poller.run(interval_seconds=0, on_detected=_on_detected)


# `ticket_issue_if_unprocessed`はIssuePoller.poll_once専用のロジックではなく、契約
# (戻り値・Issue Ticket Store/Job Repositoryとのやり取り)そのものを直接検証する。
def test_ticket_issue_if_unprocessed_creates_record_and_enqueues_job():
    store = _FakeIssueTicketStore()
    job_repo = _FakeJobRepository()

    outcome = ticket_issue_if_unprocessed(store, job_repo, "group/project", 1)

    assert isinstance(outcome, DetectedIssue)
    assert outcome.project == "group/project"
    assert outcome.issue_iid == 1
    assert store.create_calls == [("group/project", 1)]
    assert job_repo.enqueue_calls == [
        (JobType.ISSUE_ANALYSIS, {"project": "group/project", "issue_iid": 1})
    ]
    assert job_repo.get(outcome.job_id) is not None


def test_ticket_issue_if_unprocessed_returns_none_when_already_recorded():
    store = _FakeIssueTicketStore(existing={("group/project", 1)})
    job_repo = _FakeJobRepository()

    outcome = ticket_issue_if_unprocessed(store, job_repo, "group/project", 1)

    assert outcome is None
    assert store.create_calls == []
    assert job_repo.enqueue_calls == []


def test_ticket_issue_if_unprocessed_ignores_duplicate_ticket_error_from_concurrent_caller():
    store = _FakeIssueTicketStore(
        create_side_effects={
            ("group/project", 1): DuplicateIssueTicketError("既に存在します")
        }
    )
    job_repo = _FakeJobRepository()

    outcome = ticket_issue_if_unprocessed(store, job_repo, "group/project", 1)

    assert outcome is None
    assert job_repo.enqueue_calls == []


def test_ticket_issue_if_unprocessed_returns_poll_error_on_ticket_store_failure():
    store = _FakeIssueTicketStore(
        create_side_effects={("group/project", 1): IssueTicketStoreError("DBロック中")}
    )
    job_repo = _FakeJobRepository()

    outcome = ticket_issue_if_unprocessed(store, job_repo, "group/project", 1)

    assert outcome == IssuePollError(
        project="group/project", issue_iid=1, message="DBロック中"
    )
    assert job_repo.enqueue_calls == []


def test_ticket_issue_if_unprocessed_returns_poll_error_on_job_enqueue_failure():
    store = _FakeIssueTicketStore()
    job_repo = _FakeJobRepository(enqueue_side_effects={0: JobError("DB書き込み失敗")})

    outcome = ticket_issue_if_unprocessed(store, job_repo, "group/project", 1)

    assert outcome == IssuePollError(
        project="group/project", issue_iid=1, message="DB書き込み失敗"
    )
    # チケットは残る(モジュールdocstringに記載の意図的なリスク)
    assert store.create_calls == [("group/project", 1)]
