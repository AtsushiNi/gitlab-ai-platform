"""構造化(JSON)ログ用のFormatter。"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

_RESERVED_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "execution_id"}


class JsonFormatter(logging.Formatter):
    """ログレコードを1行のJSONへ変換するFormatter。

    可観測性ツールでの取り込みを見越し、標準フィールド
    (timestamp/level/logger/message/execution_id)に加えて、
    `logger.info(..., extra={...})` で渡した任意フィールドもそのまま出力する。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "execution_id": getattr(record, "execution_id", None),
        }

        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_ATTRS
        }
        payload.update(extras)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)
