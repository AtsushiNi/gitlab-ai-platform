import dataclasses

import pytest

from gitlab_ai_platform.design import DesignInput, DesignResult
from gitlab_ai_platform.orchestrator import Uncertainty, UncertaintySeverity


def _design_input(**overrides) -> DesignInput:
    kwargs = dict(
        requirements=("要求1",),
        acceptance_criteria=("条件1",),
        assumptions=("前提1",),
        assumed_uncertainties=("エラーメッセージの文言は? → 一般的な文言を使う",),
    )
    kwargs.update(overrides)
    return DesignInput(**kwargs)


def _design_result(**overrides) -> DesignResult:
    kwargs = dict(
        design_document="# 責務\n...",
        uncertainties=(
            Uncertainty(question="q1", severity=UncertaintySeverity.CRITICAL),
        ),
    )
    kwargs.update(overrides)
    return DesignResult(**kwargs)


def test_design_input_is_frozen():
    design_input = _design_input()

    with pytest.raises(dataclasses.FrozenInstanceError):
        design_input.requirements = ()


def test_design_input_holds_fields_as_tuples():
    design_input = _design_input()

    assert design_input.requirements == ("要求1",)
    assert design_input.acceptance_criteria == ("条件1",)
    assert design_input.assumptions == ("前提1",)
    assert design_input.assumed_uncertainties == (
        "エラーメッセージの文言は? → 一般的な文言を使う",
    )


def test_design_result_is_frozen():
    result = _design_result()

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.design_document = "changed"


def test_design_result_holds_fields():
    result = _design_result()

    assert result.design_document == "# 責務\n..."
    assert len(result.uncertainties) == 1
    assert result.uncertainties[0].question == "q1"
