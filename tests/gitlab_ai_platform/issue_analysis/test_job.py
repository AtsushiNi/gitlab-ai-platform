from gitlab_ai_platform.issue_analysis import RequirementAnalysis
from gitlab_ai_platform.issue_analysis.job import build_issue_analysis_job_result
from gitlab_ai_platform.orchestrator import (
    Uncertainty,
    UncertaintySeverity,
    judge_uncertainties,
)

_PROJECT = "group/project"
_ISSUE_IID = 7


def _analysis(**overrides) -> RequirementAnalysis:
    kwargs = dict(
        requirements=("要求1",),
        acceptance_criteria=("条件1",),
        assumptions=("前提1",),
        uncertainties=(),
    )
    kwargs.update(overrides)
    return RequirementAnalysis(**kwargs)


def test_result_with_no_uncertainties_has_empty_questions_and_assumed_uncertainties():
    analysis = _analysis()
    judgments = judge_uncertainties(analysis.uncertainties)

    result = build_issue_analysis_job_result(_PROJECT, _ISSUE_IID, analysis, judgments)

    assert result == {
        "project": _PROJECT,
        "issue_iid": _ISSUE_IID,
        "requirements": ["要求1"],
        "acceptance_criteria": ["条件1"],
        "assumptions": ["前提1"],
        "assumed_uncertainties": [],
        "questions": [],
    }


def test_result_includes_assumed_uncertainties_from_minor_severity():
    analysis = _analysis(
        uncertainties=(
            Uncertainty(
                question="エラーメッセージの文言は?",
                severity=UncertaintySeverity.MINOR,
                assumption="一般的な文言を使う",
            ),
        )
    )
    judgments = judge_uncertainties(analysis.uncertainties)

    result = build_issue_analysis_job_result(_PROJECT, _ISSUE_IID, analysis, judgments)

    assert result["questions"] == []
    assert result["assumed_uncertainties"] == [
        {
            "question": "エラーメッセージの文言は?",
            "severity": "minor",
            "assumption": "一般的な文言を使う",
        }
    ]


def test_result_includes_questions_from_critical_severity():
    analysis = _analysis(
        uncertainties=(
            Uncertainty(
                question="パスワード再設定フローも対象ですか?",
                severity=UncertaintySeverity.CRITICAL,
            ),
        )
    )
    judgments = judge_uncertainties(analysis.uncertainties)

    result = build_issue_analysis_job_result(_PROJECT, _ISSUE_IID, analysis, judgments)

    assert result["assumed_uncertainties"] == []
    assert result["questions"] == [
        {"question": "パスワード再設定フローも対象ですか?", "severity": "critical"}
    ]
