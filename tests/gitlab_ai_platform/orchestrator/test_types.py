import dataclasses

import pytest

from gitlab_ai_platform.orchestrator import (
    JudgmentAction,
    Uncertainty,
    UncertaintyJudgment,
    UncertaintySeverity,
)


def test_uncertainty_severity_values():
    assert UncertaintySeverity.CRITICAL == "critical"
    assert UncertaintySeverity.MINOR == "minor"


def test_judgment_action_values():
    assert JudgmentAction.ASK == "ask"
    assert JudgmentAction.ASSUME == "assume"


def test_uncertainty_is_frozen_and_defaults_to_no_assumption_or_phase():
    uncertainty = Uncertainty(
        question="APIのタイムアウトは何秒?", severity=UncertaintySeverity.CRITICAL
    )

    assert uncertainty.assumption is None
    assert uncertainty.phase is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        uncertainty.question = "別の質問"


def test_uncertainty_judgment_is_frozen():
    uncertainty = Uncertainty(
        question="対象は最新版のみ?",
        severity=UncertaintySeverity.MINOR,
        assumption="最新版のみを対象と仮定した",
    )
    judgment = UncertaintyJudgment(
        uncertainty=uncertainty,
        action=JudgmentAction.ASSUME,
        assumption_note=uncertainty.assumption,
    )

    assert judgment.assumption_note == "最新版のみを対象と仮定した"
    with pytest.raises(dataclasses.FrozenInstanceError):
        judgment.action = JudgmentAction.ASK
