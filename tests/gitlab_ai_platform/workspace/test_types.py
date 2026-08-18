import dataclasses
from pathlib import Path

import pytest

from gitlab_ai_platform.workspace import IssueWorktreeHandle, WorktreeHandle


def test_worktree_handle_is_frozen():
    handle = WorktreeHandle(
        project="group/project",
        mr_iid=1,
        path=Path("/tmp/x"),
        branch="mr-1",
        sha="abc123",
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        handle.sha = "def456"


def test_worktree_handle_holds_fields():
    handle = WorktreeHandle(
        project="group/project",
        mr_iid=1,
        path=Path("/tmp/x"),
        branch="mr-1",
        sha="abc123",
    )

    assert handle.project == "group/project"
    assert handle.mr_iid == 1
    assert handle.path == Path("/tmp/x")
    assert handle.branch == "mr-1"
    assert handle.sha == "abc123"


def test_issue_worktree_handle_is_frozen():
    handle = IssueWorktreeHandle(
        project="group/project",
        issue_iid=1,
        path=Path("/tmp/x"),
        branch="issue-1",
        sha="abc123",
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        handle.sha = "def456"


def test_issue_worktree_handle_holds_fields():
    handle = IssueWorktreeHandle(
        project="group/project",
        issue_iid=1,
        path=Path("/tmp/x"),
        branch="issue-1",
        sha="abc123",
    )

    assert handle.project == "group/project"
    assert handle.issue_iid == 1
    assert handle.path == Path("/tmp/x")
    assert handle.branch == "issue-1"
    assert handle.sha == "abc123"
