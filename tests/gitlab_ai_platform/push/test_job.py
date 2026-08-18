from gitlab_ai_platform.gitlab_adapter.types import MergeRequest
from gitlab_ai_platform.push import (
    build_push_job_payload,
    build_push_job_result,
    push_job_payload_to_args,
)

_PROJECT = "group/project"
_ISSUE_IID = 7


def _implement_payload(**overrides) -> dict:
    kwargs = dict(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        plan_document="# 概要\n実装計画の本文です。",
        tasks=[{"title": "タスク1", "description": "内容1"}],
        assumed_uncertainties=[],
    )
    kwargs.update(overrides)
    return kwargs


def _implement_result(**overrides) -> dict:
    kwargs = dict(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        summary="タスク1を実装した",
        commit_message="タスク1を実装",
        commit_sha="after-sha",
        remote_branch="ai/issue-7",
        local_branch="issue-7",
        worktree_path="/tmp/workspace/issue-7",
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


def _merge_request(**overrides) -> MergeRequest:
    kwargs = dict(
        project=_PROJECT,
        iid=42,
        title="[Issue #7] タスク1を実装した",
        description="...",
        state="opened",
        source_branch="ai/issue-7",
        target_branch="main",
        sha="pushed-sha",
        author="ai-bot",
        web_url="https://gitlab.example.com/group/project/-/merge_requests/42",
    )
    kwargs.update(overrides)
    return MergeRequest(**kwargs)


# --- build_push_job_payload / push_job_payload_to_args ---


def test_build_push_job_payload_carries_plan_document_from_implement_payload():
    payload = build_push_job_payload(
        _PROJECT,
        _ISSUE_IID,
        implement_payload=_implement_payload(),
        implement_result=_implement_result(),
    )

    assert payload["plan_document"] == "# 概要\n実装計画の本文です。"


def test_build_push_job_payload_carries_commit_fields_from_implement_result():
    payload = build_push_job_payload(
        _PROJECT,
        _ISSUE_IID,
        implement_payload=_implement_payload(),
        implement_result=_implement_result(),
    )

    assert payload == {
        "project": _PROJECT,
        "issue_iid": _ISSUE_IID,
        "plan_document": "# 概要\n実装計画の本文です。",
        "summary": "タスク1を実装した",
        "commit_message": "タスク1を実装",
        "commit_sha": "after-sha",
        "remote_branch": "ai/issue-7",
        "local_branch": "issue-7",
        "worktree_path": "/tmp/workspace/issue-7",
        "assumed_uncertainties": [
            {
                "question": "エラーメッセージの文言は?",
                "severity": "minor",
                "assumption": "一般的な文言を使う",
            }
        ],
    }


def test_push_job_payload_to_args_round_trips():
    payload = build_push_job_payload(
        _PROJECT,
        _ISSUE_IID,
        implement_payload=_implement_payload(),
        implement_result=_implement_result(),
    )

    push_input = push_job_payload_to_args(payload)

    assert push_input.project == _PROJECT
    assert push_input.issue_iid == _ISSUE_IID
    assert push_input.plan_document == "# 概要\n実装計画の本文です。"
    assert push_input.summary == "タスク1を実装した"
    assert push_input.commit_message == "タスク1を実装"
    assert push_input.commit_sha == "after-sha"
    assert push_input.remote_branch == "ai/issue-7"
    assert push_input.local_branch == "issue-7"
    assert push_input.worktree_path == "/tmp/workspace/issue-7"
    assert push_input.assumed_uncertainties == (
        {
            "question": "エラーメッセージの文言は?",
            "severity": "minor",
            "assumption": "一般的な文言を使う",
        },
    )


def test_push_job_payload_to_args_defaults_missing_optional_fields():
    payload = {
        "project": _PROJECT,
        "issue_iid": _ISSUE_IID,
        "commit_sha": "after-sha",
        "remote_branch": "ai/issue-7",
        "local_branch": "issue-7",
        "worktree_path": "/tmp/workspace/issue-7",
    }

    push_input = push_job_payload_to_args(payload)

    assert push_input.plan_document == ""
    assert push_input.summary == ""
    assert push_input.commit_message is None
    assert push_input.assumed_uncertainties == ()


# --- build_push_job_result ---


def test_build_push_job_result_reports_pushed_sha_and_merge_request_fields():
    result = build_push_job_result(
        _PROJECT,
        _ISSUE_IID,
        pushed_commit_sha="pushed-sha",
        merge_request=_merge_request(),
    )

    assert result == {
        "project": _PROJECT,
        "issue_iid": _ISSUE_IID,
        "pushed_commit_sha": "pushed-sha",
        "remote_branch": "ai/issue-7",
        "merge_request_iid": 42,
        "merge_request_web_url": "https://gitlab.example.com/group/project/-/merge_requests/42",
    }
