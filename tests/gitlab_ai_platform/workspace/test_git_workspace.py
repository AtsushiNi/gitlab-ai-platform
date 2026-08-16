from __future__ import annotations

import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gitlab_ai_platform.workspace import DiskLimitExceededError, GitWorkspaceManager
from gitlab_ai_platform.workspace.git_workspace import (
    _deslugify_project,
    _slugify_project,
)

from .conftest import OriginRepo, run_git


def _manager(
    tmp_path: Path, origin: OriginRepo, *, max_disk_bytes: int = 10**9
) -> GitWorkspaceManager:
    return GitWorkspaceManager(
        tmp_path / "workspace",
        clone_url_for=lambda project: str(origin.path),
        max_disk_bytes=max_disk_bytes,
    )


def test_prepare_creates_bare_clone_and_worktree_checked_out_at_ref(
    tmp_path, origin_repo
):
    manager = _manager(tmp_path, origin_repo)

    handle = manager.prepare("group/project", 1, "main")

    assert handle.path.exists()
    assert (handle.path / "README.md").exists()
    assert handle.sha == origin_repo.main_sha
    assert (tmp_path / "workspace" / "repos" / "group%2Fproject.git").exists()


def test_prepare_checks_out_exact_commit_sha_even_if_not_branch_tip(
    tmp_path, origin_repo
):
    # State Storeはcommit_sha単位でレビュー状態を追うため、Workspace Managerもbranchの
    # 最新ではなく指定commitそのものを再現できる必要がある
    manager = _manager(tmp_path, origin_repo)

    handle = manager.prepare("group/project", 2, origin_repo.feature_sha_1)

    assert handle.sha == origin_repo.feature_sha_1
    assert (handle.path / "feature.txt").read_text() == "first\n"


def test_prepare_reuses_existing_bare_clone_for_second_mr_of_same_project(
    tmp_path, origin_repo
):
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


def test_prepare_raises_disk_limit_exceeded_when_no_worktree_can_be_evicted(
    tmp_path, origin_repo
):
    # 破棄できるworktreeが1つも無い状態でも上限を満たせない、意図的に不可能な上限設定
    manager = _manager(tmp_path, origin_repo, max_disk_bytes=-1)

    with pytest.raises(DiskLimitExceededError):
        manager.prepare("group/project", 1, "main")


def test_prepare_enforces_disk_budget_when_updating_existing_worktree(
    tmp_path, origin_repo
):
    # 既存worktreeをreset --hardで更新するパスでも上限チェックが働くことの回帰テスト
    # (以前は新規worktree作成時のみチェックしており、更新パスは無条件で上限を無視していた)
    manager = _manager(tmp_path, origin_repo, max_disk_bytes=10**9)
    manager.prepare("group/project", 1, "main")

    tight_manager = _manager(tmp_path, origin_repo, max_disk_bytes=-1)

    with pytest.raises(DiskLimitExceededError):
        tight_manager.prepare("group/project", 1, "feature-a")


# -- 並列実行(M2-1、ADR-0015) -----------------------------------------------------


def test_prepare_serializes_same_project_but_allows_different_projects_to_overlap(
    tmp_path, origin_repo
):
    # 同一project(=同一bare repo)へのgit操作は、複数MRから同時に呼ばれても常に
    # 直列化される(projectロック)。一方、異なるprojectへの操作は真に並行実行できる
    # ことを、実際のgit呼び出しの重なりを計測して確認する
    workspace_root = tmp_path / "workspace"
    lock = threading.Lock()
    active_by_cwd: dict[str, int] = {}
    max_active_by_cwd: dict[str, int] = {}
    global_active = 0
    global_max_active = 0

    def spy_run(command, *, cwd=None, **kwargs):
        nonlocal global_active, global_max_active
        key = str(cwd)
        with lock:
            active_by_cwd[key] = active_by_cwd.get(key, 0) + 1
            max_active_by_cwd[key] = max(
                max_active_by_cwd.get(key, 0), active_by_cwd[key]
            )
            global_active += 1
            global_max_active = max(global_max_active, global_active)
        time.sleep(0.02)  # 重なりを観測しやすくするための余白
        try:
            return subprocess.run(command, cwd=cwd, **kwargs)
        finally:
            with lock:
                active_by_cwd[key] -= 1
                global_active -= 1

    manager = GitWorkspaceManager(
        workspace_root,
        clone_url_for=lambda project: str(origin_repo.path),
        max_disk_bytes=10**9,
        run=spy_run,
    )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(manager.prepare, "group/project-a", 1, "main"),
            executor.submit(manager.prepare, "group/project-b", 1, "feature-a"),
            executor.submit(manager.prepare, "group/project-a", 2, "feature-a"),
        ]
        handles = [f.result(timeout=30) for f in futures]

    # group/project-a向けのbare repoに対する操作は、どの瞬間を見ても同時に1つまでしか
    # 走らない(projectロックによる直列化。破損を防ぐ本Issueの核心)
    bare_a = str(workspace_root / "repos" / "group%2Fproject-a.git")
    assert max_active_by_cwd.get(bare_a, 1) <= 1
    # 異なるproject同士のgit操作は実際に重なった(真に並行実行された)
    assert global_max_active > 1
    assert all(handle.path.exists() for handle in handles)
    assert len({handle.path for handle in handles}) == 3


def test_collect_garbage_skips_project_whose_lock_is_held_elsewhere(
    tmp_path, origin_repo
):
    # GC(collect_garbage)は、他スレッドが操作中(ロック取得できない)projectを
    # ブロッキング待ちせずスキップする設計(デッドロック回避、ADR-0015)。ロックを
    # 保持したままの状態を模擬し、より古いworktreeでも操作中なら退避対象から外れ、
    # 次点(操作中でない方)が退避されることを確認する。
    # projectロックはRLock(同一スレッドからの再入を許す、同一project内の別MRを退避する
    # 正当なケースのため)なので、「他スレッドが保持中」を別スレッドで再現する必要がある
    # (同じテストスレッドでlock.acquire()するだけでは、collect_garbage呼び出し自体が
    # 再入として通ってしまい、意図した「busyで退避不可」を再現できない)
    manager = _manager(tmp_path, origin_repo, max_disk_bytes=10**9)
    handle_a = manager.prepare("group/project-a", 1, "main")
    handle_b = manager.prepare("group/project-b", 1, "main")
    now = time.time()
    os.utime(
        handle_a.path, (now - 200, now - 200)
    )  # aの方が古い(通常ならaが先に選ばれる)
    os.utime(handle_b.path, (now - 100, now - 100))

    # ロック取得中の状態をホワイトボックスに再現する(内部実装への直接アクセス)
    lock_a = manager._project_lock("group/project-a")
    acquired = threading.Event()
    release = threading.Event()

    def _hold_lock_a() -> None:
        lock_a.acquire()
        acquired.set()
        release.wait(timeout=10)
        lock_a.release()

    holder = threading.Thread(target=_hold_lock_a)
    holder.start()
    assert acquired.wait(timeout=5)
    try:
        manager._max_disk_bytes = 0  # 強制的に上限超過状態にする
        removed = manager.collect_garbage()
    finally:
        release.set()
        holder.join(timeout=5)

    assert [r.project for r in removed] == ["group/project-b"]
    assert handle_a.path.exists()
    assert not handle_b.path.exists()


# -- スラッグ変換(_slugify_project/_deslugify_project) -----------------------------


def test_slugify_project_is_injective_for_projects_with_literal_underscore():
    # "/"を"__"に置換する単純な方式は、GitLabのproject/group名にアンダースコアが
    # 許可されているため単射でなかった(別プロジェクトのbare repoを共有してしまう)
    assert _slugify_project("ab/cd") != _slugify_project("ab__cd")


@pytest.mark.parametrize(
    "project",
    ["group/project", "group/sub/project", "a_/b", "a__b", "ab__cd", "trailing_/slash"],
)
def test_deslugify_project_is_inverse_of_slugify(project: str):
    assert _deslugify_project(_slugify_project(project)) == project


def test_resolve_ref_probe_applies_git_config(tmp_path, origin_repo):
    # `_resolve_ref`の存在確認プローブが`_run_git`と同様に`git_config`の`-c`引数を
    # 適用することの回帰テスト(以前はプローブだけこれを適用しておらず、
    # safe.directory等を注入する環境で無関係な失敗を「refが存在しない」と誤解釈しうった)
    commands: list[list[str]] = []

    def spy_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        commands.append(command)
        return subprocess.run(command, **kwargs)  # type: ignore[arg-type]

    manager = GitWorkspaceManager(
        tmp_path / "workspace",
        clone_url_for=lambda project: str(origin_repo.path),
        max_disk_bytes=10**9,
        git_config={"user.name": "spy-test"},
        run=spy_run,
    )

    manager.prepare("group/project", 1, "main")

    probe_commands = [c for c in commands if "--verify" in c and "--quiet" in c]
    assert probe_commands, "rev-parse --verify --quietのプローブ呼び出しが見つからない"
    assert all("-c" in c and "user.name=spy-test" in c for c in probe_commands)
