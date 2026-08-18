import dataclasses

import pytest

from gitlab_ai_platform.push import PushInput

_PROJECT = "group/project"
_ISSUE_IID = 7


def _push_input(**overrides) -> PushInput:
    kwargs = dict(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        plan_document="# 概要\n実装計画の本文です。",
        summary="タスク1を実装した",
        commit_message="タスク1を実装",
        commit_sha="after-sha",
        remote_branch="ai/issue-7",
        local_branch="issue-7",
        worktree_path="/tmp/workspace/issue-7",
        assumed_uncertainties=(
            {
                "question": "エラーメッセージの文言は?",
                "severity": "minor",
                "assumption": "一般的な文言を使う",
            },
        ),
    )
    kwargs.update(overrides)
    return PushInput(**kwargs)


def test_push_input_is_frozen():
    push_input = _push_input()

    with pytest.raises(dataclasses.FrozenInstanceError):
        push_input.commit_sha = "changed"


def test_push_input_holds_fields():
    push_input = _push_input()

    assert push_input.project == _PROJECT
    assert push_input.issue_iid == _ISSUE_IID
    assert push_input.plan_document == "# 概要\n実装計画の本文です。"
    assert push_input.summary == "タスク1を実装した"
    assert push_input.commit_message == "タスク1を実装"
    assert push_input.commit_sha == "after-sha"
    assert push_input.remote_branch == "ai/issue-7"
    assert push_input.local_branch == "issue-7"
    assert push_input.worktree_path == "/tmp/workspace/issue-7"
    assert (
        push_input.assumed_uncertainties[0]["question"] == "エラーメッセージの文言は?"
    )


def test_push_input_allows_none_commit_message():
    push_input = _push_input(commit_message=None)

    assert push_input.commit_message is None


def test_push_input_defaults_assumed_uncertainties_to_empty_tuple():
    push_input = PushInput(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        plan_document="",
        summary="",
        commit_message=None,
        commit_sha="sha",
        remote_branch="ai/issue-7",
        local_branch="issue-7",
        worktree_path="/tmp/workspace/issue-7",
    )

    assert push_input.assumed_uncertainties == ()
