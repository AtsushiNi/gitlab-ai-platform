from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from gitlab_ai_platform.runner import ClaudeCodeRunner, ReviewContext, RunResult

# M4-3([#109], docs/adr/0027-issue-analysis-runner-execution.md): `run`(MRレビュー専用、
# instructions+ReviewContextをRunner内部で結合)と対になる`run_prompt`(組み立て済みの
# プロンプトをそのまま実行する汎用の経路)を追加した
_EXPECTED_PUBLIC_METHODS = {"run", "run_prompt"}


def _public_methods(protocol_cls: type) -> set[str]:
    return {name for name in dir(protocol_cls) if not name.startswith("_")}


def test_claude_code_runner_exposes_only_expected_operations():
    # Runnerは「渡されたコンテキストで実行する」だけ(docs/architecture.mdの境界)という
    # 責務を、将来メソッドが増えた際にこのテストで検知できるようにする
    assert _public_methods(ClaudeCodeRunner) == _EXPECTED_PUBLIC_METHODS


class _FakeClaudeCodeRunner:
    """Protocolを満たすダミー実装。subprocess実装(SubprocessClaudeCodeRunner)もこの形になる。"""

    def run(
        self,
        worktree_path: Path,
        instructions: str,
        context: ReviewContext,
        *,
        timeout_seconds: int,
        allowed_tools: Sequence[str] = (),
        disallowed_tools: Sequence[str] = (),
        permission_mode: str | None = None,
    ) -> RunResult:
        return RunResult(
            is_error=False,
            result_text="ok",
            session_id="s1",
            terminal_reason="completed",
            permission_denials=(),
            num_turns=1,
            total_cost_usd=0.0,
            timed_out=False,
            duration_seconds=1.0,
            log_path=Path("/tmp/log.json"),
            raw={},
        )

    def run_prompt(
        self,
        worktree_path: Path,
        prompt: str,
        *,
        log_key: str,
        timeout_seconds: int,
        allowed_tools: Sequence[str] = (),
        disallowed_tools: Sequence[str] = (),
        permission_mode: str | None = None,
    ) -> RunResult:
        return RunResult(
            is_error=False,
            result_text="ok",
            session_id="s1",
            terminal_reason="completed",
            permission_denials=(),
            num_turns=1,
            total_cost_usd=0.0,
            timed_out=False,
            duration_seconds=1.0,
            log_path=Path("/tmp/log.json"),
            raw={},
        )


def test_fake_claude_code_runner_satisfies_protocol():
    runner = _FakeClaudeCodeRunner()

    assert isinstance(runner, ClaudeCodeRunner)
