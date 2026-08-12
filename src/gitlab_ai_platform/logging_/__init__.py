"""アプリケーション全体のログ設定。

方針(M0-3 [#3](https://github.com/AtsushiNi/gitlab-ai-platform/issues/3)):

- 標準ライブラリの`logging`のみを使う(ADR-0001: 依存最小化)。
- コンソール(人間向け、Windowsでの対話利用)には読みやすいテキスト形式、
  ファイル(可観測性・後追い調査向け)にはJSON形式の構造化ログを出す。
- 実行(MRレビュー等)ごとに`execution_id`を付与し、並行実行時にもログを追跡できるようにする。
  付与は`execution_id_scope`で行い、ログ出力コード側は意識しなくてよい。
- ファイルログは日次でローテーションし、無限に肥大化しないようにする。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from .context import (
    ExecutionIdFilter,
    execution_id_scope,
    get_execution_id,
    new_execution_id,
    set_execution_id,
)
from .formatter import JsonFormatter

_CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s (%(execution_id)s): %(message)s"


def setup_logging(
    *,
    level: int | str = logging.INFO,
    log_dir: str | os.PathLike[str] | None = None,
    log_file_name: str = "gitlab-ai-platform.log",
    backup_days: int = 14,
    console: bool = True,
) -> None:
    """ルートロガーを設定する。

    プロセスの起点(CLIエントリポイント等)で一度だけ呼ぶことを想定している。
    `log_dir`を指定すると、日次ローテーションするJSON構造化ログをそこに書き出す。
    """
    root = logging.getLogger()
    root.setLevel(level)

    # 二重設定(テストの繰り返し実行等)を避けるため、既存ハンドラは一旦外す
    for handler in list(root.handlers):
        root.removeHandler(handler)

    execution_id_filter = ExecutionIdFilter()

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
        console_handler.addFilter(execution_id_filter)
        root.addHandler(console_handler)

    if log_dir is not None:
        log_dir_path = Path(log_dir)
        log_dir_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            log_dir_path / log_file_name,
            when="midnight",
            backupCount=backup_days,
            encoding="utf-8",
        )
        file_handler.setFormatter(JsonFormatter())
        file_handler.addFilter(execution_id_filter)
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """モジュール用のロガーを取得する。

    `setup_logging`を呼ぶ前でも動作する(標準ライブラリの`logging`同様、
    ハンドラ未設定のロガーを返す)。
    """
    return logging.getLogger(name)


__all__ = [
    "setup_logging",
    "get_logger",
    "execution_id_scope",
    "get_execution_id",
    "set_execution_id",
    "new_execution_id",
    "JsonFormatter",
]
