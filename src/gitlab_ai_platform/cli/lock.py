"""常駐(watch)モードの多重起動防止用プロセスロック。

方針(M1-11 [#39](https://github.com/AtsushiNi/gitlab-ai-platform/issues/39)、
`docs/adr/0009-cli-watch-design.md`):

- 同一`state_db_path`に対して複数の`watch`プロセスが同時に起動すると、`poll_once`〜
  `execute_review`のウィンドウで2プロセスが同じ未処理commitを同時に検出し得る
  (`store.create`の一意制約は防いでくれるが、二重にClaude Codeを起動するだけ無駄になる)。
  ロックファイルに対するOSのアドバイザリロックで、2つ目のプロセスの起動自体を防ぐ。
- ロックはファイルディスクリプタに紐づくOSレベルの機構(`fcntl.flock`/`msvcrt.locking`)を使う。
  プロセスが異常終了してもOSがプロセス終了時に自動的に解放するため、PIDファイル+存在チェック
  方式(前回異常終了時のファイルが残ったまま次回起動できなくなる、PIDが再利用されると誤判定する)
  で起きがちな「ロックの消し忘れでデッドロックする」問題が起きない。
- Windows(`msvcrt`)/POSIX(`fcntl`)の両方に対応する。このCLIはWindows上での実行が前提
  (`docs/architecture.md`「Windows/Linuxの分担」)なため必須の対応。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType


class AlreadyRunningError(RuntimeError):
    """同じロックファイルを別プロセスが既に保持していることを表す。"""


class ProcessLock:
    """`lock_path`に対する排他ロックを取得・解放する。多重起動防止に使う。"""

    def __init__(self, lock_path: Path | str) -> None:
        self._lock_path = Path(lock_path)
        self._file: object | None = None

    def acquire(self) -> None:
        """ロックを取得する。既に別プロセスが保持していれば`AlreadyRunningError`を送出する。"""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        file = open(self._lock_path, "a+")  # noqa: SIM115 - releaseまで保持する
        try:
            _lock_exclusive_nonblocking(file)
        except OSError as exc:
            file.close()
            raise AlreadyRunningError(
                f"別のwatchプロセスが既に実行中です(ロックファイル: {self._lock_path})"
            ) from exc

        # PID記録は診断用(`ps`/タスクマネージャでロック保持プロセスを特定する手掛かり)。
        # ロックの正当性自体はOSのアドバイザリロックに依存しており、この内容の読み書きには依存しない
        file.seek(0)
        file.truncate()
        file.write(str(os.getpid()))
        file.flush()
        self._file = file

    def release(self) -> None:
        """ロックを解放する。未取得の状態で呼んでも何もしない。"""
        if self._file is None:
            return
        file = self._file
        self._file = None
        _unlock(file)
        file.close()

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


def _lock_exclusive_nonblocking(file) -> None:
    if sys.platform == "win32":
        import msvcrt

        # msvcrt.lockingは対象領域(ここでは1バイト目)にデータが存在する必要があるため、
        # 空ファイルの場合は先に1バイト書いてから先頭をロックする
        file.seek(0, os.SEEK_END)
        if file.tell() == 0:
            file.write("0")
            file.flush()
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(file) -> None:
    if sys.platform == "win32":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


__all__ = ["ProcessLock", "AlreadyRunningError"]
