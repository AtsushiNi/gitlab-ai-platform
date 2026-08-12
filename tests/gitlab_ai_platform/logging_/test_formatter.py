from __future__ import annotations

import json
import logging
import sys

from gitlab_ai_platform.logging_.formatter import JsonFormatter


def _make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_format_outputs_expected_fields() -> None:
    record = _make_record(execution_id="exec-1")

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["execution_id"] == "exec-1"
    assert "timestamp" in payload


def test_format_defaults_execution_id_to_none_when_absent() -> None:
    record = _make_record()

    payload = json.loads(JsonFormatter().format(record))

    assert payload["execution_id"] is None


def test_format_includes_extra_fields() -> None:
    record = _make_record(execution_id=None, mr_iid=42)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["mr_iid"] == 42


def test_format_includes_exception_info() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    payload = json.loads(JsonFormatter().format(record))

    assert "ValueError: boom" in payload["exception"]
