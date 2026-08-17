"""Job Repository が送出する例外の基底クラス。

具体的なエラー分類(SQLite固有のエラーの変換等)は実装側の責務。ここではインターフェースと
して呼び出し側が握れる型だけを定義する(`store/errors.py`と同じ方針)。
"""

from __future__ import annotations


class JobError(Exception):
    """Job Repository経由の操作が失敗したことを表す基底例外。"""


class InvalidJobTransitionError(JobError):
    """許可されていない状態遷移が試みられたことを表す。

    `docs/adr/0016-job-abstraction.md`で決定した遷移(`PENDING → RUNNING`等)以外への
    `update_status`呼び出しで送出する。
    """


class JobNotFoundError(JobError):
    """指定した`job_id`のJobが存在しないことを表す。"""


class LeaseLostError(JobError):
    """`claim`で取得したリースが失効済みであることを表す(M3-2, `docs/adr/0017-job-queue.md`)。

    可視性タイムアウトで別workerに再取得された後、元のworkerが遅れて`heartbeat`/`complete`/
    `fail`を呼んだ場合など、要求元の`worker_id`が現在のリース所有者と一致しないときに送出する。
    """


__all__ = [
    "InvalidJobTransitionError",
    "JobError",
    "JobNotFoundError",
    "LeaseLostError",
]
