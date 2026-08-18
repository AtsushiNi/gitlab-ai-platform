import pytest

from gitlab_ai_platform.implement import ImplementResult
from gitlab_ai_platform.implement.job import (
    build_implement_job_payload,
    build_implement_job_result,
    build_resolved_implement_job_result,
    implement_job_payload_to_args,
)
from gitlab_ai_platform.orchestrator import (
    Uncertainty,
    UncertaintySeverity,
    judge_uncertainties,
)

_PROJECT = "group/project"
_ISSUE_IID = 7


def _plan_result(**overrides) -> dict:
    kwargs = dict(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        plan_document="# 概要\n...",
        tasks=[{"title": "タスク1", "description": "内容1"}],
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


def _implement_result(**overrides) -> ImplementResult:
    kwargs = dict(
        summary="タスク1を実装した",
        commit_message="タスク1を実装",
        tests_passed=True,
        uncertainties=(),
    )
    kwargs.update(overrides)
    return ImplementResult(**kwargs)


# --- build_implement_job_payload / implement_job_payload_to_args ---


def test_build_implement_job_payload_carries_plan_fields():
    payload = build_implement_job_payload(_PROJECT, _ISSUE_IID, _plan_result())

    assert payload == {
        "project": _PROJECT,
        "issue_iid": _ISSUE_IID,
        "plan_document": "# 概要\n...",
        "tasks": [{"title": "タスク1", "description": "内容1"}],
        "assumed_uncertainties": [
            {
                "question": "エラーメッセージの文言は?",
                "severity": "minor",
                "assumption": "一般的な文言を使う",
            }
        ],
    }


def test_implement_job_payload_to_args_round_trips():
    payload = build_implement_job_payload(_PROJECT, _ISSUE_IID, _plan_result())

    project, issue_iid, implement_input = implement_job_payload_to_args(payload)

    assert project == _PROJECT
    assert issue_iid == _ISSUE_IID
    assert implement_input.plan_document == "# 概要\n..."
    assert len(implement_input.tasks) == 1
    assert implement_input.tasks[0].title == "タスク1"
    assert implement_input.tasks[0].description == "内容1"
    assert implement_input.assumed_uncertainties == (
        "エラーメッセージの文言は? → 一般的な文言を使う",
    )


def test_implement_job_payload_to_args_defaults_missing_optional_fields_to_empty():
    payload = {"project": _PROJECT, "issue_iid": _ISSUE_IID}

    _, _, implement_input = implement_job_payload_to_args(payload)

    assert implement_input.plan_document == ""
    assert implement_input.tasks == ()
    assert implement_input.assumed_uncertainties == ()


# --- build_implement_job_result ---


def test_result_with_no_uncertainties_has_empty_questions_and_assumed_uncertainties():
    implement_result = _implement_result()
    judgments = judge_uncertainties(implement_result.uncertainties)

    result = build_implement_job_result(
        _PROJECT,
        _ISSUE_IID,
        implement_result=implement_result,
        commit_sha="after-sha",
        remote_branch="ai/issue-7",
        local_branch="issue-7",
        worktree_path="/tmp/workspace/issue-7",
        judgments=judgments,
    )

    assert result == {
        "project": _PROJECT,
        "issue_iid": _ISSUE_IID,
        "summary": "タスク1を実装した",
        "commit_message": "タスク1を実装",
        "commit_sha": "after-sha",
        "remote_branch": "ai/issue-7",
        "local_branch": "issue-7",
        "worktree_path": "/tmp/workspace/issue-7",
        "assumed_uncertainties": [],
        "questions": [],
    }


def test_result_includes_assumed_uncertainties_from_minor_severity():
    implement_result = _implement_result(
        uncertainties=(
            Uncertainty(
                question="エラーハンドリングの粒度は?",
                severity=UncertaintySeverity.MINOR,
                assumption="既存の例外型をそのまま再送出した",
            ),
        )
    )
    judgments = judge_uncertainties(implement_result.uncertainties)

    result = build_implement_job_result(
        _PROJECT,
        _ISSUE_IID,
        implement_result=implement_result,
        commit_sha="after-sha",
        remote_branch="ai/issue-7",
        local_branch="issue-7",
        worktree_path="/tmp/workspace/issue-7",
        judgments=judgments,
    )

    assert result["questions"] == []
    assert result["assumed_uncertainties"] == [
        {
            "question": "エラーハンドリングの粒度は?",
            "severity": "minor",
            "assumption": "既存の例外型をそのまま再送出した",
        }
    ]


def test_result_includes_questions_from_critical_severity():
    implement_result = _implement_result(
        uncertainties=(
            Uncertainty(
                question="既存の命名規則に従いますか?",
                severity=UncertaintySeverity.CRITICAL,
            ),
        )
    )
    judgments = judge_uncertainties(implement_result.uncertainties)

    result = build_implement_job_result(
        _PROJECT,
        _ISSUE_IID,
        implement_result=implement_result,
        commit_sha="after-sha",
        remote_branch="ai/issue-7",
        local_branch="issue-7",
        worktree_path="/tmp/workspace/issue-7",
        judgments=judgments,
    )

    assert result["assumed_uncertainties"] == []
    assert result["questions"] == [
        {"question": "既存の命名規則に従いますか?", "severity": "critical"}
    ]
    # 不明点があっても、既にcommit済みの事実(commit_sha等)は結果に残る
    assert result["commit_sha"] == "after-sha"


# --- build_resolved_implement_job_result ---


def _waiting_human_result(**overrides) -> dict:
    kwargs = dict(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        summary="タスク1を実装した",
        commit_message="タスク1を実装",
        commit_sha="after-sha",
        remote_branch="ai/issue-7",
        local_branch="issue-7",
        worktree_path="/tmp/workspace/issue-7",
        assumed_uncertainties=[],
        questions=[{"question": "既存の命名規則に従いますか?", "severity": "critical"}],
    )
    kwargs.update(overrides)
    return kwargs


def test_resolved_result_moves_questions_into_resolved_questions_with_answers():
    result = _waiting_human_result()

    resolved = build_resolved_implement_job_result(result, ["ケバブケースに従う"])

    assert resolved["questions"] == []
    assert resolved["resolved_questions"] == [
        {
            "question": "既存の命名規則に従いますか?",
            "severity": "critical",
            "answer": "ケバブケースに従う",
        }
    ]


def test_resolved_result_preserves_other_fields_unchanged():
    result = _waiting_human_result()

    resolved = build_resolved_implement_job_result(result, ["ケバブケースに従う"])

    assert resolved["project"] == _PROJECT
    assert resolved["issue_iid"] == _ISSUE_IID
    assert resolved["commit_sha"] == "after-sha"
    assert resolved["remote_branch"] == "ai/issue-7"


def test_resolved_result_raises_value_error_on_answer_count_mismatch():
    result = _waiting_human_result()

    with pytest.raises(ValueError, match="answersの件数"):
        build_resolved_implement_job_result(result, [])
