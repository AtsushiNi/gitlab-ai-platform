from gitlab_ai_platform.push import NoFileChangesError, PushError


def test_no_file_changes_error_is_push_error():
    assert issubclass(NoFileChangesError, PushError)


def test_push_error_is_exception():
    assert issubclass(PushError, Exception)
