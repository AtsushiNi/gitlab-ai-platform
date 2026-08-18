from __future__ import annotations

import pytest

from gitlab_ai_platform.gitlab_adapter.types import (
    Discussion,
    Issue,
    MergeRequest,
    MergeRequestDiff,
    Note,
)
from gitlab_ai_platform.runner import IssueContext, ReviewContext


@pytest.fixture
def merge_request() -> MergeRequest:
    return MergeRequest(
        project="group/project",
        iid=42,
        title="Add feature X",
        description="This MR adds feature X.",
        state="opened",
        source_branch="feature-x",
        target_branch="main",
        sha="0123456789abcdef",
        author="alice",
    )


@pytest.fixture
def review_context(merge_request: MergeRequest) -> ReviewContext:
    diffs = (
        MergeRequestDiff(
            old_path="app.py",
            new_path="app.py",
            diff="@@ -1 +1 @@\n-old\n+new\n",
            new_file=False,
            renamed_file=False,
            deleted_file=False,
        ),
    )
    discussions = (
        Discussion(
            id="d1",
            notes=(
                Note(
                    id=1,
                    body="please add a test",
                    author="bob",
                    created_at="2026-01-01",
                ),
                Note(
                    id=2,
                    body="changed target branch",
                    author="system",
                    created_at="2026-01-01",
                    system=True,
                ),
            ),
        ),
    )
    return ReviewContext(
        merge_request=merge_request, diffs=diffs, discussions=discussions
    )


@pytest.fixture
def issue() -> Issue:
    return Issue(
        project="group/project",
        iid=7,
        title="Add feature Y",
        description="We need feature Y because of reason Z.",
        state="opened",
        author="carol",
        labels=("要求分析待ち",),
    )


@pytest.fixture
def issue_context(issue: Issue) -> IssueContext:
    return IssueContext(issue=issue)
