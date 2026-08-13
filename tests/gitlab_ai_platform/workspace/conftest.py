from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


def run_git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result


@dataclass(frozen=True)
class OriginRepo:
    """テスト用origin(通常のnon-bareリポジトリ)の情報。"""

    path: Path
    main_sha: str
    feature_sha_1: str
    feature_sha_2: str


@pytest.fixture
def origin_repo(tmp_path: Path) -> OriginRepo:
    """`main`に1コミット、`feature-a`に`main`から分岐した2コミットを持つorigin repoを作る。

    実サービス(社内GitLab)には繋がず、ローカルの一時ディレクトリで完結させる
    (CLAUDE.mdのテスト方針)。
    """
    repo_path = tmp_path / "origin"
    repo_path.mkdir()
    run_git("init", "-b", "main", cwd=repo_path)
    run_git("config", "user.email", "test@example.com", cwd=repo_path)
    run_git("config", "user.name", "Test", cwd=repo_path)

    (repo_path / "README.md").write_text("main\n")
    run_git("add", "README.md", cwd=repo_path)
    run_git("commit", "-m", "initial commit", cwd=repo_path)
    main_sha = run_git("rev-parse", "HEAD", cwd=repo_path).stdout.strip()

    run_git("checkout", "-b", "feature-a", cwd=repo_path)
    (repo_path / "feature.txt").write_text("first\n")
    run_git("add", "feature.txt", cwd=repo_path)
    run_git("commit", "-m", "feature commit 1", cwd=repo_path)
    feature_sha_1 = run_git("rev-parse", "HEAD", cwd=repo_path).stdout.strip()

    (repo_path / "feature.txt").write_text("first\nsecond\n")
    run_git("add", "feature.txt", cwd=repo_path)
    run_git("commit", "-m", "feature commit 2", cwd=repo_path)
    feature_sha_2 = run_git("rev-parse", "HEAD", cwd=repo_path).stdout.strip()

    run_git("checkout", "main", cwd=repo_path)

    return OriginRepo(
        path=repo_path,
        main_sha=main_sha,
        feature_sha_1=feature_sha_1,
        feature_sha_2=feature_sha_2,
    )
