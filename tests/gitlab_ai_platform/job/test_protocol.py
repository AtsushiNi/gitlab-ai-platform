import dataclasses
from datetime import UTC, datetime

import pytest

from gitlab_ai_platform.job import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    Job,
    JobRepository,
    JobStatus,
    JobType,
)

_EXPECTED_PUBLIC_METHODS = {
    "enqueue",
    "get",
    "update_status",
    "list_by_status",
    "close",
    # M3-2([#92], docs/adr/0017-job-queue.md): 取得の排他・可視性タイムアウト・リトライ・
    # デッドレターのための新メソッド
    "claim",
    "heartbeat",
    "complete",
    "fail",
    "list_dead_letters",
    # M4-3([#109], docs/adr/0026-job-waiting-human-transition.md): `complete`と対になる
    # WAITING_HUMANへの遷移メソッド
    "wait_for_human",
}


def _public_methods(protocol_cls: type) -> set[str]:
    return {name for name in dir(protocol_cls) if not name.startswith("_")}


def test_job_repository_exposes_only_expected_operations():
    # 将来メソッドが意図せず増減した場合にこのテストで検知できるようにする
    assert _public_methods(JobRepository) == _EXPECTED_PUBLIC_METHODS


def test_job_type_values():
    assert JobType.REVIEW == "review"
    assert JobType.ISSUE_ANALYSIS == "issue-analysis"
    assert JobType.DESIGN == "design"
    assert JobType.PLAN == "plan"
    assert JobType.IMPLEMENT == "implement"


def test_job_status_values():
    assert JobStatus.PENDING == "pending"
    assert JobStatus.RUNNING == "running"
    assert JobStatus.WAITING_HUMAN == "waiting_human"
    assert JobStatus.DONE == "done"
    assert JobStatus.FAILED == "failed"


def _job(**overrides) -> Job:
    now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
    kwargs = dict(
        id="job-1",
        job_type=JobType.REVIEW,
        status=JobStatus.PENDING,
        payload={"project": "group/project", "mr_iid": 1},
        result=None,
        error=None,
        created_at=now,
        updated_at=now,
    )
    kwargs.update(overrides)
    return Job(**kwargs)


def test_job_is_frozen():
    job = _job()

    with pytest.raises(dataclasses.FrozenInstanceError):
        job.status = JobStatus.RUNNING


def test_job_holds_payload_and_result_as_plain_dicts():
    job = _job(
        status=JobStatus.DONE,
        result={"result_path": "reviews/group/project/1/abc123"},
    )

    assert job.payload == {"project": "group/project", "mr_iid": 1}
    assert job.result == {"result_path": "reviews/group/project/1/abc123"}


def test_job_queue_fields_have_backward_compatible_defaults():
    # M3-2([#92], docs/adr/0017-job-queue.md)で追加したフィールド。既存フィールドのみを
    # 指定するキーワード構築(_job()ヘルパー)が無改修で動くことを確認する
    job = _job()

    assert job.attempts == 0
    assert job.max_attempts == DEFAULT_MAX_ATTEMPTS
    assert job.lease_owner is None
    assert job.lease_expires_at is None
    assert job.dead_letter_at is None


def test_default_visibility_timeout_seconds_is_positive():
    assert DEFAULT_VISIBILITY_TIMEOUT_SECONDS > 0


class _FakeJobRepository:
    """Protocolを満たすダミー実装。SQLite実装もこの形になる。"""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._next_id = 1

    def enqueue(
        self,
        job_type: JobType,
        payload: dict,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> Job:
        job_id = str(self._next_id)
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

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        result: dict | None = None,
        error: str | None = None,
    ) -> Job:
        current = self._jobs[job_id]
        updated = dataclasses.replace(
            current,
            status=status,
            result=result if result is not None else current.result,
            error=error if error is not None else current.error,
            updated_at=datetime.now(UTC),
        )
        self._jobs[job_id] = updated
        return updated

    def list_by_status(self, status: JobStatus) -> list[Job]:
        return [job for job in self._jobs.values() if job.status == status]

    def close(self) -> None:
        pass

    def claim(
        self,
        worker_id: str,
        *,
        job_types=None,
        visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    ) -> Job | None:
        for job in self._jobs.values():
            if job.status != JobStatus.PENDING:
                continue
            if job_types is not None and job.job_type not in job_types:
                continue
            updated = dataclasses.replace(
                job,
                status=JobStatus.RUNNING,
                lease_owner=worker_id,
                attempts=job.attempts + 1,
                updated_at=datetime.now(UTC),
            )
            self._jobs[job.id] = updated
            return updated
        return None

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    ) -> Job:
        return self._jobs[job_id]

    def complete(self, job_id: str, worker_id: str, result: dict | None = None) -> Job:
        return self.update_status(job_id, JobStatus.DONE, result=result)

    def fail(
        self, job_id: str, worker_id: str, error: str, *, retry: bool = True
    ) -> Job:
        return self.update_status(job_id, JobStatus.FAILED, error=error)

    def wait_for_human(
        self, job_id: str, worker_id: str, result: dict | None = None
    ) -> Job:
        return self.update_status(job_id, JobStatus.WAITING_HUMAN, result=result)

    def list_dead_letters(self) -> list[Job]:
        return [job for job in self._jobs.values() if job.dead_letter_at is not None]


def test_fake_job_repository_satisfies_protocol():
    repo = _FakeJobRepository()

    assert isinstance(repo, JobRepository)
