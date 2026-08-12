from __future__ import annotations

import json
import logging
from pathlib import Path

from gitlab_ai_platform.logging_ import execution_id_scope, get_logger, setup_logging


def test_setup_logging_writes_json_line_to_file(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    setup_logging(log_dir=log_dir, console=False)

    logger = get_logger("gitlab_ai_platform.test")
    with execution_id_scope("exec-42"):
        logger.info("hello", extra={"mr_iid": 7})

    for handler in logging.getLogger().handlers:
        handler.flush()

    log_file = log_dir / "gitlab-ai-platform.log"
    assert log_file.exists()

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[-1])
    assert payload["message"] == "hello"
    assert payload["execution_id"] == "exec-42"
    assert payload["mr_iid"] == 7


def test_setup_logging_adds_console_handler_when_enabled(tmp_path: Path) -> None:
    setup_logging(log_dir=tmp_path / "logs", console=True)

    handlers = logging.getLogger().handlers
    assert any(isinstance(h, logging.StreamHandler) for h in handlers)


def test_setup_logging_omits_console_handler_when_disabled(tmp_path: Path) -> None:
    setup_logging(log_dir=tmp_path / "logs", console=False)

    handlers = logging.getLogger().handlers
    assert not any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        for h in handlers
    )


def test_setup_logging_is_idempotent_across_calls(tmp_path: Path) -> None:
    setup_logging(log_dir=tmp_path / "logs1", console=True)
    first_handler_count = len(logging.getLogger().handlers)

    setup_logging(log_dir=tmp_path / "logs2", console=True)

    assert len(logging.getLogger().handlers) == first_handler_count


def test_setup_logging_without_log_dir_only_configures_console(tmp_path: Path) -> None:
    setup_logging(log_dir=None, console=True)

    handlers = logging.getLogger().handlers
    assert len(handlers) == 1
    assert not any(isinstance(h, logging.FileHandler) for h in handlers)
