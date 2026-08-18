from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gitlab_ai_platform.gitlab_adapter.types import CommitActionType
from gitlab_ai_platform.push import (
    PushError,
    compute_commit_actions,
    resolve_push_base_sha,
)


def _run_git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result


def _head_sha(cwd: Path) -> str:
    return _run_git("rev-parse", "HEAD", cwd=cwd).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    # 実サービスには繋がず、ローカルの一時ディレクトリで完結させる(CLAUDE.mdのテスト方針)。
    # `git worktree`で作成した実際のworktreeを模す代わりに、`refs/remotes/origin/main`を
    # 手動で作っておき、`resolve_push_base_sha`が参照するrefの構造だけを再現する
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _run_git("init", "-b", "main", cwd=repo_path)
    _run_git("config", "user.email", "test@example.com", cwd=repo_path)
    _run_git("config", "user.name", "Test", cwd=repo_path)
    (repo_path / "README.md").write_text("hello\n")
    (repo_path / "to_delete.txt").write_text("bye\n")
    _run_git("add", "README.md", "to_delete.txt", cwd=repo_path)
    _run_git("commit", "-m", "initial commit", cwd=repo_path)

    base_sha = _head_sha(repo_path)
    _run_git("update-ref", "refs/remotes/origin/main", base_sha, cwd=repo_path)

    _run_git("checkout", "-b", "ai/issue-7", cwd=repo_path)
    (repo_path / "README.md").write_text("hello, updated\n")
    (repo_path / "new_file.py").write_text("print('new')\n")
    repo_path.joinpath("to_delete.txt").unlink()
    _run_git("add", "-A", cwd=repo_path)
    _run_git("commit", "-m", "implement task", cwd=repo_path)

    return repo_path


def test_resolve_push_base_sha_finds_branch_point(repo: Path):
    base_sha = _run_git(
        "rev-parse", "refs/remotes/origin/main", cwd=repo
    ).stdout.strip()
    commit_sha = _head_sha(repo)

    resolved = resolve_push_base_sha(repo, "main", commit_sha)

    assert resolved == base_sha


def test_resolve_push_base_sha_still_finds_branch_point_when_default_branch_advanced(
    repo: Path,
):
    # feature branch作成後にdefault branch側が進んでも(pushフェーズ自身がまだmerge/rebaseを
    # 行っていない限り)merge-baseは実装用branchが分岐した時点のcommitのまま(ADR-0034)
    base_sha = _run_git(
        "rev-parse", "refs/remotes/origin/main", cwd=repo
    ).stdout.strip()
    commit_sha = _head_sha(repo)

    _run_git("checkout", "main", cwd=repo)
    (repo / "other.txt").write_text("advanced\n")
    _run_git("add", "other.txt", cwd=repo)
    _run_git("commit", "-m", "unrelated change on main", cwd=repo)
    advanced_main_sha = _head_sha(repo)
    _run_git("update-ref", "refs/remotes/origin/main", advanced_main_sha, cwd=repo)
    _run_git("checkout", "ai/issue-7", cwd=repo)

    resolved = resolve_push_base_sha(repo, "main", commit_sha)

    assert resolved == base_sha


def test_resolve_push_base_sha_raises_push_error_when_git_fails(tmp_path: Path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    with pytest.raises(PushError):
        resolve_push_base_sha(not_a_repo, "main", "deadbeef")


def test_compute_commit_actions_reports_create_update_and_delete(repo: Path):
    commit_sha = _head_sha(repo)

    actions = compute_commit_actions(repo, "main", commit_sha)

    by_path = {a.file_path: a for a in actions}
    assert set(by_path) == {"README.md", "new_file.py", "to_delete.txt"}

    assert by_path["README.md"].action == CommitActionType.UPDATE
    assert by_path["README.md"].content == "hello, updated\n"

    assert by_path["new_file.py"].action == CommitActionType.CREATE
    assert by_path["new_file.py"].content == "print('new')\n"

    assert by_path["to_delete.txt"].action == CommitActionType.DELETE
    assert by_path["to_delete.txt"].content is None


def test_compute_commit_actions_treats_rename_as_delete_and_create(repo: Path):
    _run_git("mv", "README.md", "RENAMED.md", cwd=repo)
    _run_git("commit", "-m", "rename readme", cwd=repo)
    commit_sha = _head_sha(repo)

    actions = compute_commit_actions(repo, "main", commit_sha)

    by_path = {a.file_path: a for a in actions}
    # --no-renamesのため、rename検出はせずdelete(旧パス)+create(新パス)の組として現れる
    # (CommitActionTypeがcreate/update/deleteの3値のみのため、ADR-0034)
    assert by_path["README.md"].action == CommitActionType.DELETE
    assert by_path["RENAMED.md"].action == CommitActionType.CREATE
    assert by_path["RENAMED.md"].content == "hello, updated\n"


def test_compute_commit_actions_returns_empty_list_when_no_changes(repo: Path):
    commit_sha = _head_sha(repo)
    # base(refs/remotes/origin/main)自体をcommit_shaに一致させれば差分は無くなる
    _run_git("update-ref", "refs/remotes/origin/main", commit_sha, cwd=repo)

    actions = compute_commit_actions(repo, "main", commit_sha)

    assert actions == []


def test_compute_commit_actions_uses_injected_run_callable():
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "merge-base" in command:
            return subprocess.CompletedProcess(
                command, 0, stdout="base-sha\n", stderr=""
            )
        if "diff" in command:
            return subprocess.CompletedProcess(
                command, 0, stdout="A\tnew_file.py\n", stderr=""
            )
        if "show" in command:
            return subprocess.CompletedProcess(
                command, 0, stdout="content\n", stderr=""
            )
        raise AssertionError(f"unexpected git command: {command}")

    actions = compute_commit_actions(
        Path("/tmp/whatever"), "main", "commit-sha", run=fake_run
    )

    assert len(actions) == 1
    assert actions[0].file_path == "new_file.py"
    assert actions[0].action == CommitActionType.CREATE
    assert actions[0].content == "content\n"
    assert len(calls) == 3
