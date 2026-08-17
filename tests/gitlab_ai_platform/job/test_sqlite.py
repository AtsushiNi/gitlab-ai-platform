import threading

import pytest

from gitlab_ai_platform.job import (
    DEFAULT_MAX_ATTEMPTS,
    InvalidJobTransitionError,
    JobError,
    JobNotFoundError,
    JobStatus,
    JobType,
    LeaseLostError,
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


# --- M3-2([#92], docs/adr/0017-job-queue.md): claim(取得の排他) ---------------------------


def test_enqueue_accepts_custom_max_attempts(repo):
    job = repo.enqueue(JobType.REVIEW, {}, max_attempts=5)

    assert job.max_attempts == 5
    assert job.attempts == 0


def test_enqueue_defaults_max_attempts(repo):
    job = repo.enqueue(JobType.REVIEW, {})

    assert job.max_attempts == DEFAULT_MAX_ATTEMPTS


def test_claim_returns_none_when_no_pending_jobs(repo):
    assert repo.claim("worker-1") is None


def test_claim_picks_pending_job_and_marks_running(repo):
    job = repo.enqueue(JobType.REVIEW, {"n": 1})

    claimed = repo.claim("worker-1")

    assert claimed.id == job.id
    assert claimed.status == JobStatus.RUNNING
    assert claimed.lease_owner == "worker-1"
    assert claimed.lease_expires_at is not None
    assert claimed.attempts == 1
    assert repo.get(job.id).status == JobStatus.RUNNING


def test_claim_does_not_return_an_already_claimed_job(repo):
    repo.enqueue(JobType.REVIEW, {"n": 1})
    repo.claim("worker-1")

    assert repo.claim("worker-2") is None


def test_claim_filters_by_job_types(repo):
    review_job = repo.enqueue(JobType.REVIEW, {})
    design_job = repo.enqueue(JobType.DESIGN, {})

    claimed = repo.claim("worker-1", job_types=[JobType.DESIGN])

    assert claimed is not None
    assert claimed.id == design_job.id
    assert repo.get(review_job.id).status == JobStatus.PENDING


def test_claim_picks_oldest_pending_job_first(repo):
    first = repo.enqueue(JobType.REVIEW, {"n": 1})
    repo.enqueue(JobType.REVIEW, {"n": 2})

    claimed = repo.claim("worker-1")

    assert claimed.id == first.id


def test_claim_wraps_sqlite_errors_as_job_error(repo):
    repo.close()

    with pytest.raises(JobError):
        repo.claim("worker-1")


def test_repo_handles_concurrent_claims_without_double_claiming(repo):
    # M2-1(ReviewWorkerPool)相当の複数ワーカーが同時にclaimしても、同じJobを2つ以上の
    # workerが取得しないことを確認する(docs/adr/0017-job-queue.mdの排他取得設計の回帰テスト)
    job_count = 20
    for n in range(job_count):
        repo.enqueue(JobType.REVIEW, {"n": n})

    claimed_ids: list[str] = []
    claimed_lock = threading.Lock()
    errors: list[BaseException] = []

    def worker(worker_id: str) -> None:
        try:
            job = repo.claim(worker_id)
            if job is not None:
                with claimed_lock:
                    claimed_ids.append(job.id)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(f"worker-{n}",)) for n in range(job_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(claimed_ids) == job_count
    assert len(set(claimed_ids)) == job_count  # 重複取得がないこと


# --- heartbeat(リース延長) ---------------------------------------------------------------


def test_heartbeat_extends_lease_expiry(repo):
    repo.enqueue(JobType.REVIEW, {})
    claimed = repo.claim("worker-1", visibility_timeout_seconds=1)

    extended = repo.heartbeat(claimed.id, "worker-1", visibility_timeout_seconds=3600)

    assert extended.lease_expires_at > claimed.lease_expires_at


def test_heartbeat_raises_lease_lost_error_for_wrong_worker(repo):
    job = repo.enqueue(JobType.REVIEW, {})
    repo.claim("worker-1")

    with pytest.raises(LeaseLostError):
        repo.heartbeat(job.id, "worker-2")


def test_heartbeat_raises_lease_lost_error_for_unclaimed_job(repo):
    job = repo.enqueue(JobType.REVIEW, {})

    with pytest.raises(LeaseLostError):
        repo.heartbeat(job.id, "worker-1")


def test_heartbeat_raises_job_not_found_for_unknown_job(repo):
    with pytest.raises(JobNotFoundError):
        repo.heartbeat("unknown-id", "worker-1")


# --- complete(正常終了の報告) -------------------------------------------------------------


def test_complete_marks_job_done_and_clears_lease(repo):
    job = repo.enqueue(JobType.REVIEW, {})
    repo.claim("worker-1")

    completed = repo.complete(job.id, "worker-1", result={"x": 1})

    assert completed.status == JobStatus.DONE
    assert completed.result == {"x": 1}
    assert completed.lease_owner is None
    assert completed.lease_expires_at is None
    assert repo.get(job.id).status == JobStatus.DONE


def test_complete_raises_lease_lost_error_for_wrong_worker(repo):
    job = repo.enqueue(JobType.REVIEW, {})
    repo.claim("worker-1")

    with pytest.raises(LeaseLostError):
        repo.complete(job.id, "worker-2")


# --- fail(失敗の報告): リトライ・デッドレター ------------------------------------------------


def test_fail_with_retry_and_capacity_remaining_returns_job_to_pending(repo):
    job = repo.enqueue(JobType.REVIEW, {}, max_attempts=3)
    repo.claim("worker-1")

    failed = repo.fail(job.id, "worker-1", "transient error")

    assert failed.status == JobStatus.PENDING
    assert failed.error == "transient error"
    assert failed.lease_owner is None
    assert failed.dead_letter_at is None
    assert failed.attempts == 1
    assert repo.list_dead_letters() == []


def test_fail_without_retry_marks_job_as_dead_letter_immediately(repo):
    job = repo.enqueue(JobType.REVIEW, {}, max_attempts=5)
    repo.claim("worker-1")

    failed = repo.fail(job.id, "worker-1", "permanent error", retry=False)

    assert failed.status == JobStatus.FAILED
    assert failed.error == "permanent error"
    assert failed.dead_letter_at is not None
    assert [j.id for j in repo.list_dead_letters()] == [job.id]


def test_fail_exhausting_max_attempts_marks_dead_letter(repo):
    job = repo.enqueue(JobType.REVIEW, {}, max_attempts=2)

    for _ in range(2):
        claimed = repo.claim("worker-1")
        assert claimed is not None
        repo.fail(claimed.id, "worker-1", "boom")

    final = repo.get(job.id)
    assert final.status == JobStatus.FAILED
    assert final.attempts == 2
    assert final.dead_letter_at is not None
    assert [j.id for j in repo.list_dead_letters()] == [job.id]
    # デッドレター化した後は再取得できない
    assert repo.claim("worker-2") is None


def test_fail_raises_lease_lost_error_for_wrong_worker(repo):
    job = repo.enqueue(JobType.REVIEW, {})
    repo.claim("worker-1")

    with pytest.raises(LeaseLostError):
        repo.fail(job.id, "worker-2", "boom")


def test_list_dead_letters_returns_empty_list_when_none(repo):
    assert repo.list_dead_letters() == []


# --- 可視性タイムアウト(claim時の自動回収) --------------------------------------------------


def test_claim_reclaims_expired_lease_and_returns_to_pending_when_capacity_remains(
    repo,
):
    job = repo.enqueue(JobType.REVIEW, {}, max_attempts=3)
    # 負の秒数を指定し、claim直後にリースを期限切れにする
    repo.claim("worker-1", visibility_timeout_seconds=-1)

    reclaimed = repo.claim("worker-2")

    assert reclaimed is not None
    assert reclaimed.id == job.id
    assert reclaimed.lease_owner == "worker-2"
    assert reclaimed.attempts == 2


def test_claim_dead_letters_expired_lease_when_max_attempts_exhausted(repo):
    job = repo.enqueue(JobType.REVIEW, {}, max_attempts=1)
    repo.claim("worker-1", visibility_timeout_seconds=-1)

    # 回収時点でattempts(1) >= max_attempts(1)のため、再取得可能なJobは無い
    assert repo.claim("worker-2") is None

    final = repo.get(job.id)
    assert final.status == JobStatus.FAILED
    assert final.dead_letter_at is not None
    assert [j.id for j in repo.list_dead_letters()] == [job.id]


# --- 既存(M3-1)スキーマからのマイグレーション ------------------------------------------------


def test_repo_migrates_pre_m3_2_schema_without_new_columns(tmp_path):
    # M3-1時点のjobsテーブル(claim関連カラムを持たない)を模したDBファイルを用意し、
    # SqliteJobRepositoryが後方互換でカラムを追加できることを確認する(docs/adr/0017-job-queue.md)
    import sqlite3

    db_path = tmp_path / "legacy_job.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            result TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    now = "2026-08-16T00:00:00+00:00"
    conn.execute(
        "INSERT INTO jobs (id, job_type, status, payload, result, error, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)",
        ("legacy-1", "review", "pending", "{}", now, now),
    )
    conn.commit()
    conn.close()

    migrated_repo = SqliteJobRepository(db_path)
    try:
        job = migrated_repo.get("legacy-1")
        assert job is not None
        assert job.status == JobStatus.PENDING
        assert job.attempts == 0
        assert job.max_attempts == DEFAULT_MAX_ATTEMPTS
        assert job.lease_owner is None
        assert job.dead_letter_at is None

        claimed = migrated_repo.claim("worker-1")
        assert claimed is not None
        assert claimed.id == "legacy-1"
    finally:
        migrated_repo.close()
