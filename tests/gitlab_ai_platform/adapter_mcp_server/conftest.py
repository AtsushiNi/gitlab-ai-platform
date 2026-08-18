"""adapter_mcp_server テスト用のフェイクGitLabAdapter。

実GitLabへは繋がず、adapter_mcp_serverのツール関数がAdapter(Protocol、ADR-0002)へ正しく
委譲することだけを検証するために使う(CLAUDE.mdのテスト方針)。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from gitlab_ai_platform.gitlab_adapter.types import (
    Branch,
    CommitAction,
    Discussion,
    Issue,
    MergeRequest,
    MergeRequestDiff,
    Note,
)


class FakeGitLabAdapter:
    """`GitLabAdapter`Protocolを満たす、呼び出し引数を記録するフェイク実装。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))

    # -- GitLabReader --------------------------------------------------------

    def get_version(self) -> str:
        self._record("get_version")
        return "17.0.0-ee"

    def list_merge_requests(
        self, project: str, *, labels: Sequence[str] = (), state: str = "opened"
    ) -> list[MergeRequest]:
        self._record(
            "list_merge_requests", project=project, labels=tuple(labels), state=state
        )
        return [
            MergeRequest(
                project=project,
                iid=1,
                title="t",
                description="d",
                state=state,
                source_branch="feature",
                target_branch="main",
                sha="sha1",
                author="alice",
                labels=tuple(labels),
                web_url="https://gitlab.example.com/g/p/-/merge_requests/1",
            )
        ]

    def get_merge_request(self, project: str, mr_iid: int) -> MergeRequest:
        self._record("get_merge_request", project=project, mr_iid=mr_iid)
        return MergeRequest(
            project=project,
            iid=mr_iid,
            title="t",
            description="d",
            state="opened",
            source_branch="feature",
            target_branch="main",
            sha="sha1",
            author="alice",
        )

    def get_merge_request_diffs(
        self, project: str, mr_iid: int
    ) -> list[MergeRequestDiff]:
        self._record("get_merge_request_diffs", project=project, mr_iid=mr_iid)
        return [
            MergeRequestDiff(
                old_path="a.py",
                new_path="a.py",
                diff="@@ -1 +1 @@\n-x\n+y\n",
                new_file=False,
                renamed_file=False,
                deleted_file=False,
            )
        ]

    def list_merge_request_discussions(
        self, project: str, mr_iid: int
    ) -> list[Discussion]:
        self._record("list_merge_request_discussions", project=project, mr_iid=mr_iid)
        return [
            Discussion(
                id="d1",
                notes=(
                    Note(
                        id=1, body="hi", author="bob", created_at="2026-01-01T00:00:00Z"
                    ),
                ),
            )
        ]

    def list_issues(
        self, project: str, *, labels: Sequence[str] = (), state: str = "opened"
    ) -> list[Issue]:
        self._record("list_issues", project=project, labels=tuple(labels), state=state)
        return [
            Issue(
                project=project,
                iid=1,
                title="t",
                description="d",
                state=state,
                author="alice",
                labels=tuple(labels),
                web_url="https://gitlab.example.com/g/p/-/issues/1",
            )
        ]

    def get_issue(self, project: str, issue_iid: int) -> Issue:
        self._record("get_issue", project=project, issue_iid=issue_iid)
        return Issue(
            project=project,
            iid=issue_iid,
            title="t",
            description="d",
            state="opened",
            author="alice",
        )

    def get_default_branch(self, project: str) -> str:
        self._record("get_default_branch", project=project)
        return "main"

    # -- GitLabWriter ---------------------------------------------------------

    def create_branch(self, project: str, branch_name: str, ref: str) -> Branch:
        self._record("create_branch", project=project, branch_name=branch_name, ref=ref)
        return Branch(name=branch_name, commit_sha="sha2", protected=False)

    def push_file_changes(
        self,
        project: str,
        branch: str,
        commit_message: str,
        actions: Sequence[CommitAction],
    ) -> str:
        self._record(
            "push_file_changes",
            project=project,
            branch=branch,
            commit_message=commit_message,
            actions=tuple(actions),
        )
        return "sha3"

    def create_merge_request(
        self,
        project: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> MergeRequest:
        self._record(
            "create_merge_request",
            project=project,
            source_branch=source_branch,
            target_branch=target_branch,
            title=title,
            description=description,
        )
        return MergeRequest(
            project=project,
            iid=3,
            title=title,
            description=description,
            state="opened",
            source_branch=source_branch,
            target_branch=target_branch,
            sha="sha4",
            author="ai-bot",
        )

    def create_merge_request_comment(
        self, project: str, mr_iid: int, body: str
    ) -> Note:
        self._record(
            "create_merge_request_comment", project=project, mr_iid=mr_iid, body=body
        )
        return Note(
            id=99, body=body, author="ai-bot", created_at="2026-01-01T00:00:00Z"
        )

    def update_merge_request(
        self,
        project: str,
        mr_iid: int,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> MergeRequest:
        self._record(
            "update_merge_request",
            project=project,
            mr_iid=mr_iid,
            title=title,
            description=description,
        )
        return MergeRequest(
            project=project,
            iid=mr_iid,
            title=title or "t",
            description=description or "d",
            state="opened",
            source_branch="feature",
            target_branch="main",
            sha="sha1",
            author="alice",
        )

    def create_issue(self, project: str, title: str, description: str = "") -> Issue:
        self._record(
            "create_issue", project=project, title=title, description=description
        )
        return Issue(
            project=project,
            iid=2,
            title=title,
            description=description,
            state="opened",
            author="ai-bot",
        )

    def update_issue(
        self,
        project: str,
        issue_iid: int,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> Issue:
        self._record(
            "update_issue",
            project=project,
            issue_iid=issue_iid,
            title=title,
            description=description,
        )
        return Issue(
            project=project,
            iid=issue_iid,
            title=title or "t",
            description=description or "d",
            state="opened",
            author="alice",
        )


@pytest.fixture
def fake_adapter() -> FakeGitLabAdapter:
    return FakeGitLabAdapter()
