import pytest

from gitlab_ai_platform.design import DesignResult
from gitlab_ai_platform.design.job import (
    build_design_job_payload,
    build_design_job_result,
    build_resolved_design_job_result,
    design_job_payload_to_args,
)
from gitlab_ai_platform.orchestrator import (
    Uncertainty,
    UncertaintySeverity,
    judge_uncertainties,
)

_PROJECT = "group/project"
_ISSUE_IID = 7


def _analysis_result(**overrides) -> dict:
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
        questions=[],
    )
    kwargs.update(overrides)
    return kwargs


def _design_result(**overrides) -> DesignResult:
    kwargs = dict(design_document="# 責務\n...", uncertainties=())
    kwargs.update(overrides)
    return DesignResult(**kwargs)


# --- build_design_job_payload / design_job_payload_to_args ---


def test_build_design_job_payload_carries_analysis_fields():
    payload = build_design_job_payload(_PROJECT, _ISSUE_IID, _analysis_result())

    assert payload == {
        "project": _PROJECT,
        "issue_iid": _ISSUE_IID,
        "requirements": ["要求1"],
        "acceptance_criteria": ["条件1"],
        "assumptions": ["前提1"],
        "assumed_uncertainties": [
            {
                "question": "エラーメッセージの文言は?",
                "severity": "minor",
                "assumption": "一般的な文言を使う",
            }
        ],
    }


def test_design_job_payload_to_args_round_trips():
    payload = build_design_job_payload(_PROJECT, _ISSUE_IID, _analysis_result())

    project, issue_iid, design_input = design_job_payload_to_args(payload)

    assert project == _PROJECT
    assert issue_iid == _ISSUE_IID
    assert design_input.requirements == ("要求1",)
    assert design_input.acceptance_criteria == ("条件1",)
    assert design_input.assumptions == ("前提1",)
    assert design_input.assumed_uncertainties == (
        "エラーメッセージの文言は? → 一般的な文言を使う",
    )


def test_design_job_payload_to_args_defaults_missing_optional_fields_to_empty():
    payload = {"project": _PROJECT, "issue_iid": _ISSUE_IID}

    _, _, design_input = design_job_payload_to_args(payload)

    assert design_input.requirements == ()
    assert design_input.acceptance_criteria == ()
    assert design_input.assumptions == ()
    assert design_input.assumed_uncertainties == ()


# --- build_design_job_result ---


def test_result_with_no_uncertainties_has_empty_questions_and_assumed_uncertainties():
    design = _design_result()
    judgments = judge_uncertainties(design.uncertainties)

    result = build_design_job_result(_PROJECT, _ISSUE_IID, design, judgments)

    assert result == {
        "project": _PROJECT,
        "issue_iid": _ISSUE_IID,
        "design_document": "# 責務\n...",
        "assumed_uncertainties": [],
        "questions": [],
    }


def test_result_includes_assumed_uncertainties_from_minor_severity():
    design = _design_result(
        uncertainties=(
            Uncertainty(
                question="キャッシュ戦略は?",
                severity=UncertaintySeverity.MINOR,
                assumption="単純なTTLキャッシュを使う",
            ),
        )
    )
    judgments = judge_uncertainties(design.uncertainties)

    result = build_design_job_result(_PROJECT, _ISSUE_IID, design, judgments)

    assert result["questions"] == []
    assert result["assumed_uncertainties"] == [
        {
            "question": "キャッシュ戦略は?",
            "severity": "minor",
            "assumption": "単純なTTLキャッシュを使う",
        }
    ]


def test_result_includes_questions_from_critical_severity():
    design = _design_result(
        uncertainties=(
            Uncertainty(
                question="既存の認証基盤を再利用しますか?",
                severity=UncertaintySeverity.CRITICAL,
            ),
        )
    )
    judgments = judge_uncertainties(design.uncertainties)

    result = build_design_job_result(_PROJECT, _ISSUE_IID, design, judgments)

    assert result["assumed_uncertainties"] == []
    assert result["questions"] == [
        {"question": "既存の認証基盤を再利用しますか?", "severity": "critical"}
    ]


# --- build_resolved_design_job_result ---


def _waiting_human_result(**overrides) -> dict:
    kwargs = dict(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        design_document="# 責務\n...",
        assumed_uncertainties=[
            {
                "question": "キャッシュ戦略は?",
                "severity": "minor",
                "assumption": "単純なTTLキャッシュを使う",
            }
        ],
        questions=[
            {"question": "既存の認証基盤を再利用しますか?", "severity": "critical"}
        ],
    )
    kwargs.update(overrides)
    return kwargs


def test_resolved_result_moves_questions_into_resolved_questions_with_answers():
    result = _waiting_human_result()

    resolved = build_resolved_design_job_result(result, ["再利用してください。"])

    assert resolved["questions"] == []
    assert resolved["resolved_questions"] == [
        {
            "question": "既存の認証基盤を再利用しますか?",
            "severity": "critical",
            "answer": "再利用してください。",
        }
    ]


def test_resolved_result_merges_resolved_questions_into_assumed_uncertainties():
    result = _waiting_human_result()

    resolved = build_resolved_design_job_result(result, ["再利用してください。"])

    assert resolved["assumed_uncertainties"] == [
        {
            "question": "キャッシュ戦略は?",
            "severity": "minor",
            "assumption": "単純なTTLキャッシュを使う",
        },
        {
            "question": "既存の認証基盤を再利用しますか?",
            "severity": "critical",
            "assumption": "再利用してください。",
        },
    ]


def test_resolved_result_preserves_other_fields_unchanged():
    result = _waiting_human_result()

    resolved = build_resolved_design_job_result(result, ["再利用してください。"])

    assert resolved["project"] == _PROJECT
    assert resolved["issue_iid"] == _ISSUE_IID
    assert resolved["design_document"] == "# 責務\n..."


def test_resolved_result_with_no_questions_is_a_noop_for_questions():
    result = _waiting_human_result(questions=[], assumed_uncertainties=[])

    resolved = build_resolved_design_job_result(result, [])

    assert resolved["questions"] == []
    assert resolved["resolved_questions"] == []
    assert resolved["assumed_uncertainties"] == []


def test_resolved_result_raises_value_error_on_answer_count_mismatch():
    result = _waiting_human_result()

    with pytest.raises(ValueError, match="answersの件数"):
        build_resolved_design_job_result(result, [])
