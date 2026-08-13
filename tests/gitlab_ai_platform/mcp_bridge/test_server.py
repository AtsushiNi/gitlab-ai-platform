"""MCPサーバーの配線(`create_server`)を検証する。

`GitLabAdapter`が持つ許可されたメソッド集合(現時点で14個)以外のツールが公開されない
ことを機構として担保する。将来`GitLabReader`/`GitLabWriter`にメソッドが増えた場合、
このテストの期待集合は意図的に更新しない限り失敗する
(=権限拡大を検知できる。`docs/adr/0010-gitlab-mcp-tool-bridge.md`参照。
M2-10(#47)分の追従はM2-12フォローアップ(#65)で対応済み)。

`create_server`/`list_tools`/`call_tool`はいずれも同一プロセス内のPython呼び出しであり、
実際のstdioソケット・パイプ通信は発生しない(実MCPプロトコル通信・実GitLabのどちらにも
繋がない、というCLAUDE.mdのテスト方針を満たす)。
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from gitlab_ai_platform.gitlab_adapter import GitLabReader, GitLabWriter
from gitlab_ai_platform.mcp_bridge import ALLOWED_TOOL_NAMES, create_server

from .conftest import FakeGitLabAdapter

# 現時点(M2-12フォローアップ #65実装時点)で許可されているべきツール名の完全な集合。
# GitLabReader/GitLabWriterにさらにメソッドが追加されたら、この集合と
# TOOL_FACTORIES/ALLOWED_TOOL_NAMESを合わせて更新すること(ADR-0010のTODO)。
_EXPECTED_ALLOWED_TOOL_NAMES = {
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

# GitLab Adapter側(ADR-0002、M2-10)で機構的に存在しないと保証されている禁止操作。
# このブリッジ経由でも当然公開されないことを重ねて確認する。
_FORBIDDEN_OPERATIONS = {
    "merge",
    "merge_merge_request",
    "accept_merge_request",
    "delete_branch",
    "push",
    "push_to_protected_branch",
    "create_project",
    "delete_project",
    "add_project_member",
    "update_project_member",
    "close_issue",
    "reopen_issue",
    "close_merge_request",
    "reopen_merge_request",
    "delete_issue",
}


def _protocol_public_methods(protocol_cls: type) -> set[str]:
    return {name for name in dir(protocol_cls) if not name.startswith("_")}


def test_allowed_tool_names_matches_current_fourteen_method_allow_list() -> None:
    assert ALLOWED_TOOL_NAMES == _EXPECTED_ALLOWED_TOOL_NAMES


def test_allowed_tool_names_matches_gitlab_adapter_protocol_exactly() -> None:
    # 現時点ではGitLabReader(7) + GitLabWriter(7)のちょうど14個がAdapterの全許可メソッドであり、
    # ブリッジのツール集合と完全一致するはず。Adapter側にメソッドが増えると
    # このテストが先に落ち、ブリッジ側の追従漏れに気づける
    adapter_methods = _protocol_public_methods(GitLabReader) | _protocol_public_methods(
        GitLabWriter
    )
    assert ALLOWED_TOOL_NAMES == adapter_methods


def test_allowed_tool_names_does_not_include_forbidden_operations() -> None:
    assert ALLOWED_TOOL_NAMES.isdisjoint(_FORBIDDEN_OPERATIONS)


def test_create_server_registers_exactly_the_allowed_tools(
    fake_adapter: FakeGitLabAdapter,
) -> None:
    server = create_server(fake_adapter)

    async def _list_names() -> set[str]:
        tools = await server.list_tools()
        return {tool.name for tool in tools}

    assert asyncio.run(_list_names()) == _EXPECTED_ALLOWED_TOOL_NAMES


def test_call_tool_delegates_to_adapter(fake_adapter: FakeGitLabAdapter) -> None:
    server = create_server(fake_adapter)

    async def _call() -> None:
        result = await server.call_tool("get_version", {})
        assert result.is_error is False
        assert result.structured_content == {"result": "17.0.0-ee"}

    asyncio.run(_call())
    assert fake_adapter.calls == [("get_version", {})]


@pytest.mark.parametrize("forbidden_name", sorted(_FORBIDDEN_OPERATIONS))
def test_call_tool_rejects_forbidden_operations_as_unknown(
    fake_adapter: FakeGitLabAdapter, forbidden_name: str
) -> None:
    # merge・branch削除・管理操作等は、そもそもGitLabAdapterにメソッドとして存在しない
    # (ADR-0002)ため、TOOL_FACTORIESにも登録されておらず、MCPサーバーからは
    # 「未知のツール」としてしか呼び出せない(=呼び出し不可能であることの確認)
    server = create_server(fake_adapter)

    async def _call() -> None:
        with pytest.raises(ToolError):
            await server.call_tool(forbidden_name, {})

    asyncio.run(_call())
    assert fake_adapter.calls == []
