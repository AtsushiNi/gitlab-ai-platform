import pytest

from gitlab_ai_platform.issue_analysis import RequirementAnalysis
from gitlab_ai_platform.issue_analysis.job import (
    build_issue_analysis_job_result,
    build_resolved_issue_analysis_job_result,
)
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


# --- build_resolved_issue_analysis_job_result (M4-5) ---


def _waiting_human_result(**overrides) -> dict:
    kwargs = dict(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        requirements=["要求1"],
        acceptance_criteria=["条件1"],
        assumptions=["前提1"],
        assumed_uncertainties=[
            {
                "question": "エラーメッセージの文言は?",
                "severity": "minor",
                "assumption": "一般的な文言を使う",
            }
        ],
        questions=[
            {"question": "パスワード再設定フローも対象ですか?", "severity": "critical"}
        ],
    )
    kwargs.update(overrides)
    return kwargs


def test_resolved_result_moves_questions_into_resolved_questions_with_answers():
    result = _waiting_human_result()

    resolved = build_resolved_issue_analysis_job_result(
        result, ["対象です。パスワード再設定フローも含めてください。"]
    )

    assert resolved["questions"] == []
    assert resolved["resolved_questions"] == [
        {
            "question": "パスワード再設定フローも対象ですか?",
            "severity": "critical",
            "answer": "対象です。パスワード再設定フローも含めてください。",
        }
    ]


def test_resolved_result_merges_resolved_questions_into_assumed_uncertainties():
    result = _waiting_human_result()

    resolved = build_resolved_issue_analysis_job_result(
        result, ["対象です。パスワード再設定フローも含めてください。"]
    )

    # 元からのASSUME判定分(minor)に、今回ASKから解決した分(critical)が合流していること
    assert resolved["assumed_uncertainties"] == [
        {
            "question": "エラーメッセージの文言は?",
            "severity": "minor",
            "assumption": "一般的な文言を使う",
        },
        {
            "question": "パスワード再設定フローも対象ですか?",
            "severity": "critical",
            "assumption": "対象です。パスワード再設定フローも含めてください。",
        },
    ]


def test_resolved_result_preserves_other_fields_unchanged():
    result = _waiting_human_result()

    resolved = build_resolved_issue_analysis_job_result(
        result, ["対象です。パスワード再設定フローも含めてください。"]
    )

    assert resolved["project"] == _PROJECT
    assert resolved["issue_iid"] == _ISSUE_IID
    assert resolved["requirements"] == ["要求1"]
    assert resolved["acceptance_criteria"] == ["条件1"]
    assert resolved["assumptions"] == ["前提1"]


def test_resolved_result_with_no_questions_is_a_noop_for_questions():
    result = _waiting_human_result(questions=[], assumed_uncertainties=[])

    resolved = build_resolved_issue_analysis_job_result(result, [])

    assert resolved["questions"] == []
    assert resolved["resolved_questions"] == []
    assert resolved["assumed_uncertainties"] == []


def test_resolved_result_raises_value_error_on_answer_count_mismatch():
    result = _waiting_human_result()

    with pytest.raises(ValueError, match="answersの件数"):
        build_resolved_issue_analysis_job_result(result, [])
