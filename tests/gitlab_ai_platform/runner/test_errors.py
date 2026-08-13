from __future__ import annotations

from pathlib import Path

from gitlab_ai_platform.runner import (
    ClaudeCodeNotFoundError,
    ClaudeCodeOutputError,
    ClaudeCodeTimeoutError,
    RunnerError,
)


def test_claude_code_not_found_error_is_a_runner_error():
    assert issubclass(ClaudeCodeNotFoundError, RunnerError)


def test_claude_code_not_found_error_holds_log_path():
    error = ClaudeCodeNotFoundError("not found", log_path=Path("/tmp/log.json"))

    assert error.log_path == Path("/tmp/log.json")


def test_claude_code_timeout_error_is_a_runner_error():
    assert issubclass(ClaudeCodeTimeoutError, RunnerError)


def test_claude_code_output_error_is_a_runner_error():
    assert issubclass(ClaudeCodeOutputError, RunnerError)


def test_claude_code_timeout_error_holds_context():
    error = ClaudeCodeTimeoutError(
        "timeout", timeout_seconds=30, log_path=Path("/tmp/log.json"), stderr="stderr"
    )

    assert error.timeout_seconds == 30
    assert error.log_path == Path("/tmp/log.json")
    assert error.stderr == "stderr"


def test_claude_code_output_error_holds_context():
    error = ClaudeCodeOutputError(
        "bad output",
        returncode=1,
        log_path=Path("/tmp/log.json"),
        stdout="not json",
        stderr="",
    )

    assert error.returncode == 1
    assert error.log_path == Path("/tmp/log.json")
    assert error.stdout == "not json"
