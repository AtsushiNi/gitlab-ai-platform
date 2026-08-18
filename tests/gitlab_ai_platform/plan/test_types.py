import dataclasses

import pytest

from gitlab_ai_platform.orchestrator import Uncertainty, UncertaintySeverity
from gitlab_ai_platform.plan import PlanInput, PlanResult, PlanTask


def _plan_input(**overrides) -> PlanInput:
    kwargs = dict(
        design_document="# 責務\n...",
        assumed_uncertainties=("エラーメッセージの文言は? → 一般的な文言を使う",),
    )
    kwargs.update(overrides)
    return PlanInput(**kwargs)


def _plan_result(**overrides) -> PlanResult:
    kwargs = dict(
        plan_document="# 概要\n...",
        tasks=(PlanTask(title="タスク1", description="内容1"),),
        uncertainties=(
            Uncertainty(question="q1", severity=UncertaintySeverity.CRITICAL),
        ),
    )
    kwargs.update(overrides)
    return PlanResult(**kwargs)


def test_plan_input_is_frozen():
    plan_input = _plan_input()

    with pytest.raises(dataclasses.FrozenInstanceError):
        plan_input.design_document = "changed"


def test_plan_input_holds_fields():
    plan_input = _plan_input()

    assert plan_input.design_document == "# 責務\n..."
    assert plan_input.assumed_uncertainties == (
        "エラーメッセージの文言は? → 一般的な文言を使う",
    )


def test_plan_task_is_frozen():
    task = PlanTask(title="タスク1", description="内容1")

    with pytest.raises(dataclasses.FrozenInstanceError):
        task.title = "changed"


def test_plan_task_holds_fields():
    task = PlanTask(title="タスク1", description="内容1")

    assert task.title == "タスク1"
    assert task.description == "内容1"


def test_plan_result_is_frozen():
    result = _plan_result()

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.plan_document = "changed"


def test_plan_result_holds_fields():
    result = _plan_result()

    assert result.plan_document == "# 概要\n..."
    assert len(result.tasks) == 1
    assert result.tasks[0].title == "タスク1"
    assert len(result.uncertainties) == 1
    assert result.uncertainties[0].question == "q1"
