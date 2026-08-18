import dataclasses

import pytest

from gitlab_ai_platform.implement import ImplementInput, ImplementResult
from gitlab_ai_platform.orchestrator import Uncertainty, UncertaintySeverity
from gitlab_ai_platform.plan import PlanTask


def _implement_input(**overrides) -> ImplementInput:
    kwargs = dict(
        plan_document="# 概要\n...",
        tasks=(PlanTask(title="タスク1", description="内容1"),),
        assumed_uncertainties=("エラーメッセージの文言は? → 一般的な文言を使う",),
    )
    kwargs.update(overrides)
    return ImplementInput(**kwargs)


def _implement_result(**overrides) -> ImplementResult:
    kwargs = dict(
        summary="タスク1を実装した",
        commit_message="タスク1を実装",
        tests_passed=True,
        uncertainties=(
            Uncertainty(question="q1", severity=UncertaintySeverity.CRITICAL),
        ),
    )
    kwargs.update(overrides)
    return ImplementResult(**kwargs)


def test_implement_input_is_frozen():
    implement_input = _implement_input()

    with pytest.raises(dataclasses.FrozenInstanceError):
        implement_input.plan_document = "changed"


def test_implement_input_holds_fields():
    implement_input = _implement_input()

    assert implement_input.plan_document == "# 概要\n..."
    assert implement_input.tasks[0].title == "タスク1"
    assert implement_input.assumed_uncertainties == (
        "エラーメッセージの文言は? → 一般的な文言を使う",
    )


def test_implement_result_is_frozen():
    result = _implement_result()

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.summary = "changed"


def test_implement_result_holds_fields():
    result = _implement_result()

    assert result.summary == "タスク1を実装した"
    assert result.commit_message == "タスク1を実装"
    assert result.tests_passed is True
    assert len(result.uncertainties) == 1
    assert result.uncertainties[0].question == "q1"


def test_implement_result_allows_none_commit_message():
    result = _implement_result(commit_message=None, tests_passed=False)

    assert result.commit_message is None
    assert result.tests_passed is False
