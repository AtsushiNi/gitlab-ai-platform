from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitlab_ai_platform.implement import ImplementError, read_worktree_state


def _run_git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    # 実サービスには繋がず、ローカルの一時ディレクトリで完結させる(CLAUDE.mdのテスト方針)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _run_git("init", "-b", "main", cwd=repo_path)
    _run_git("config", "user.email", "test@example.com", cwd=repo_path)
    _run_git("config", "user.name", "Test", cwd=repo_path)
    (repo_path / "README.md").write_text("hello\n")
    _run_git("add", "README.md", cwd=repo_path)
    _run_git("commit", "-m", "initial commit", cwd=repo_path)
    return repo_path


def test_read_worktree_state_reports_head_sha_and_clean_state(repo: Path):
    expected_sha = _run_git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    state = read_worktree_state(repo)

    assert state.head_sha == expected_sha
    assert state.is_clean is True


def test_read_worktree_state_reports_dirty_when_uncommitted_changes_exist(repo: Path):
    (repo / "README.md").write_text("changed\n")

    state = read_worktree_state(repo)

    assert state.is_clean is False


def test_read_worktree_state_reports_dirty_for_untracked_files(repo: Path):
    (repo / "new_file.txt").write_text("new\n")

    state = read_worktree_state(repo)

    assert state.is_clean is False


def test_read_worktree_state_changes_head_sha_after_new_commit(repo: Path):
    before = read_worktree_state(repo)

    (repo / "feature.txt").write_text("feature\n")
    _run_git("add", "feature.txt", cwd=repo)
    _run_git("commit", "-m", "add feature", cwd=repo)

    after = read_worktree_state(repo)

    assert after.head_sha != before.head_sha
    assert after.is_clean is True


def test_read_worktree_state_raises_implement_error_when_git_fails(tmp_path: Path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    with pytest.raises(ImplementError):
        read_worktree_state(not_a_repo)


def test_read_worktree_state_uses_injected_run_callable():
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "rev-parse" in command:
            return subprocess.CompletedProcess(
                command, 0, stdout="fake-sha\n", stderr=""
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    state = read_worktree_state(Path("/tmp/whatever"), run=fake_run)

    assert state.head_sha == "fake-sha"
    assert state.is_clean is True
    assert len(calls) == 2
