from __future__ import annotations

import threading
import time

import pytest

from gitlab_ai_platform.cli.worker_pool import ReviewWorkerPool

# 各テストは「全ジョブが実際に完了した」ことを自前のEvent/カウンタで確認してから
# shutdown_and_reraise()を呼ぶ。shutdown()はcancel_futures=Trueで未着手のジョブを
# キャンセルするため、投入直後に呼ぶと(ワーカー数より投入数が多い場合)後続ジョブが
# 実行されないままキャンセルされてしまい、テストが意図と異なる形で「たまたま通る」
# 事故を避けるため


def test_submit_runs_job_in_background_thread():
    done = threading.Event()
    pool = ReviewWorkerPool(2, threading.Event())

    pool.submit(done.set)

    assert done.wait(timeout=5)
    pool.shutdown_and_reraise()


def test_shutdown_and_reraise_does_not_raise_without_fatal_exception():
    pool = ReviewWorkerPool(2, threading.Event())
    done = threading.Event()

    pool.submit(done.set)

    assert done.wait(timeout=5)
    pool.shutdown_and_reraise()  # 例外を送出しない


def test_submit_bounds_concurrency_to_max_workers():
    max_workers = 2
    total_jobs = 6
    lock = threading.Lock()
    active = 0
    max_active = 0
    completed = 0
    all_done = threading.Event()

    def job() -> None:
        nonlocal active, max_active, completed
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
            completed += 1
            if completed == total_jobs:
                all_done.set()

    pool = ReviewWorkerPool(max_workers, threading.Event())
    for _ in range(total_jobs):
        pool.submit(job)

    assert all_done.wait(timeout=5)
    pool.shutdown_and_reraise()

    assert max_active <= max_workers
    assert max_active > 1  # 実際に複数ジョブが同時実行された(単なる逐次実行ではない)


def test_unexpected_exception_sets_stop_event_and_is_reraised_on_shutdown():
    stop_event = threading.Event()
    pool = ReviewWorkerPool(1, stop_event)
    done = threading.Event()

    def failing_job() -> None:
        try:
            raise RuntimeError("boom")
        finally:
            done.set()

    pool.submit(failing_job)

    assert done.wait(timeout=5)
    with pytest.raises(RuntimeError, match="boom"):
        pool.shutdown_and_reraise()
    assert stop_event.is_set()


def test_one_job_failure_does_not_prevent_other_jobs_from_running():
    # Issue #80「失敗時の隔離」: 1件のジョブが想定外の例外を送出しても、他のジョブは
    # そのまま実行され続けることを検証する
    results: list[int] = []
    lock = threading.Lock()
    completed = 0
    total = 3
    all_done = threading.Event()

    def _mark_done() -> None:
        nonlocal completed
        with lock:
            completed += 1
            if completed == total:
                all_done.set()

    def ok_job(n: int) -> None:
        with lock:
            results.append(n)
        _mark_done()

    def failing_job() -> None:
        try:
            raise RuntimeError("boom")
        finally:
            _mark_done()

    pool = ReviewWorkerPool(3, threading.Event())
    pool.submit(lambda: ok_job(1))
    pool.submit(failing_job)
    pool.submit(lambda: ok_job(2))

    assert all_done.wait(timeout=5)
    with pytest.raises(RuntimeError, match="boom"):
        pool.shutdown_and_reraise()

    assert sorted(results) == [1, 2]
