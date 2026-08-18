import dataclasses

import pytest

from gitlab_ai_platform.issue_analysis import RequirementAnalysis
from gitlab_ai_platform.orchestrator import Uncertainty, UncertaintySeverity


def _analysis(**overrides) -> RequirementAnalysis:
    kwargs = dict(
        requirements=("要求1",),
        acceptance_criteria=("条件1",),
        assumptions=("前提1",),
        uncertainties=(
            Uncertainty(question="q1", severity=UncertaintySeverity.CRITICAL),
        ),
    )
    kwargs.update(overrides)
    return RequirementAnalysis(**kwargs)


def test_requirement_analysis_is_frozen():
    analysis = _analysis()

    with pytest.raises(dataclasses.FrozenInstanceError):
        analysis.requirements = ()


def test_requirement_analysis_holds_fields_as_tuples():
    analysis = _analysis()

    assert analysis.requirements == ("要求1",)
    assert analysis.acceptance_criteria == ("条件1",)
    assert analysis.assumptions == ("前提1",)
    assert len(analysis.uncertainties) == 1
    assert analysis.uncertainties[0].question == "q1"
