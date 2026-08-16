import logging
from collections.abc import Sequence
from typing import Any

import pytest
import requests

from gitlab_ai_platform.gitlab_adapter import (
    CommitAction,
    CommitActionType,
    GitLabAdapter,
    GitLabApiError,
    GitLabRestAdapter,
    ProtectedBranchError,
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

    def __init__(self, responses: Sequence[_FakeResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _adapter(
    responses: Sequence[_FakeResponse], *, sleep: Any = lambda seconds: None
) -> tuple[GitLabRestAdapter, _FakeSession]:
    session = _FakeSession(responses)
    adapter = GitLabRestAdapter(
        _BASE_URL,
        _TOKEN,
        session=session,
        max_retries=3,
        backoff_seconds=0.0,
        sleep=sleep,
    )
    return adapter, session


def _author(username: str = "alice") -> dict[str, str]:
    return {"username": username}


def test_rest_adapter_satisfies_gitlab_adapter_protocol():
    adapter, _ = _adapter([])

    assert isinstance(adapter, GitLabAdapter)


# 許可リスト(ADR-0002)にある操作だけが具象クラスの公開メソッドであることを保証する。
# test_protocol.pyのProtocolレベルの完全一致テストと同じ強さの保証を、具象クラスにも
# 適用する(ブロックリスト方式だと、リストにない新しい禁止操作名の追加漏れを検知できない)。
_ALLOWED_PUBLIC_OPERATIONS = {
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


def test_rest_adapter_exposes_only_allow_listed_operations():
    public_methods = {
        name for name in dir(GitLabRestAdapter) if not name.startswith("_")
    }

    assert public_methods == _ALLOWED_PUBLIC_OPERATIONS


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

    result = adapter.list_merge_requests(
        "group/project", labels=["レビュー待ち"], state="opened"
    )

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


def _unprotected_branch_response(name: str = "feature/x") -> _FakeResponse:
    return _FakeResponse(json_data={"name": name, "protected": False})


def test_push_file_changes_returns_new_sha():
    adapter, session = _adapter(
        [_unprotected_branch_response(), _FakeResponse(json_data={"id": "new-sha"})]
    )

    actions = [
        CommitAction(
            action=CommitActionType.UPDATE, file_path="a.py", content="print(1)"
        )
    ]
    new_sha = adapter.push_file_changes(
        "group/project", "feature/x", "fix bug", actions
    )

    assert new_sha == "new-sha"
    assert session.calls[0]["method"] == "GET"
    body = session.calls[1]["json"]
    assert body["branch"] == "feature/x"
    assert body["commit_message"] == "fix bug"
    assert body["actions"] == [
        {"action": "update", "file_path": "a.py", "content": "print(1)"}
    ]


def test_push_file_changes_omits_content_for_delete_action():
    adapter, session = _adapter(
        [_unprotected_branch_response(), _FakeResponse(json_data={"id": "new-sha"})]
    )

    actions = [CommitAction(action=CommitActionType.DELETE, file_path="old.py")]
    adapter.push_file_changes("group/project", "feature/x", "remove file", actions)

    body = session.calls[1]["json"]
    assert body["actions"] == [{"action": "delete", "file_path": "old.py"}]


def test_push_file_changes_rejects_protected_branch_without_calling_commits_api():
    adapter, session = _adapter(
        [_FakeResponse(json_data={"name": "main", "protected": True})]
    )

    actions = [
        CommitAction(action=CommitActionType.UPDATE, file_path="a.py", content="x")
    ]
    with pytest.raises(ProtectedBranchError):
        adapter.push_file_changes("group/project", "main", "oops", actions)

    # protectedと判明した時点で拒否し、Commits APIへは到達しない
    assert len(session.calls) == 1
    assert session.calls[0]["method"] == "GET"


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


def test_list_issues_paginates_using_x_next_page_header():
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data=[
                    {
                        "iid": 1,
                        "title": "first",
                        "description": "",
                        "state": "opened",
                        "author": _author(),
                        "labels": ["bug"],
                        "web_url": "https://gitlab.example.com/g/p/-/issues/1",
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
                        "author": _author("bob"),
                    }
                ],
                headers={"X-Next-Page": ""},
            ),
        ]
    )

    result = adapter.list_issues("group/project", labels=["bug"], state="opened")

    assert [issue.iid for issue in result] == [1, 2]
    assert result[0].labels == ("bug",)
    assert result[0].author == "alice"
    assert len(session.calls) == 2
    assert session.calls[0]["params"]["labels"] == "bug"
    assert session.calls[0]["url"].endswith("/issues")


def test_get_issue():
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data={
                    "iid": 5,
                    "title": "title",
                    "description": "desc",
                    "state": "opened",
                    "author": _author(),
                }
            )
        ]
    )

    issue = adapter.get_issue("group/project", 5)

    assert issue.iid == 5
    assert issue.project == "group/project"
    assert session.calls[0]["url"] == (
        f"{_BASE_URL}/api/v4/projects/group%2Fproject/issues/5"
    )


def test_create_issue():
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data={
                    "iid": 3,
                    "title": "new issue",
                    "description": "body",
                    "state": "opened",
                    "author": _author("ai-bot"),
                }
            )
        ]
    )

    issue = adapter.create_issue("group/project", "new issue", "body")

    assert issue.iid == 3
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["json"] == {"title": "new issue", "description": "body"}


def test_update_issue_sends_only_specified_fields():
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data={
                    "iid": 3,
                    "title": "updated title",
                    "description": "old desc",
                    "state": "opened",
                    "author": _author(),
                }
            )
        ]
    )

    issue = adapter.update_issue("group/project", 3, title="updated title")

    assert issue.title == "updated title"
    assert session.calls[0]["method"] == "PUT"
    assert session.calls[0]["json"] == {"title": "updated title"}


def test_update_issue_does_not_send_state_event():
    """update_issueにはstate_eventを渡す引数自体が存在しない。送信ボディに
    close/reopen相当のキーが決して含まれないことを回帰確認する。"""
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data={
                    "iid": 3,
                    "title": "t",
                    "description": "d",
                    "state": "opened",
                    "author": _author(),
                }
            )
        ]
    )

    adapter.update_issue("group/project", 3, title="t", description="d")

    assert "state_event" not in session.calls[0]["json"]
    assert set(session.calls[0]["json"].keys()) == {"title", "description"}


def test_update_merge_request_sends_only_specified_fields():
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data={
                    "iid": 9,
                    "title": "updated",
                    "description": "",
                    "state": "opened",
                    "source_branch": "feature/x",
                    "target_branch": "main",
                    "sha": "abc",
                    "author": _author(),
                }
            )
        ]
    )

    mr = adapter.update_merge_request("group/project", 9, description="new desc")

    assert mr.iid == 9
    assert session.calls[0]["method"] == "PUT"
    assert session.calls[0]["url"] == (
        f"{_BASE_URL}/api/v4/projects/group%2Fproject/merge_requests/9"
    )
    assert session.calls[0]["json"] == {"description": "new desc"}


def test_update_merge_request_does_not_send_state_event():
    """update_merge_requestにはstate_event(close/reopen/merge相当)を渡す引数が
    存在しない。送信ボディにも決して含まれないことを回帰確認する。"""
    adapter, session = _adapter(
        [
            _FakeResponse(
                json_data={
                    "iid": 9,
                    "title": "t",
                    "description": "d",
                    "state": "opened",
                    "source_branch": "feature/x",
                    "target_branch": "main",
                    "sha": "abc",
                    "author": _author(),
                }
            )
        ]
    )

    adapter.update_merge_request("group/project", 9, title="t", description="d")

    assert "state_event" not in session.calls[0]["json"]
    assert set(session.calls[0]["json"].keys()) == {"title", "description"}


def test_429_retries_using_retry_after_header_then_succeeds():
    sleeps: list[float] = []
    adapter, session = _adapter(
        [
            _FakeResponse(
                status_code=429, headers={"Retry-After": "2"}, text="rate limited"
            ),
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


def test_5xx_on_non_get_request_is_not_retried_to_avoid_duplicate_writes():
    """POST等の非冪等操作は、サーバー側で処理が完了している可能性があるため
    5xxで再送しない(429のみリトライ対象)。get_versionと違いリトライされないので
    callsは1回のまま。"""
    adapter, session = _adapter(
        [
            _unprotected_branch_response(),
            _FakeResponse(status_code=503, text="unavailable"),
        ],
    )

    actions = [
        CommitAction(action=CommitActionType.UPDATE, file_path="a.py", content="x")
    ]
    with pytest.raises(GitLabApiError) as excinfo:
        adapter.push_file_changes("group/project", "feature/x", "msg", actions)

    assert excinfo.value.status_code == 503
    assert len(session.calls) == 2  # GET(branch確認) + POST(未リトライ)


def test_malformed_response_missing_required_field_raises_gitlab_api_error():
    adapter, _ = _adapter(
        [_FakeResponse(json_data={"iid": 1, "title": "no author field"})]
    )

    with pytest.raises(GitLabApiError):
        adapter.get_merge_request("group/project", 1)


# -- 監査ログ(M1-3) -----------------------------------------------------------


def _write_log_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.message == "gitlab_adapter.write"]


def test_create_branch_records_success_audit_log(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter(
        [_FakeResponse(json_data={"name": "feature/x", "commit": {"id": "sha1"}})]
    )

    adapter.create_branch("group/project", "feature/x", "main")

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].operation == "create_branch"
    assert records[0].status == "success"
    assert records[0].project == "group/project"
    assert records[0].branch_name == "feature/x"


def test_push_file_changes_records_success_audit_log(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter(
        [_unprotected_branch_response(), _FakeResponse(json_data={"id": "new-sha"})]
    )
    actions = [
        CommitAction(action=CommitActionType.UPDATE, file_path="a.py", content="x")
    ]

    adapter.push_file_changes("group/project", "feature/x", "fix", actions)

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].operation == "push_file_changes"
    assert records[0].status == "success"
    assert records[0].branch == "feature/x"
    assert records[0].action_count == 1


def test_push_file_changes_records_rejected_audit_log_for_protected_branch(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter(
        [_FakeResponse(json_data={"name": "main", "protected": True})]
    )
    actions = [
        CommitAction(action=CommitActionType.UPDATE, file_path="a.py", content="x")
    ]

    with pytest.raises(ProtectedBranchError):
        adapter.push_file_changes("group/project", "main", "oops", actions)

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].operation == "push_file_changes"
    assert records[0].status == "rejected_protected_branch"
    assert records[0].branch == "main"


def test_push_file_changes_records_error_audit_log_on_api_failure(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter(
        [
            _unprotected_branch_response(),
            _FakeResponse(status_code=503, text="unavailable"),
        ]
    )
    actions = [
        CommitAction(action=CommitActionType.UPDATE, file_path="a.py", content="x")
    ]

    with pytest.raises(GitLabApiError):
        adapter.push_file_changes("group/project", "feature/x", "oops", actions)

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].status == "error"


def test_create_merge_request_records_success_audit_log(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter(
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

    adapter.create_merge_request("group/project", "feature/x", "main", "AI fix")

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].operation == "create_merge_request"
    assert records[0].status == "success"
    assert records[0].mr_iid == 9


def test_create_merge_request_comment_records_success_audit_log_without_body(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter(
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

    adapter.create_merge_request_comment("group/project", 9, "LGTM")

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].operation == "create_merge_request_comment"
    assert records[0].note_id == 42
    # コメント本文そのものは監査ログに含めない(機微・任意長になりうるため)
    assert not hasattr(records[0], "body")


def test_update_merge_request_records_success_audit_log(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter(
        [
            _FakeResponse(
                json_data={
                    "iid": 9,
                    "title": "updated",
                    "description": "",
                    "state": "opened",
                    "source_branch": "feature/x",
                    "target_branch": "main",
                    "sha": "abc",
                    "author": _author(),
                }
            )
        ]
    )

    adapter.update_merge_request("group/project", 9, title="updated")

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].operation == "update_merge_request"
    assert records[0].status == "success"
    assert records[0].mr_iid == 9
    # 更新後のタイトル・説明文そのものは監査ログに含めない
    assert not hasattr(records[0], "title")
    assert not hasattr(records[0], "description")


def test_create_issue_records_success_audit_log(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter(
        [
            _FakeResponse(
                json_data={
                    "iid": 3,
                    "title": "new issue",
                    "description": "",
                    "state": "opened",
                    "author": _author("ai-bot"),
                }
            )
        ]
    )

    adapter.create_issue("group/project", "new issue")

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].operation == "create_issue"
    assert records[0].status == "success"
    assert records[0].issue_iid == 3


def test_update_issue_records_success_audit_log(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter(
        [
            _FakeResponse(
                json_data={
                    "iid": 3,
                    "title": "updated",
                    "description": "",
                    "state": "opened",
                    "author": _author(),
                }
            )
        ]
    )

    adapter.update_issue("group/project", 3, title="updated")

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].operation == "update_issue"
    assert records[0].status == "success"
    assert records[0].issue_iid == 3


def test_update_merge_request_records_error_audit_log_on_api_failure(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter(
        [_FakeResponse(status_code=404, json_data={"message": "not found"})]
    )

    with pytest.raises(GitLabApiError):
        adapter.update_merge_request("group/project", 9, title="x")

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].operation == "update_merge_request"
    assert records[0].status == "error"


def test_create_issue_records_error_audit_log_on_malformed_response(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter([_FakeResponse(json_data={"title": "no iid"})])

    with pytest.raises(GitLabApiError):
        adapter.create_issue("group/project", "new issue")

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].operation == "create_issue"
    assert records[0].status == "error"


def test_update_issue_records_error_audit_log_on_api_failure(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter(
        [_FakeResponse(status_code=404, json_data={"message": "not found"})]
    )

    with pytest.raises(GitLabApiError):
        adapter.update_issue("group/project", 3, title="x")

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].operation == "update_issue"
    assert records[0].status == "error"


# 2xxだが必須フィールドが欠けている(=マッピングに失敗する)応答は、監査ログ上も
# 「成功」や「記録なし」ではなく必ず"error"として残ることを確認する回帰テスト群。


def test_create_branch_records_error_audit_log_on_malformed_response(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter(
        [_FakeResponse(json_data={"name": "feature/x"})]
    )  # commit欠落

    with pytest.raises(GitLabApiError):
        adapter.create_branch("group/project", "feature/x", "main")

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].status == "error"


def test_push_file_changes_records_error_audit_log_on_malformed_response(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter([_unprotected_branch_response(), _FakeResponse(json_data={})])
    actions = [
        CommitAction(action=CommitActionType.UPDATE, file_path="a.py", content="x")
    ]

    with pytest.raises(GitLabApiError):
        adapter.push_file_changes("group/project", "feature/x", "fix", actions)

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].status == "error"


def test_push_file_changes_records_error_audit_log_when_protected_check_fails(
    caplog: pytest.LogCaptureFixture,
):
    """protected branch確認用のGET自体がエラーになった場合も監査ログに残ること。"""
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter(
        [_FakeResponse(status_code=404, json_data={"message": "not found"})]
    )
    actions = [
        CommitAction(action=CommitActionType.UPDATE, file_path="a.py", content="x")
    ]

    with pytest.raises(GitLabApiError):
        adapter.push_file_changes("group/project", "no-such-branch", "fix", actions)

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].status == "error"


def test_create_merge_request_records_error_audit_log_on_malformed_response(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter([_FakeResponse(json_data={"title": "no iid"})])

    with pytest.raises(GitLabApiError):
        adapter.create_merge_request("group/project", "feature/x", "main", "AI fix")

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].status == "error"


def test_create_merge_request_comment_records_error_audit_log_on_malformed_response(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO, logger="gitlab_ai_platform.gitlab_adapter.rest")
    adapter, _ = _adapter([_FakeResponse(json_data={"body": "no id"})])

    with pytest.raises(GitLabApiError):
        adapter.create_merge_request_comment("group/project", 9, "LGTM")

    records = _write_log_records(caplog)
    assert len(records) == 1
    assert records[0].status == "error"


def test_connection_error_is_wrapped_as_gitlab_api_error_after_retries_exhausted():
    # requestsの生の例外がそのまま伝播すると、呼び出し側(Poller等)がGitLabAdapterError
    # だけをcatchする契約をすり抜けてしまう回帰テスト。GETはリトライ対象なので、
    # 予算(max_retries=3、_adapterのデフォルト)を使い切るだけの回数を用意する
    adapter, session = _adapter(
        [requests.exceptions.ConnectionError("connection refused")] * 4
    )

    with pytest.raises(GitLabApiError):
        adapter.get_version()

    assert len(session.calls) == 4


def test_connection_error_on_get_is_retried_then_succeeds():
    sleeps: list[float] = []
    adapter, session = _adapter(
        [
            requests.exceptions.ConnectionError("connection refused"),
            _FakeResponse(json_data={"version": "17.0.0"}),
        ],
        sleep=sleeps.append,
    )

    assert adapter.get_version() == "17.0.0"
    assert len(sleeps) == 1
    assert len(session.calls) == 2


def test_connection_error_on_post_is_not_retried():
    # 非冪等な書き込み操作は、送信済みかどうか判別できない接続エラーで再送しない
    adapter, session = _adapter(
        [requests.exceptions.ConnectionError("connection refused")]
    )

    with pytest.raises(GitLabApiError):
        adapter.create_branch("group/project", "feature/x", "main")

    assert len(session.calls) == 1
