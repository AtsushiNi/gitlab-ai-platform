import threading

import pytest

from gitlab_ai_platform.job import (
    InvalidJobTransitionError,
    JobError,
    JobNotFoundError,
    JobStatus,
    JobType,
    SqliteJobRepository,
)


@pytest.fixture
def repo():
    # 実DBには繋がず、テストごとに独立したインメモリDBを使う(CLAUDE.mdのテスト方針)。
    r = SqliteJobRepository(":memory:")
    yield r
    r.close()


def test_enqueue_creates_pending_job(repo):
    job = repo.enqueue(JobType.REVIEW, {"project": "group/project", "mr_iid": 1})

    assert job.job_type == JobType.REVIEW
    assert job.status == JobStatus.PENDING
    assert job.payload == {"project": "group/project", "mr_iid": 1}
    assert job.result is None
    assert job.error is None
    assert job.id


def test_enqueue_then_get_returns_same_job(repo):
    created = repo.enqueue(JobType.REVIEW, {"project": "group/project", "mr_iid": 1})

    found = repo.get(created.id)

    assert found == created


def test_get_returns_none_for_unknown_job(repo):
    assert repo.get("unknown-id") is None


def test_enqueue_generates_distinct_ids(repo):
    first = repo.enqueue(JobType.REVIEW, {"mr_iid": 1})
    second = repo.enqueue(JobType.REVIEW, {"mr_iid": 2})

    assert first.id != second.id


@pytest.mark.parametrize("job_type", list(JobType))
def test_enqueue_accepts_all_reserved_job_types(repo, job_type):
    # ADR-0016: review以外は現時点でRunnerが処理できないが、値としては予約済み
    job = repo.enqueue(job_type, {})

    assert job.job_type == job_type


def test_update_status_pending_to_running(repo):
    job = repo.enqueue(JobType.REVIEW, {})

    updated = repo.update_status(job.id, JobStatus.RUNNING)

    assert updated.status == JobStatus.RUNNING
    assert repo.get(job.id).status == JobStatus.RUNNING


def test_update_status_running_to_done_persists_result(repo):
    job = repo.enqueue(JobType.REVIEW, {})
    repo.update_status(job.id, JobStatus.RUNNING)

    updated = repo.update_status(
        job.id, JobStatus.DONE, result={"result_path": "reviews/x"}
    )

    assert updated.status == JobStatus.DONE
    assert updated.result == {"result_path": "reviews/x"}
    assert repo.get(job.id).result == {"result_path": "reviews/x"}


def test_update_status_running_to_failed_persists_error(repo):
    job = repo.enqueue(JobType.REVIEW, {})
    repo.update_status(job.id, JobStatus.RUNNING)

    updated = repo.update_status(job.id, JobStatus.FAILED, error="boom")

    assert updated.status == JobStatus.FAILED
    assert updated.error == "boom"


def test_update_status_running_to_waiting_human_and_back(repo):
    job = repo.enqueue(JobType.REVIEW, {})
    repo.update_status(job.id, JobStatus.RUNNING)

    waiting = repo.update_status(job.id, JobStatus.WAITING_HUMAN)
    assert waiting.status == JobStatus.WAITING_HUMAN

    resumed = repo.update_status(job.id, JobStatus.RUNNING)
    assert resumed.status == JobStatus.RUNNING


def test_update_status_waiting_human_to_failed(repo):
    job = repo.enqueue(JobType.REVIEW, {})
    repo.update_status(job.id, JobStatus.RUNNING)
    repo.update_status(job.id, JobStatus.WAITING_HUMAN)

    updated = repo.update_status(job.id, JobStatus.FAILED)

    assert updated.status == JobStatus.FAILED


def test_update_status_without_result_or_error_preserves_existing_values(repo):
    # result/errorを省略した更新(例: DONE経由でない状態遷移のみ)が、既に記録済みの値を
    # 意図せずNULLへ巻き戻さないこと(store/sqlite.pyのCOALESCE方針と同じ)
    job = repo.enqueue(JobType.REVIEW, {})
    repo.update_status(job.id, JobStatus.RUNNING)
    repo.update_status(job.id, JobStatus.WAITING_HUMAN)
    repo.update_status(job.id, JobStatus.RUNNING)

    updated = repo.update_status(job.id, JobStatus.DONE, result={"x": 1})
    assert updated.result == {"x": 1}


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (JobStatus.PENDING, JobStatus.DONE),
        (JobStatus.PENDING, JobStatus.FAILED),
        (JobStatus.PENDING, JobStatus.WAITING_HUMAN),
        (JobStatus.PENDING, JobStatus.PENDING),
        (JobStatus.RUNNING, JobStatus.PENDING),
        (JobStatus.RUNNING, JobStatus.RUNNING),
        (JobStatus.WAITING_HUMAN, JobStatus.PENDING),
        (JobStatus.WAITING_HUMAN, JobStatus.DONE),
        (JobStatus.WAITING_HUMAN, JobStatus.WAITING_HUMAN),
        (JobStatus.DONE, JobStatus.PENDING),
        (JobStatus.DONE, JobStatus.RUNNING),
        (JobStatus.DONE, JobStatus.FAILED),
        (JobStatus.FAILED, JobStatus.PENDING),
        (JobStatus.FAILED, JobStatus.RUNNING),
        (JobStatus.FAILED, JobStatus.DONE),
    ],
)
def test_update_status_rejects_disallowed_transitions(repo, from_status, to_status):
    job = repo.enqueue(JobType.REVIEW, {})
    # from_statusまで正規の遷移で進める
    _advance_to(repo, job.id, from_status)

    with pytest.raises(InvalidJobTransitionError):
        repo.update_status(job.id, to_status)


def _advance_to(repo, job_id: str, status: JobStatus) -> None:
    if status == JobStatus.PENDING:
        return
    repo.update_status(job_id, JobStatus.RUNNING)
    if status == JobStatus.RUNNING:
        return
    if status == JobStatus.WAITING_HUMAN:
        repo.update_status(job_id, JobStatus.WAITING_HUMAN)
        return
    repo.update_status(job_id, status)


def test_update_status_raises_for_unknown_job(repo):
    with pytest.raises(JobNotFoundError):
        repo.update_status("unknown-id", JobStatus.RUNNING)


def test_list_by_status_returns_only_matching_jobs(repo):
    pending = repo.enqueue(JobType.REVIEW, {"n": 1})
    running = repo.enqueue(JobType.REVIEW, {"n": 2})
    repo.update_status(running.id, JobStatus.RUNNING)

    pending_jobs = repo.list_by_status(JobStatus.PENDING)
    running_jobs = repo.list_by_status(JobStatus.RUNNING)

    assert [j.id for j in pending_jobs] == [pending.id]
    assert [j.id for j in running_jobs] == [running.id]


def test_list_by_status_returns_empty_list_when_no_matches(repo):
    assert repo.list_by_status(JobStatus.FAILED) == []


def test_jobs_are_isolated_by_id(repo):
    first = repo.enqueue(JobType.REVIEW, {"n": 1})
    repo.enqueue(JobType.REVIEW, {"n": 2})

    repo.update_status(first.id, JobStatus.RUNNING)

    assert repo.get(first.id).status == JobStatus.RUNNING


def test_repo_is_usable_from_a_different_thread_than_the_constructor(repo):
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            repo.enqueue(JobType.REVIEW, {"from": "thread"})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert errors == []


def test_repo_handles_many_concurrent_writers_without_errors(repo):
    # M2-1の並列レビュー実行(cli/worker_pool.py)からJob経由で呼ばれることを想定し、
    # 複数のワーカースレッドが同時にenqueue/update_statusを呼んでも"database is locked"の
    # ような非決定的な失敗を起こさないことを確認する
    worker_count = 20
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def worker(n: int) -> None:
        try:
            job = repo.enqueue(JobType.REVIEW, {"n": n})
            repo.update_status(job.id, JobStatus.RUNNING)
            repo.update_status(job.id, JobStatus.DONE, result={"n": n})
        except BaseException as exc:  # noqa: BLE001
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    done_jobs = repo.list_by_status(JobStatus.DONE)
    assert len(done_jobs) == worker_count


def test_enqueue_wraps_sqlite_errors_as_job_error(repo):
    repo.close()

    with pytest.raises(JobError):
        repo.enqueue(JobType.REVIEW, {})


def test_get_wraps_sqlite_errors_as_job_error(repo):
    repo.close()

    with pytest.raises(JobError):
        repo.get("some-id")
