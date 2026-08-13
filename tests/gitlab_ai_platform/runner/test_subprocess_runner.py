from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gitlab_ai_platform.runner import (
    ClaudeCodeNotFoundError,
    ClaudeCodeOutputError,
    ClaudeCodeTimeoutError,
    ReviewContext,
    SubprocessClaudeCodeRunner,
)

_SUCCESS_JSON = json.dumps(
    {
        "is_error": False,
        "result": "LGTM",
        "session_id": "sess-1",
        "terminal_reason": "completed",
        "permission_denials": [],
        "num_turns": 3,
        "total_cost_usd": 0.05,
    }
)


class _FakePopen:
    """`subprocess.Popen`のダミー実装。`communicate`呼び出しごとの挙動を`outcomes`で指定する。

    `outcomes`の各要素は`"timeout"`(TimeoutExpiredを送出する)または
    `(stdout, stderr)`のタプル(その内容を返す)。
    """

    def __init__(self, command: list[str], *, outcomes: list, returncode: int = 0, **kwargs) -> None:
        self.args = command
        self.kwargs = kwargs
        self._outcomes = list(outcomes)
        self.returncode = returncode
        self.terminate_called = False
        self.kill_called = False

    def communicate(self, timeout: float | None = None):
        outcome = self._outcomes.pop(0)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(cmd=self.args, timeout=timeout)
        return outcome

    def terminate(self) -> None:
        self.terminate_called = True

    def kill(self) -> None:
        self.kill_called = True


def _popen_factory(outcomes: list, *, returncode: int = 0, recorded_calls: list | None = None):
    def factory(command: list[str], **kwargs) -> _FakePopen:
        if recorded_calls is not None:
            recorded_calls.append((command, kwargs))
        return _FakePopen(command, outcomes=outcomes, returncode=returncode, **kwargs)

    return factory


def test_run_executes_claude_and_parses_json_result(tmp_path: Path, review_context: ReviewContext):
    calls: list = []
    runner = SubprocessClaudeCodeRunner(
        tmp_path / "logs",
        popen=_popen_factory([(_SUCCESS_JSON, "")], recorded_calls=calls),
    )
    worktree_path = tmp_path / "worktree"

    result = runner.run(
        worktree_path, "Review this MR carefully.", review_context, timeout_seconds=60
    )

    assert result.is_error is False
    assert result.result_text == "LGTM"
    assert result.session_id == "sess-1"
    assert result.terminal_reason == "completed"
    assert result.num_turns == 3
    assert result.total_cost_usd == 0.05
    assert result.timed_out is False
    assert result.log_path.exists()

    (command, kwargs) = calls[0]
    assert command[:2] == ["claude", "-p"]
    assert command[-2:] == ["--output-format", "json"]
    assert kwargs["cwd"] == str(worktree_path)


def test_run_includes_instructions_and_context_in_prompt(
    tmp_path: Path, review_context: ReviewContext
):
    calls: list = []
    runner = SubprocessClaudeCodeRunner(
        tmp_path / "logs", popen=_popen_factory([(_SUCCESS_JSON, "")], recorded_calls=calls)
    )

    runner.run(
        tmp_path / "worktree", "Review this MR carefully.", review_context, timeout_seconds=60
    )

    prompt = calls[0][0][2]
    assert "Review this MR carefully." in prompt
    assert review_context.merge_request.title in prompt
    assert review_context.merge_request.description in prompt
    assert review_context.diffs[0].diff in prompt
    assert "please add a test" in prompt
    # systemノート(discussion内のシステムイベント)はコメントとしてプロンプトに含めない
    assert "changed target branch" not in prompt


def test_run_builds_permission_flags(tmp_path: Path, review_context: ReviewContext):
    calls: list = []
    runner = SubprocessClaudeCodeRunner(
        tmp_path / "logs", popen=_popen_factory([(_SUCCESS_JSON, "")], recorded_calls=calls)
    )

    runner.run(
        tmp_path / "worktree",
        "instructions",
        review_context,
        timeout_seconds=60,
        allowed_tools=["Read", "Grep"],
        disallowed_tools=["Bash"],
        permission_mode="acceptEdits",
    )

    command = calls[0][0]
    assert "--permission-mode" in command
    assert command[command.index("--permission-mode") + 1] == "acceptEdits"
    assert "--allowedTools" in command
    assert command[command.index("--allowedTools") + 1] == "Read Grep"
    assert "--disallowedTools" in command
    assert command[command.index("--disallowedTools") + 1] == "Bash"
    # 危険な全許可フラグはRunnerのインターフェース上どこにも現れない
    assert "--dangerously-skip-permissions" not in command


def test_run_raises_claude_code_not_found_error_when_binary_missing(
    tmp_path: Path, review_context: ReviewContext
):
    def factory(command, **kwargs):
        raise FileNotFoundError()

    runner = SubprocessClaudeCodeRunner(tmp_path / "logs", popen=factory)

    with pytest.raises(ClaudeCodeNotFoundError):
        runner.run(tmp_path / "worktree", "instructions", review_context, timeout_seconds=60)


def test_run_terminates_gracefully_on_timeout_and_returns_result(
    tmp_path: Path, review_context: ReviewContext
):
    # spike §4の実測: SIGTERM後もClaude Codeは最終結果JSON(terminal_reason: aborted_*)を
    # 出力してから終了することがある。この場合は例外にせず、通常のRunResultとして扱う
    aborted_json = json.dumps(
        {
            "is_error": True,
            "result": "aborted",
            "session_id": "sess-2",
            "terminal_reason": "aborted_tools",
            "permission_denials": [],
            "num_turns": 1,
            "total_cost_usd": 0.01,
        }
    )
    captured_popen: list[_FakePopen] = []

    def factory(command, **kwargs):
        popen = _FakePopen(command, outcomes=["timeout", (aborted_json, "")], **kwargs)
        captured_popen.append(popen)
        return popen

    runner = SubprocessClaudeCodeRunner(tmp_path / "logs", popen=factory)

    result = runner.run(
        tmp_path / "worktree", "instructions", review_context, timeout_seconds=5
    )

    assert result.timed_out is True
    assert result.is_error is True
    assert result.terminal_reason == "aborted_tools"
    assert captured_popen[0].terminate_called is True
    assert captured_popen[0].kill_called is False


def test_run_kills_process_when_it_does_not_respond_to_terminate(
    tmp_path: Path, review_context: ReviewContext
):
    captured_popen: list[_FakePopen] = []

    def factory(command, **kwargs):
        popen = _FakePopen(command, outcomes=["timeout", "timeout", ("", "")], **kwargs)
        captured_popen.append(popen)
        return popen

    runner = SubprocessClaudeCodeRunner(
        tmp_path / "logs", terminate_grace_seconds=1, popen=factory
    )

    with pytest.raises(ClaudeCodeTimeoutError) as exc_info:
        runner.run(tmp_path / "worktree", "instructions", review_context, timeout_seconds=5)

    assert captured_popen[0].terminate_called is True
    assert captured_popen[0].kill_called is True
    assert exc_info.value.log_path.exists()


def test_run_raises_output_error_for_invalid_json(tmp_path: Path, review_context: ReviewContext):
    runner = SubprocessClaudeCodeRunner(
        tmp_path / "logs", popen=_popen_factory([("not json", "some stderr")])
    )

    with pytest.raises(ClaudeCodeOutputError) as exc_info:
        runner.run(tmp_path / "worktree", "instructions", review_context, timeout_seconds=60)

    assert exc_info.value.log_path.exists()
    assert exc_info.value.stdout == "not json"


def test_write_log_saves_command_and_output_without_leaking_env(
    tmp_path: Path, review_context: ReviewContext
):
    log_dir = tmp_path / "logs"
    runner = SubprocessClaudeCodeRunner(
        log_dir,
        env={"AWS_SECRET_ACCESS_KEY": "super-secret"},
        popen=_popen_factory([(_SUCCESS_JSON, "")]),
    )

    result = runner.run(
        tmp_path / "worktree", "instructions", review_context, timeout_seconds=60
    )

    expected_dir = log_dir / "group%2Fproject" / "mr-42"
    assert result.log_path.parent == expected_dir

    saved = json.loads(result.log_path.read_text(encoding="utf-8"))
    assert saved["stdout"] == _SUCCESS_JSON
    assert saved["timed_out"] is False
    assert "super-secret" not in result.log_path.read_text(encoding="utf-8")
