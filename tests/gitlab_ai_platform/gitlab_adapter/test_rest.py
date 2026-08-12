from collections.abc import Sequence
from typing import Any

import pytest

from gitlab_ai_platform.gitlab_adapter import (
    CommitAction,
    CommitActionType,
    GitLabAdapter,
    GitLabApiError,
    GitLabRestAdapter,
)

_BASE_URL = "https://gitlab.example.com"
_TOKEN = "glpat-secret"


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
    """`requests.Session`の代わりに使うテスト用フェイク。実サービスには繋がない。"""

    def __init__(self, responses: Sequence[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._responses.pop(0)


def _adapter(
    responses: Sequence[_FakeResponse], *, sleep: Any = lambda seconds: None
) -> tuple[GitLabRestAdapter, _FakeSession]:
    session = _FakeSession(responses)
    adapter = GitLabRestAdapter(
        _BASE_URL, _TOKEN, session=session, max_retries=3, backoff_seconds=0.0, sleep=sleep
    )
    return adapter, session


def _author(username: str = "alice") -> dict[str, str]:
    return {"username": username}


def test_rest_adapter_satisfies_gitlab_adapter_protocol():
    adapter, _ = _adapter([])

    assert isinstance(adapter, GitLabAdapter)


# 許可リスト(ADR-0002)にない書き込み操作が具象クラスに増えていないことを、
# 具象クラス自体に対しても保証する(Protocolレベルのチェックだけでは具象クラスの余剰
# メソッドまでは検出できないため)。
_FORBIDDEN_WRITE_OPERATIONS = {
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
}


def test_rest_adapter_does_not_expose_forbidden_write_operations():
    public_methods = {name for name in dir(GitLabRestAdapter) if not name.startswith("_")}

    assert public_methods.isdisjoint(_FORBIDDEN_WRITE_OPERATIONS)


def test_get_version():
    adapter, session = _adapter([_FakeResponse(json_data={"version": "17.1.2-ee"})])

    assert adapter.get_version() == "17.1.2-ee"
    assert session.calls[0]["url"] == f"{_BASE_URL}/api/v4/version"


def test_list_merge_requests_paginates_using_x_next_page_header():
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data=[
                    {
                        "iid": 1,
                        "title": "first",
                        "description": "",
                        "state": "opened",
                        "source_branch": "feature/a",
                        "target_branch": "main",
                        "sha": "aaa",
                        "author": _author(),
                        "labels": ["レビュー待ち"],
                        "web_url": "https://gitlab.example.com/g/p/-/merge_requests/1",
                    }
                ],
                headers={"X-Next-Page": "2"},
            ),
            _FakeResponse(
                json_data=[
                    {
                        "iid": 2,
                        "title": "second",
                        "description": "",
                        "state": "opened",
                        "source_branch": "feature/b",
                        "target_branch": "main",
                        "sha": "bbb",
                        "author": _author("bob"),
                    }
                ],
                headers={"X-Next-Page": ""},
            ),
        ]
    )

    result = adapter.list_merge_requests("group/project", labels=["レビュー待ち"], state="opened")

    assert [mr.iid for mr in result] == [1, 2]
    assert result[0].labels == ("レビュー待ち",)
    assert result[0].author == "alice"
    assert len(session.calls) == 2
    assert session.calls[0]["params"]["labels"] == "レビュー待ち"
    assert session.calls[0]["params"]["page"] == 1
    assert session.calls[1]["params"]["page"] == 2


def test_list_merge_requests_url_encodes_project_path():
    adapter, session = _adapter(
        [_FakeResponse(json_data=[], headers={"X-Next-Page": ""})]
    )

    adapter.list_merge_requests("group/sub/project")

    assert session.calls[0]["url"] == (
        f"{_BASE_URL}/api/v4/projects/group%2Fsub%2Fproject/merge_requests"
    )


def test_get_merge_request():
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data={
                    "iid": 5,
                    "title": "title",
                    "description": "desc",
                    "state": "opened",
                    "source_branch": "feature/x",
                    "target_branch": "main",
                    "sha": "abc123",
                    "author": _author(),
                }
            )
        ]
    )

    mr = adapter.get_merge_request("group/project", 5)

    assert mr.iid == 5
    assert mr.project == "group/project"
    assert session.calls[0]["url"] == (
        f"{_BASE_URL}/api/v4/projects/group%2Fproject/merge_requests/5"
    )


def test_get_merge_request_diffs_uses_diffs_endpoint_not_changes():
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data=[
                    {
                        "old_path": "a.py",
                        "new_path": "a.py",
                        "diff": "@@ ... @@",
                        "new_file": False,
                        "renamed_file": False,
                        "deleted_file": False,
                    }
                ],
                headers={"X-Next-Page": ""},
            )
        ]
    )

    diffs = adapter.get_merge_request_diffs("group/project", 5)

    assert diffs[0].old_path == "a.py"
    assert session.calls[0]["url"].endswith("/merge_requests/5/diffs")


def test_list_merge_request_discussions_preserves_thread_structure():
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data=[
                    {
                        "id": "disc-1",
                        "notes": [
                            {
                                "id": 10,
                                "body": "please fix",
                                "author": _author("reviewer"),
                                "created_at": "2026-08-13T00:00:00Z",
                            },
                            {
                                "id": 11,
                                "body": "done",
                                "author": _author(),
                                "created_at": "2026-08-13T01:00:00Z",
                                "system": False,
                            },
                        ],
                    }
                ],
                headers={"X-Next-Page": ""},
            )
        ]
    )

    discussions = adapter.list_merge_request_discussions("group/project", 5)

    assert len(discussions) == 1
    assert discussions[0].id == "disc-1"
    assert [note.body for note in discussions[0].notes] == ["please fix", "done"]
    assert session.calls[0]["url"].endswith("/merge_requests/5/discussions")


def test_create_branch():
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data={
                    "name": "feature/ai-fix",
                    "commit": {"id": "sha123"},
                    "protected": False,
                }
            )
        ]
    )

    branch = adapter.create_branch("group/project", "feature/ai-fix", "main")

    assert branch.name == "feature/ai-fix"
    assert branch.commit_sha == "sha123"
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["params"] == {"branch": "feature/ai-fix", "ref": "main"}


def test_push_file_changes_returns_new_sha():
    adapter, session = _adapter([_FakeResponse(json_data={"id": "new-sha"})])

    actions = [
        CommitAction(action=CommitActionType.UPDATE, file_path="a.py", content="print(1)")
    ]
    new_sha = adapter.push_file_changes("group/project", "feature/x", "fix bug", actions)

    assert new_sha == "new-sha"
    body = session.calls[0]["json"]
    assert body["branch"] == "feature/x"
    assert body["commit_message"] == "fix bug"
    assert body["actions"] == [{"action": "update", "file_path": "a.py", "content": "print(1)"}]


def test_push_file_changes_omits_content_for_delete_action():
    adapter, session = _adapter([_FakeResponse(json_data={"id": "new-sha"})])

    actions = [CommitAction(action=CommitActionType.DELETE, file_path="old.py")]
    adapter.push_file_changes("group/project", "feature/x", "remove file", actions)

    body = session.calls[0]["json"]
    assert body["actions"] == [{"action": "delete", "file_path": "old.py"}]


def test_create_merge_request():
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data={
                    "iid": 9,
                    "title": "AI fix",
                    "description": "",
                    "state": "opened",
                    "source_branch": "feature/x",
                    "target_branch": "main",
                    "sha": "abc",
                    "author": _author("ai-bot"),
                }
            )
        ]
    )

    mr = adapter.create_merge_request("group/project", "feature/x", "main", "AI fix")

    assert mr.iid == 9
    assert session.calls[0]["json"]["title"] == "AI fix"


def test_create_merge_request_comment():
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data={
                    "id": 42,
                    "body": "LGTM",
                    "author": _author("ai-bot"),
                    "created_at": "2026-08-13T00:00:00Z",
                }
            )
        ]
    )

    note = adapter.create_merge_request_comment("group/project", 9, "LGTM")

    assert note.body == "LGTM"
    assert session.calls[0]["json"] == {"body": "LGTM"}


def test_429_retries_using_retry_after_header_then_succeeds():
    sleeps: list[float] = []
    adapter, session = _adapter(
        [
            _FakeResponse(status_code=429, headers={"Retry-After": "2"}, text="rate limited"),
            _FakeResponse(json_data={"version": "17.0.0"}),
        ],
        sleep=sleeps.append,
    )

    assert adapter.get_version() == "17.0.0"
    assert sleeps == [2.0]
    assert len(session.calls) == 2


def test_5xx_retries_with_backoff_then_succeeds():
    sleeps: list[float] = []
    adapter, session = _adapter(
        [
            _FakeResponse(status_code=503, text="unavailable"),
            _FakeResponse(json_data={"version": "17.0.0"}),
        ],
        sleep=sleeps.append,
    )

    assert adapter.get_version() == "17.0.0"
    assert len(sleeps) == 1
    assert len(session.calls) == 2


def test_retries_exhausted_raises_gitlab_api_error():
    adapter, session = _adapter(
        [_FakeResponse(status_code=503, text="unavailable")] * 4,
    )

    with pytest.raises(GitLabApiError) as excinfo:
        adapter.get_version()

    assert excinfo.value.status_code == 503
    assert len(session.calls) == 4


def test_non_retryable_error_raises_immediately_with_message_from_body():
    adapter, session = _adapter(
        [_FakeResponse(status_code=404, json_data={"message": "404 Project Not Found"})]
    )

    with pytest.raises(GitLabApiError) as excinfo:
        adapter.get_merge_request("group/missing", 1)

    assert excinfo.value.status_code == 404
    assert "Project Not Found" in str(excinfo.value)
    assert len(session.calls) == 1
