import dataclasses

import pytest

from gitlab_ai_platform.gitlab_adapter import CommitAction, CommitActionType, MergeRequest


def test_merge_request_labels_default_to_empty_tuple():
    mr = MergeRequest(
        project="group/project",
        iid=1,
        title="title",
        description="",
        state="opened",
        source_branch="feature/x",
        target_branch="main",
        sha="abc123",
        author="alice",
    )

    assert mr.labels == ()


def test_dataclasses_are_frozen():
    action = CommitAction(
        action=CommitActionType.CREATE, file_path="a.txt", content="hello"
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        action.file_path = "b.txt"


def test_commit_action_type_values_match_gitlab_commits_api():
    assert CommitActionType.CREATE == "create"
    assert CommitActionType.UPDATE == "update"
    assert CommitActionType.DELETE == "delete"
