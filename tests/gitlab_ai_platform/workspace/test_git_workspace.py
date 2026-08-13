from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from gitlab_ai_platform.workspace import DiskLimitExceededError, GitWorkspaceManager

from .conftest import OriginRepo, run_git


def _manager(
    tmp_path: Path, origin: OriginRepo, *, max_disk_bytes: int = 10**9
) -> GitWorkspaceManager:
    return GitWorkspaceManager(
        tmp_path / "workspace",
        clone_url_for=lambda project: str(origin.path),
        max_disk_bytes=max_disk_bytes,
    )


def test_prepare_creates_bare_clone_and_worktree_checked_out_at_ref(tmp_path, origin_repo):
    manager = _manager(tmp_path, origin_repo)

    handle = manager.prepare("group/project", 1, "main")

    assert handle.path.exists()
    assert (handle.path / "README.md").exists()
    assert handle.sha == origin_repo.main_sha
    assert (tmp_path / "workspace" / "repos" / "group__project.git").exists()


def test_prepare_checks_out_exact_commit_sha_even_if_not_branch_tip(tmp_path, origin_repo):
    # State Storeはcommit_sha単位でレビュー状態を追うため、Workspace Managerもbranchの
    # 最新ではなく指定commitそのものを再現できる必要がある
    manager = _manager(tmp_path, origin_repo)

    handle = manager.prepare("group/project", 2, origin_repo.feature_sha_1)

    assert handle.sha == origin_repo.feature_sha_1
    assert (handle.path / "feature.txt").read_text() == "first\n"


def test_prepare_reuses_existing_bare_clone_for_second_mr_of_same_project(tmp_path, origin_repo):
    manager = _manager(tmp_path, origin_repo)

    manager.prepare("group/project", 1, "main")
    handle_2 = manager.prepare("group/project", 2, "feature-a")

    bare_repos = list((tmp_path / "workspace" / "repos").iterdir())
    assert len(bare_repos) == 1
    assert handle_2.sha == origin_repo.feature_sha_2


def test_prepare_updates_existing_worktree_to_new_commit(tmp_path, origin_repo):
    # Spike S-3 §4.2の回避策(bare repoへのfetchはrefs/remotes/origin/*のみ)が、
    # 対象branchをworktreeでcheckout中でも機能することの回帰テスト
    manager = _manager(tmp_path, origin_repo)
    manager.prepare("group/project", 1, "feature-a")

    run_git("checkout", "feature-a", cwd=origin_repo.path)
    (origin_repo.path / "feature.txt").write_text("first\nsecond\nthird\n")
    run_git("add", "feature.txt", cwd=origin_repo.path)
    run_git("commit", "-m", "feature commit 3", cwd=origin_repo.path)
    new_sha = run_git("rev-parse", "HEAD", cwd=origin_repo.path).stdout.strip()
    run_git("checkout", "main", cwd=origin_repo.path)

    handle = manager.prepare("group/project", 1, "feature-a")

    assert handle.sha == new_sha
    assert (handle.path / "feature.txt").read_text() == "first\nsecond\nthird\n"


def test_worktrees_of_different_mrs_do_not_share_working_tree(tmp_path, origin_repo):
    # 「並列レビューでworking treeを共有しない」(docs/architecture.md)ことの確認
    manager = _manager(tmp_path, origin_repo)

    handle_main = manager.prepare("group/project", 1, "main")
    handle_feature = manager.prepare("group/project", 2, "feature-a")

    assert handle_main.path != handle_feature.path
    assert not (handle_main.path / "feature.txt").exists()
    assert (handle_feature.path / "feature.txt").exists()


def test_discard_removes_worktree_directory(tmp_path, origin_repo):
    manager = _manager(tmp_path, origin_repo)
    handle = manager.prepare("group/project", 1, "main")

    manager.discard("group/project", 1)

    assert not handle.path.exists()


def test_discard_is_idempotent_for_unknown_mr(tmp_path, origin_repo):
    manager = _manager(tmp_path, origin_repo)

    manager.discard("group/project", 999)  # 例外を送出しないこと


def test_discard_allows_recreating_worktree_afterwards(tmp_path, origin_repo):
    manager = _manager(tmp_path, origin_repo)
    manager.prepare("group/project", 1, "main")
    manager.discard("group/project", 1)

    handle = manager.prepare("group/project", 1, "main")

    assert handle.path.exists()
    assert handle.sha == origin_repo.main_sha


def test_prepare_evicts_oldest_worktree_when_disk_limit_reached(tmp_path, origin_repo):
    manager = _manager(tmp_path, origin_repo, max_disk_bytes=0)

    handle_1 = manager.prepare("group/project", 1, "main")
    assert handle_1.path.exists()

    handle_2 = manager.prepare("group/project", 2, "feature-a")

    assert handle_2.path.exists()
    assert not handle_1.path.exists()


def test_collect_garbage_evicts_oldest_worktrees_first(tmp_path, origin_repo):
    builder = _manager(tmp_path, origin_repo, max_disk_bytes=10**9)
    handle_1 = builder.prepare("group/project", 1, "main")
    handle_2 = builder.prepare("group/project", 2, "feature-a")
    handle_3 = builder.prepare("group/project", 3, "main")

    now = time.time()
    os.utime(handle_1.path, (now - 300, now - 300))
    os.utime(handle_2.path, (now - 200, now - 200))
    os.utime(handle_3.path, (now - 100, now - 100))

    # 別インスタンスでcollect_garbage単体の挙動を検証する(prepare内の事前チェックとは独立)
    gc_manager = _manager(tmp_path, origin_repo, max_disk_bytes=0)
    removed = gc_manager.collect_garbage()

    assert [r.mr_iid for r in removed] == [1, 2, 3]
    assert not handle_1.path.exists()
    assert not handle_2.path.exists()
    assert not handle_3.path.exists()


def test_collect_garbage_returns_empty_list_when_within_limit(tmp_path, origin_repo):
    manager = _manager(tmp_path, origin_repo, max_disk_bytes=10**9)
    manager.prepare("group/project", 1, "main")

    assert manager.collect_garbage() == []


def test_prepare_raises_disk_limit_exceeded_when_no_worktree_can_be_evicted(tmp_path, origin_repo):
    # 破棄できるworktreeが1つも無い状態でも上限を満たせない、意図的に不可能な上限設定
    manager = _manager(tmp_path, origin_repo, max_disk_bytes=-1)

    with pytest.raises(DiskLimitExceededError):
        manager.prepare("group/project", 1, "main")
