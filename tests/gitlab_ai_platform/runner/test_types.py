from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from gitlab_ai_platform.runner import ReviewContext, RunResult


def test_review_context_is_immutable(review_context: ReviewContext):
    with pytest.raises(dataclasses.FrozenInstanceError):
        review_context.diffs = ()  # type: ignore[misc]


def test_run_result_is_immutable():
    result = RunResult(
        is_error=False,
        result_text="ok",
        session_id="s1",
        terminal_reason="completed",
        permission_denials=(),
        num_turns=1,
        total_cost_usd=0.01,
        timed_out=False,
        duration_seconds=1.0,
        log_path=Path("/tmp/log.json"),
        raw={},
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.is_error = True  # type: ignore[misc]
