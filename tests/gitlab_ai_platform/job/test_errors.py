from gitlab_ai_platform.job import (
    InvalidJobTransitionError,
    JobError,
    JobNotFoundError,
    LeaseLostError,
)


def test_invalid_job_transition_error_is_a_job_error():
    assert issubclass(InvalidJobTransitionError, JobError)


def test_job_not_found_error_is_a_job_error():
    assert issubclass(JobNotFoundError, JobError)


def test_lease_lost_error_is_a_job_error():
    # M3-2([#92], docs/adr/0017-job-queue.md): claim済みJobへのheartbeat/complete/failが
    # 別workerに再取得された後(可視性タイムアウト経過後)に呼ばれたことを表す
    assert issubclass(LeaseLostError, JobError)
