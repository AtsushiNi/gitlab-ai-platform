"""`resolve_default_project`(cwdのgit remoteからのproject自動検出)を検証する。

実際に`git`コマンドをローカルの一時ディレクトリ上で実行するが、実GitLab等の外部サービスには
一切繋がない(CLAUDE.mdのテスト方針)。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from gitlab_ai_platform.adapter_mcp_server.default_project import (
    resolve_default_project,
)


def _init_repo(tmp_path: Path, *, remote_url: str | None = None) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    if remote_url is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=repo_path,
            check=True,
            capture_output=True,
        )
    return repo_path


def test_resolves_project_from_scp_style_ssh_remote_url(tmp_path: Path) -> None:
    repo_path = _init_repo(
        tmp_path, remote_url="git@gitlab.example.com:group/project.git"
    )

    assert resolve_default_project(cwd=repo_path) == "group/project"


def test_resolves_project_from_https_remote_url(tmp_path: Path) -> None:
    repo_path = _init_repo(
        tmp_path, remote_url="https://gitlab.example.com/group/project.git"
    )

    assert resolve_default_project(cwd=repo_path) == "group/project"


def test_resolves_nested_subgroup_path(tmp_path: Path) -> None:
    repo_path = _init_repo(
        tmp_path, remote_url="git@gitlab.example.com:group/subgroup/project.git"
    )

    assert resolve_default_project(cwd=repo_path) == "group/subgroup/project"


def test_resolves_remote_url_without_dot_git_suffix(tmp_path: Path) -> None:
    repo_path = _init_repo(
        tmp_path, remote_url="https://gitlab.example.com/group/project"
    )

    assert resolve_default_project(cwd=repo_path) == "group/project"


def test_returns_none_when_origin_remote_is_missing(tmp_path: Path) -> None:
    repo_path = _init_repo(tmp_path)

    assert resolve_default_project(cwd=repo_path) is None


def test_returns_none_when_not_a_git_repository(tmp_path: Path) -> None:
    assert resolve_default_project(cwd=tmp_path) is None
