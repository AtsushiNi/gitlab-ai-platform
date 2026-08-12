"""実行ID(execution id)をログレコードに紐付けるためのコンテキスト管理。

MRレビューなど「1回の実行」単位でログを追跡できるようにするためのもの。
contextvarを使っているため、スレッドや非同期タスクをまたいでも実行ごとに独立する。
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Iterator
from contextvars import ContextVar

_execution_id: ContextVar[str | None] = ContextVar("execution_id", default=None)


def new_execution_id() -> str:
    """新しい実行IDを生成する。"""
    return uuid.uuid4().hex


def get_execution_id() -> str | None:
    """現在のコンテキストに設定されている実行IDを返す。未設定なら`None`。"""
    return _execution_id.get()


def set_execution_id(execution_id: str) -> None:
    """現在のコンテキストに実行IDを設定する。"""
    _execution_id.set(execution_id)


@contextlib.contextmanager
def execution_id_scope(execution_id: str | None = None) -> Iterator[str]:
    """指定した(省略時は新規発行した)実行IDを、withブロックの間だけ設定する。"""
    execution_id = execution_id or new_execution_id()
    token = _execution_id.set(execution_id)
    try:
        yield execution_id
    finally:
        _execution_id.reset(token)


class ExecutionIdFilter(logging.Filter):
    """LogRecordへ現在のコンテキストの実行IDを付与するFilter。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.execution_id = get_execution_id()
        return True
