"""検出された複数MRのレビューを並行実行するワーカープール。

方針(M2-1 [#80](https://github.com/AtsushiNi/gitlab-ai-platform/issues/80)、
`docs/adr/0015-parallel-review-execution.md`、`docs/architecture.md`「MR Poller」「Windows/Linuxの
分担」):

- `concurrent.futures.ThreadPoolExecutor`を`max_workers`(=`config.max_parallel`)で構築し、
  投入された1件ごとのジョブ(`Callable[[], None]`)を並行実行する。プロセス内の複数スレッドで
  並行処理する方式を選び、別プロセス/コンテナへの分離はしない(`docs/architecture.md`の
  「Windows/Linuxの分担」により、M1〜M2のWindows実行では人間の端末上で完結させる方針のため。
  プロセス分離はM3以降のLinux/Docker移行時のスコープ)。
- 1件のジョブの失敗は他のジョブに影響させない(Issue #80の「失敗時の隔離」)。ジョブ自身が
  既知のパイプライン例外(`GitLabAdapterError`/`WorkspaceError`/`RunnerError`/`ReviewError`/
  `StateStoreError`)を捕まえてログに記録する想定(`cli/watch.py`の`build_on_detected`)で、
  このモジュールはジョブの中身を関知しない。ジョブが送出する「それ以外の想定外の例外」だけを
  捕まえ、`stop_event`をセットして`MrPoller.run`のループを早期終了させたうえで、
  `shutdown_and_reraise`で合成ルート側(`run_watch_loop`)に伝播させる
  (`docs/adr/0009-cli-watch-design.md`の「想定外の例外はプロセスを落とす」方針を維持)。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from ..logging_ import get_logger

_logger = get_logger(__name__)


class ReviewWorkerPool:
    """`max_workers`個までのスレッドでレビュージョブを並行実行するプール。

    `submit`は即座に戻り(ジョブの完了を待たない)、実際の実行は別スレッドで行う。
    """

    def __init__(self, max_workers: int, stop_event: threading.Event) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="review-worker"
        )
        # 想定外の例外を検知した際にセットし、MrPoller.runのポーリングループを止めるために使う
        self._stop_event = stop_event
        self._fatal_lock = threading.Lock()
        self._fatal_exc: BaseException | None = None

    def submit(self, job: Callable[[], None]) -> None:
        """`job`をプールに投入する。"""
        self._executor.submit(self._run, job)

    def _run(self, job: Callable[[], None]) -> None:
        try:
            job()
        except BaseException as exc:  # noqa: BLE001 - 想定外の例外を合成ルートまで運ぶため
            with self._fatal_lock:
                if self._fatal_exc is None:
                    self._fatal_exc = exc
            _logger.error(
                "watch.worker_pool_unexpected_error",
                extra={"error": str(exc)},
            )
            self._stop_event.set()

    def shutdown_and_reraise(self) -> None:
        """実行中のジョブの完了を待ってプールを終了し、想定外の例外があれば再送出する。

        未着手のジョブ(まだ実行が始まっていないもの)はキャンセルする。既に実行が
        始まっているジョブは完了まで待つ(1件のバグで他のMRの処理を中断しない、
        Issue #80の「失敗時の隔離」)。
        """
        self._executor.shutdown(wait=True, cancel_futures=True)
        if self._fatal_exc is not None:
            raise self._fatal_exc


__all__ = ["ReviewWorkerPool"]
