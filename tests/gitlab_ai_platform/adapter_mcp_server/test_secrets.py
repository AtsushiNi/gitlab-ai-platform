"""認証情報(GitLab PAT)がMCPツールのレスポンス・エラーメッセージに含まれないことを検証する。

`GitLabRestAdapter`(REST実装)を、`requests.Session`相当のフェイクと組み合わせて使う
(実GitLabへは繋がない。`tests/gitlab_ai_platform/gitlab_adapter/test_rest.py`と同じ方針)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from gitlab_ai_platform.gitlab_adapter import GitLabRestAdapter
from gitlab_ai_platform.adapter_mcp_server import TOOL_DESCRIPTIONS, create_server

_SECRET_TOKEN = "glpat-super-secret-should-never-leak"
_BASE_URL = "https://gitlab.example.com"


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.headers = headers or {}
        self.text = text

    def json(self) -> Any:
        if self._json_data is None:
            raise ValueError("no json body")
        return self._json_data


class _FakeSession:
    """`requests.Session`の代わり。トークンがヘッダ以外(URL等)に紛れないことも確認する。"""

    def __init__(self, responses: Sequence[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.sent_headers: list[dict[str, str]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        assert _SECRET_TOKEN not in url
        self.sent_headers.append(kwargs.get("headers", {}))
        return self._responses.pop(0)


def _build_server(responses: Sequence[_FakeResponse]) -> tuple[Any, _FakeSession]:
    session = _FakeSession(responses)
    adapter = GitLabRestAdapter(_BASE_URL, _SECRET_TOKEN, session=session, max_retries=0)
    return create_server(adapter), session


def test_tool_descriptions_do_not_contain_token() -> None:
    for description in TOOL_DESCRIPTIONS.values():
        assert _SECRET_TOKEN not in description


def test_successful_tool_call_result_does_not_contain_token() -> None:
    server, session = _build_server([_FakeResponse(json_data={"version": "17.0.0-ee"})])

    async def _call() -> Any:
        return await server.call_tool("get_version", {})

    result = asyncio.run(_call())

    assert _SECRET_TOKEN not in str(result)
    # トークンはヘッダ経由でのみGitLab APIへ送られており(=認証は機能している)、
    # かつツールの戻り値には現れないことの両方を確認する
    assert session.sent_headers == [{"PRIVATE-TOKEN": _SECRET_TOKEN}]


def test_tool_call_error_message_does_not_contain_token() -> None:
    server, _ = _build_server(
        [_FakeResponse(status_code=404, json_data={"message": "404 Not Found"})]
    )

    async def _call() -> str:
        try:
            await server.call_tool("get_version", {})
        except ToolError as exc:
            return str(exc)
        raise AssertionError("ToolError が送出されるはず")

    message = asyncio.run(_call())

    assert _SECRET_TOKEN not in message
