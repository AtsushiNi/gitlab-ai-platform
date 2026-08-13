from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gitlab_ai_platform.cli.lock import AlreadyRunningError, ProcessLock


def test_acquire_creates_lock_file_with_pid(tmp_path: Path):
    lock_path = tmp_path / "watch.lock"
    lock = ProcessLock(lock_path)

    lock.acquire()
    try:
        assert lock_path.is_file()
    finally:
        lock.release()


def test_second_process_lock_fails_while_first_holds_it(tmp_path: Path):
    lock_path = tmp_path / "watch.lock"
    first = ProcessLock(lock_path)
    second = ProcessLock(lock_path)

    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            second.acquire()
    finally:
        first.release()


def test_lock_can_be_reacquired_after_release(tmp_path: Path):
    lock_path = tmp_path / "watch.lock"
    first = ProcessLock(lock_path)
    first.acquire()
    first.release()

    second = ProcessLock(lock_path)
    second.acquire()
    second.release()


def test_context_manager_releases_lock_on_exit(tmp_path: Path):
    lock_path = tmp_path / "watch.lock"

    with ProcessLock(lock_path):
        with pytest.raises(AlreadyRunningError):
            ProcessLock(lock_path).acquire()

    # withブロックを抜けた時点で解放されているので再取得できる
    other = ProcessLock(lock_path)
    other.acquire()
    other.release()


def test_release_without_acquire_is_a_noop(tmp_path: Path):
    lock = ProcessLock(tmp_path / "watch.lock")
    lock.release()


def test_acquire_creates_parent_directories(tmp_path: Path):
    lock_path = tmp_path / "nested" / "dir" / "watch.lock"
    lock = ProcessLock(lock_path)

    lock.acquire()
    try:
        assert lock_path.is_file()
    finally:
        lock.release()


class _FakeMsvcrt:
    """`msvcrt.locking`を模したフェイク。ロック中に2回目の`LK_NBLCK`が来ると失敗させる。

    実際のOSロックはfd番号ではなくファイル実体単位で排他されるため、fdの値では
    判定せず単一のフラグで管理する(このテストでは1つのロックファイルしか扱わないため)。
    """

    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self) -> None:
        self._locked = False
        self.locking_calls: list[tuple[int, int]] = []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.locking_calls.append((fd, mode))
        if mode == self.LK_NBLCK:
            if self._locked:
                raise OSError("Permission denied")
            self._locked = True
        elif mode == self.LK_UNLCK:
            self._locked = False


def test_windows_locking_path_uses_msvcrt(tmp_path: Path, monkeypatch):
    # このリポジトリの開発機はmacOSのためWindows実機では検証できない
    # (references/spike-S3-git-worktree-windows.md と同様の制約)。`sys.platform`と
    # `sys.modules["msvcrt"]`を差し替え、Windows分岐のロジック自体を検証する
    fake_msvcrt = _FakeMsvcrt()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

    lock_path = tmp_path / "watch.lock"
    first = ProcessLock(lock_path)
    second = ProcessLock(lock_path)

    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            second.acquire()
    finally:
        first.release()

    # 解放後は同じfdを別プロセス(ここでは同一プロセス内の別インスタンス)が再取得できる
    third = ProcessLock(lock_path)
    third.acquire()
    third.release()

    assert any(mode == fake_msvcrt.LK_NBLCK for _fd, mode in fake_msvcrt.locking_calls)
    assert any(mode == fake_msvcrt.LK_UNLCK for _fd, mode in fake_msvcrt.locking_calls)
