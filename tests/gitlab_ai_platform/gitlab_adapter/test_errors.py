from gitlab_ai_platform.gitlab_adapter import GitLabAdapterError, GitLabApiError


def test_gitlab_api_error_is_a_gitlab_adapter_error():
    assert issubclass(GitLabApiError, GitLabAdapterError)


def test_gitlab_api_error_retains_message_and_status_code():
    error = GitLabApiError("not found", status_code=404)

    assert str(error) == "not found"
    assert error.status_code == 404


def test_gitlab_api_error_status_code_defaults_to_none():
    error = GitLabApiError("boom")

    assert error.status_code is None
