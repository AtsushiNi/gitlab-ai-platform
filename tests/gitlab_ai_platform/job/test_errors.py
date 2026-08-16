from gitlab_ai_platform.job import InvalidJobTransitionError, JobError, JobNotFoundError


def test_invalid_job_transition_error_is_a_job_error():
    assert issubclass(InvalidJobTransitionError, JobError)


def test_job_not_found_error_is_a_job_error():
    assert issubclass(JobNotFoundError, JobError)
