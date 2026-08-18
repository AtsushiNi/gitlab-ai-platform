import pytest

from gitlab_ai_platform.orchestrator import (
    Uncertainty,
    UncertaintySeverity,
    judge_uncertainties,
)
from gitlab_ai_platform.plan import PlanResult, PlanTask
from gitlab_ai_platform.plan.job import (
    build_plan_job_payload,
    build_plan_job_result,
    build_resolved_plan_job_result,
    plan_job_payload_to_args,
)

_PROJECT = "group/project"
_ISSUE_IID = 7


def _design_result(**overrides) -> dict:
    kwargs = dict(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        design_document="# 責務\n...",
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


def _plan_result(**overrides) -> PlanResult:
    kwargs = dict(
        plan_document="# 概要\n...",
        tasks=(PlanTask(title="タスク1", description="内容1"),),
        uncertainties=(),
    )
    kwargs.update(overrides)
    return PlanResult(**kwargs)


# --- build_plan_job_payload / plan_job_payload_to_args ---


def test_build_plan_job_payload_carries_design_fields():
    payload = build_plan_job_payload(_PROJECT, _ISSUE_IID, _design_result())

    assert payload == {
        "project": _PROJECT,
        "issue_iid": _ISSUE_IID,
        "design_document": "# 責務\n...",
        "assumed_uncertainties": [
            {
                "question": "エラーメッセージの文言は?",
                "severity": "minor",
                "assumption": "一般的な文言を使う",
            }
        ],
    }


def test_plan_job_payload_to_args_round_trips():
    payload = build_plan_job_payload(_PROJECT, _ISSUE_IID, _design_result())

    project, issue_iid, plan_input = plan_job_payload_to_args(payload)

    assert project == _PROJECT
    assert issue_iid == _ISSUE_IID
    assert plan_input.design_document == "# 責務\n..."
    assert plan_input.assumed_uncertainties == (
        "エラーメッセージの文言は? → 一般的な文言を使う",
    )


def test_plan_job_payload_to_args_defaults_missing_optional_fields_to_empty():
    payload = {"project": _PROJECT, "issue_iid": _ISSUE_IID}

    _, _, plan_input = plan_job_payload_to_args(payload)

    assert plan_input.design_document == ""
    assert plan_input.assumed_uncertainties == ()


# --- build_plan_job_result ---


def test_result_with_no_uncertainties_has_empty_questions_and_assumed_uncertainties():
    plan = _plan_result()
    judgments = judge_uncertainties(plan.uncertainties)

    result = build_plan_job_result(_PROJECT, _ISSUE_IID, plan, judgments)

    assert result == {
        "project": _PROJECT,
        "issue_iid": _ISSUE_IID,
        "plan_document": "# 概要\n...",
        "tasks": [{"title": "タスク1", "description": "内容1"}],
        "assumed_uncertainties": [],
        "questions": [],
    }


def test_result_includes_multiple_tasks_in_order():
    plan = _plan_result(
        tasks=(
            PlanTask(title="タスク1", description="内容1"),
            PlanTask(title="タスク2", description="内容2"),
        )
    )
    judgments = judge_uncertainties(plan.uncertainties)

    result = build_plan_job_result(_PROJECT, _ISSUE_IID, plan, judgments)

    assert result["tasks"] == [
        {"title": "タスク1", "description": "内容1"},
        {"title": "タスク2", "description": "内容2"},
    ]


def test_result_includes_assumed_uncertainties_from_minor_severity():
    plan = _plan_result(
        uncertainties=(
            Uncertainty(
                question="タスク粒度はこれでよいか?",
                severity=UncertaintySeverity.MINOR,
                assumption="1コミット相当の粒度とした",
            ),
        )
    )
    judgments = judge_uncertainties(plan.uncertainties)

    result = build_plan_job_result(_PROJECT, _ISSUE_IID, plan, judgments)

    assert result["questions"] == []
    assert result["assumed_uncertainties"] == [
        {
            "question": "タスク粒度はこれでよいか?",
            "severity": "minor",
            "assumption": "1コミット相当の粒度とした",
        }
    ]


def test_result_includes_questions_from_critical_severity():
    plan = _plan_result(
        uncertainties=(
            Uncertainty(
                question="既存の移行ツールを再利用しますか?",
                severity=UncertaintySeverity.CRITICAL,
            ),
        )
    )
    judgments = judge_uncertainties(plan.uncertainties)

    result = build_plan_job_result(_PROJECT, _ISSUE_IID, plan, judgments)

    assert result["assumed_uncertainties"] == []
    assert result["questions"] == [
        {"question": "既存の移行ツールを再利用しますか?", "severity": "critical"}
    ]


# --- build_resolved_plan_job_result ---


def _waiting_human_result(**overrides) -> dict:
    kwargs = dict(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        plan_document="# 概要\n...",
        tasks=[{"title": "タスク1", "description": "内容1"}],
        assumed_uncertainties=[
            {
                "question": "タスク粒度はこれでよいか?",
                "severity": "minor",
                "assumption": "1コミット相当の粒度とした",
            }
        ],
        questions=[
            {"question": "既存の移行ツールを再利用しますか?", "severity": "critical"}
        ],
    )
    kwargs.update(overrides)
    return kwargs


def test_resolved_result_moves_questions_into_resolved_questions_with_answers():
    result = _waiting_human_result()

    resolved = build_resolved_plan_job_result(result, ["再利用してください。"])

    assert resolved["questions"] == []
    assert resolved["resolved_questions"] == [
        {
            "question": "既存の移行ツールを再利用しますか?",
            "severity": "critical",
            "answer": "再利用してください。",
        }
    ]


def test_resolved_result_merges_resolved_questions_into_assumed_uncertainties():
    result = _waiting_human_result()

    resolved = build_resolved_plan_job_result(result, ["再利用してください。"])

    assert resolved["assumed_uncertainties"] == [
        {
            "question": "タスク粒度はこれでよいか?",
            "severity": "minor",
            "assumption": "1コミット相当の粒度とした",
        },
        {
            "question": "既存の移行ツールを再利用しますか?",
            "severity": "critical",
            "assumption": "再利用してください。",
        },
    ]


def test_resolved_result_preserves_other_fields_unchanged():
    result = _waiting_human_result()

    resolved = build_resolved_plan_job_result(result, ["再利用してください。"])

    assert resolved["project"] == _PROJECT
    assert resolved["issue_iid"] == _ISSUE_IID
    assert resolved["plan_document"] == "# 概要\n..."
    assert resolved["tasks"] == [{"title": "タスク1", "description": "内容1"}]


def test_resolved_result_with_no_questions_is_a_noop_for_questions():
    result = _waiting_human_result(questions=[], assumed_uncertainties=[])

    resolved = build_resolved_plan_job_result(result, [])

    assert resolved["questions"] == []
    assert resolved["resolved_questions"] == []
    assert resolved["assumed_uncertainties"] == []


def test_resolved_result_raises_value_error_on_answer_count_mismatch():
    result = _waiting_human_result()

    with pytest.raises(ValueError, match="answersの件数"):
        build_resolved_plan_job_result(result, [])
