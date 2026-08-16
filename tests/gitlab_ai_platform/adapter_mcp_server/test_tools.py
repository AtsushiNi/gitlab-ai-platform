"""各MCPツール関数が、対応する`GitLabAdapter`メソッドへ正しく委譲することを検証する。

MCPプロトコル(stdio・`MCPServer`)は経由せず、`TOOL_FACTORIES`が返すPython呼び出し可能
オブジェクトを直接呼び出す。実MCPプロトコル通信・実GitLabのどちらへも繋がない
(CLAUDE.mdのテスト方針)。
"""

from __future__ import annotations

import pytest

from gitlab_ai_platform.adapter_mcp_server.tools import TOOL_FACTORIES
from gitlab_ai_platform.gitlab_adapter.types import CommitActionType

from .conftest import FakeGitLabAdapter


def test_get_version_delegates(fake_adapter: FakeGitLabAdapter) -> None:
    tool = TOOL_FACTORIES["get_version"](fake_adapter)

    result = tool()

    assert result == "17.0.0-ee"
    assert fake_adapter.calls == [("get_version", {})]


def test_list_merge_requests_delegates_and_converts_result(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    tool = TOOL_FACTORIES["list_merge_requests"](fake_adapter)

    result = tool(project="group/project", labels=["bug"], state="opened")

    assert fake_adapter.calls == [
        (
            "list_merge_requests",
            {"project": "group/project", "labels": ("bug",), "state": "opened"},
        )
    ]
    assert isinstance(result, list)
    assert result[0]["project"] == "group/project"
    assert result[0]["labels"] == ["bug"]  # tupleがJSON安全なlistに変換されている


def test_list_merge_requests_defaults_labels_to_empty(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    tool = TOOL_FACTORIES["list_merge_requests"](fake_adapter)

    tool(project="group/project")

    assert fake_adapter.calls == [
        (
            "list_merge_requests",
            {"project": "group/project", "labels": (), "state": "opened"},
        )
    ]


def test_get_merge_request_delegates(fake_adapter: FakeGitLabAdapter) -> None:
    tool = TOOL_FACTORIES["get_merge_request"](fake_adapter)

    result = tool(project="group/project", mr_iid=5)

    assert fake_adapter.calls == [
        ("get_merge_request", {"project": "group/project", "mr_iid": 5})
    ]
    assert result["iid"] == 5
    assert isinstance(result, dict)


def test_get_merge_request_diffs_delegates(fake_adapter: FakeGitLabAdapter) -> None:
    tool = TOOL_FACTORIES["get_merge_request_diffs"](fake_adapter)

    result = tool(project="group/project", mr_iid=5)

    assert fake_adapter.calls == [
        ("get_merge_request_diffs", {"project": "group/project", "mr_iid": 5})
    ]
    assert result == [
        {
            "old_path": "a.py",
            "new_path": "a.py",
            "diff": "@@ -1 +1 @@\n-x\n+y\n",
            "new_file": False,
            "renamed_file": False,
            "deleted_file": False,
        }
    ]


def test_list_merge_request_discussions_delegates_and_converts_nested_notes(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    tool = TOOL_FACTORIES["list_merge_request_discussions"](fake_adapter)

    result = tool(project="group/project", mr_iid=5)

    assert fake_adapter.calls == [
        ("list_merge_request_discussions", {"project": "group/project", "mr_iid": 5})
    ]
    assert result == [
        {
            "id": "d1",
            "notes": [
                {
                    "id": 1,
                    "body": "hi",
                    "author": "bob",
                    "created_at": "2026-01-01T00:00:00Z",
                    "system": False,
                }
            ],
        }
    ]


def test_create_branch_delegates(fake_adapter: FakeGitLabAdapter) -> None:
    tool = TOOL_FACTORIES["create_branch"](fake_adapter)

    result = tool(project="group/project", branch_name="feature/x", ref="main")

    assert fake_adapter.calls == [
        (
            "create_branch",
            {"project": "group/project", "branch_name": "feature/x", "ref": "main"},
        )
    ]
    assert result == {"name": "feature/x", "commit_sha": "sha2", "protected": False}


def test_push_file_changes_parses_actions_and_delegates(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    tool = TOOL_FACTORIES["push_file_changes"](fake_adapter)

    result = tool(
        project="group/project",
        branch="feature/x",
        commit_message="fix: x",
        actions=[
            {"action": "create", "file_path": "a.txt", "content": "hello"},
            {"action": "delete", "file_path": "b.txt"},
        ],
    )

    assert result == "sha3"
    assert len(fake_adapter.calls) == 1
    name, kwargs = fake_adapter.calls[0]
    assert name == "push_file_changes"
    assert kwargs["project"] == "group/project"
    assert kwargs["branch"] == "feature/x"
    assert kwargs["commit_message"] == "fix: x"

    actions = kwargs["actions"]
    assert actions[0].action == CommitActionType.CREATE
    assert actions[0].file_path == "a.txt"
    assert actions[0].content == "hello"
    assert actions[1].action == CommitActionType.DELETE
    assert actions[1].content is None


def test_create_merge_request_delegates_with_default_description(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    tool = TOOL_FACTORIES["create_merge_request"](fake_adapter)

    result = tool(
        project="group/project",
        source_branch="feature/x",
        target_branch="main",
        title="t",
    )

    assert fake_adapter.calls == [
        (
            "create_merge_request",
            {
                "project": "group/project",
                "source_branch": "feature/x",
                "target_branch": "main",
                "title": "t",
                "description": "",
            },
        )
    ]
    assert result["title"] == "t"


def test_create_merge_request_comment_delegates(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    tool = TOOL_FACTORIES["create_merge_request_comment"](fake_adapter)

    result = tool(project="group/project", mr_iid=5, body="LGTM")

    assert fake_adapter.calls == [
        (
            "create_merge_request_comment",
            {"project": "group/project", "mr_iid": 5, "body": "LGTM"},
        )
    ]
    assert result == {
        "id": 99,
        "body": "LGTM",
        "author": "ai-bot",
        "created_at": "2026-01-01T00:00:00Z",
        "system": False,
    }


def test_list_issues_delegates_and_converts_result(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    tool = TOOL_FACTORIES["list_issues"](fake_adapter)

    result = tool(project="group/project", labels=["bug"], state="opened")

    assert fake_adapter.calls == [
        (
            "list_issues",
            {"project": "group/project", "labels": ("bug",), "state": "opened"},
        )
    ]
    assert result[0]["project"] == "group/project"
    assert result[0]["labels"] == ["bug"]


def test_get_issue_delegates(fake_adapter: FakeGitLabAdapter) -> None:
    tool = TOOL_FACTORIES["get_issue"](fake_adapter)

    result = tool(project="group/project", issue_iid=5)

    assert fake_adapter.calls == [
        ("get_issue", {"project": "group/project", "issue_iid": 5})
    ]
    assert result["iid"] == 5


def test_update_merge_request_delegates_and_does_not_expose_state_event(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    tool = TOOL_FACTORIES["update_merge_request"](fake_adapter)

    result = tool(project="group/project", mr_iid=9, description="new desc")

    assert fake_adapter.calls == [
        (
            "update_merge_request",
            {
                "project": "group/project",
                "mr_iid": 9,
                "title": None,
                "description": "new desc",
            },
        )
    ]
    assert result["description"] == "new desc"
    # state_event相当の引数はツール関数のシグネチャ自体に存在しない(ADR-0002 M2-10追記)
    assert "state_event" not in fake_adapter.calls[0][1]


def test_create_issue_delegates(fake_adapter: FakeGitLabAdapter) -> None:
    tool = TOOL_FACTORIES["create_issue"](fake_adapter)

    result = tool(project="group/project", title="new issue", description="body")

    assert fake_adapter.calls == [
        (
            "create_issue",
            {"project": "group/project", "title": "new issue", "description": "body"},
        )
    ]
    assert result["title"] == "new issue"


def test_update_issue_delegates_and_does_not_expose_state_event(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    tool = TOOL_FACTORIES["update_issue"](fake_adapter)

    result = tool(project="group/project", issue_iid=3, title="updated title")

    assert fake_adapter.calls == [
        (
            "update_issue",
            {
                "project": "group/project",
                "issue_iid": 3,
                "title": "updated title",
                "description": None,
            },
        )
    ]
    assert result["title"] == "updated title"
    assert "state_event" not in fake_adapter.calls[0][1]


def test_list_merge_requests_falls_back_to_default_project_when_omitted(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    tool = TOOL_FACTORIES["list_merge_requests"](fake_adapter, "group/default-project")

    tool()

    assert fake_adapter.calls == [
        (
            "list_merge_requests",
            {"project": "group/default-project", "labels": (), "state": "opened"},
        )
    ]


def test_list_merge_requests_explicit_project_overrides_default(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    tool = TOOL_FACTORIES["list_merge_requests"](fake_adapter, "group/default-project")

    tool(project="group/other-project")

    assert fake_adapter.calls[0][1]["project"] == "group/other-project"


def test_get_merge_request_raises_when_project_omitted_and_no_default(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    tool = TOOL_FACTORIES["get_merge_request"](fake_adapter)

    with pytest.raises(ValueError):
        tool(mr_iid=1)

    assert fake_adapter.calls == []


def test_create_merge_request_falls_back_to_default_project_when_omitted(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    tool = TOOL_FACTORIES["create_merge_request"](fake_adapter, "group/default-project")

    tool(source_branch="feature/x", target_branch="main", title="t")

    assert fake_adapter.calls[0][1]["project"] == "group/default-project"


def test_get_version_ignores_default_project_argument(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    # get_versionはprojectを取らないが、他のファクトリと同じ`(adapter, default_project)`の
    # 呼び出し規約(server.pyのcreate_server)を満たせることを確認する
    tool = TOOL_FACTORIES["get_version"](fake_adapter, "group/default-project")

    assert tool() == "17.0.0-ee"


def test_tool_factories_cover_exactly_the_fourteen_allowed_methods() -> None:
    assert set(TOOL_FACTORIES) == {
        "get_version",
        "list_merge_requests",
        "get_merge_request",
        "get_merge_request_diffs",
        "list_merge_request_discussions",
        "list_issues",
        "get_issue",
        "create_branch",
        "push_file_changes",
        "create_merge_request",
        "create_merge_request_comment",
        "update_merge_request",
        "create_issue",
        "update_issue",
    }
