from gitlab_ai_platform.workspace import DiskLimitExceededError, GitCommandError, WorkspaceError


def test_git_command_error_is_a_workspace_error():
    assert issubclass(GitCommandError, WorkspaceError)


def test_disk_limit_exceeded_error_is_a_workspace_error():
    assert issubclass(DiskLimitExceededError, WorkspaceError)


def test_git_command_error_holds_command_context():
    error = GitCommandError("failed", command=["git", "status"], returncode=1, stderr="oops")

    assert error.command == ["git", "status"]
    assert error.returncode == 1
    assert error.stderr == "oops"
