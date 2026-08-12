"""`GitLabReader` / `GitLabWriter` を満たすGitLab REST API実装。

方針(M1-2 [#30](https://github.com/AtsushiNi/gitlab-ai-platform/issues/30)、
`docs/adr/0002-gitlab-adapter-interface.md`、`references/spike-S2-gitlab-rest-api.md`):

- `GitLabWriter`の許可リスト(create_branch / push_file_changes / create_merge_request /
  create_merge_request_comment)以外の書き込み操作(merge、branch削除、管理操作等)は
  このクラスにメソッドとして実装しない。呼び出し可能な操作をコード上絞り込むというADR-0002の
  方針を、具象クラス側でも厳密に守る。
- diffは`changes`ではなく`diffs`エンドポイントを使う。コメントは`discussions`を使い、
  返信関係(スレッド構造)を保つ。
- 一覧取得は`per_page=100`固定 + `X-Next-Page`レスポンスヘッダに基づくoffsetページングとする。
- `429`(レート制限)と`5xx`は再試行対象とし、`429`は`Retry-After`ヘッダに従う。それ以外の
  エラーレスポンスは`GitLabApiError`として送出する。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import quote

import requests

from .errors import GitLabApiError
from .types import (
    Branch,
    CommitAction,
    Discussion,
    MergeRequest,
    MergeRequestDiff,
    Note,
)

_API_PREFIX = "/api/v4"
_PER_PAGE = 100
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_SECONDS = 1.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class GitLabRestAdapter:
    """GitLab REST API(v4)経由で`GitLabReader`/`GitLabWriter`を実装する。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 30.0,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_base = base_url.rstrip("/") + _API_PREFIX
        self._session = session if session is not None else requests.Session()
        self._headers = {"PRIVATE-TOKEN": token}
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep

    # -- GitLabReader ------------------------------------------------------

    def get_version(self) -> str:
        data = self._request_json("GET", "/version")
        return data["version"]

    def list_merge_requests(
        self,
        project: str,
        *,
        labels: Sequence[str] = (),
        state: str = "opened",
    ) -> list[MergeRequest]:
        params: dict[str, Any] = {"state": state}
        if labels:
            params["labels"] = ",".join(labels)

        items = self._paginated_get(
            f"/projects/{_encode_project(project)}/merge_requests", params=params
        )
        return [_map_merge_request(project, item) for item in items]

    def get_merge_request(self, project: str, mr_iid: int) -> MergeRequest:
        data = self._request_json(
            "GET", f"/projects/{_encode_project(project)}/merge_requests/{mr_iid}"
        )
        return _map_merge_request(project, data)

    def get_merge_request_diffs(self, project: str, mr_iid: int) -> list[MergeRequestDiff]:
        items = self._paginated_get(
            f"/projects/{_encode_project(project)}/merge_requests/{mr_iid}/diffs"
        )
        return [_map_diff(item) for item in items]

    def list_merge_request_discussions(self, project: str, mr_iid: int) -> list[Discussion]:
        items = self._paginated_get(
            f"/projects/{_encode_project(project)}/merge_requests/{mr_iid}/discussions"
        )
        return [_map_discussion(item) for item in items]

    # -- GitLabWriter --------------------------------------------------------
    # 許可リスト(ADR-0002)にある4操作のみを実装する。merge・branch削除・管理操作などは
    # 意図的にメソッドとして追加しない。

    def create_branch(self, project: str, branch_name: str, ref: str) -> Branch:
        data = self._request_json(
            "POST",
            f"/projects/{_encode_project(project)}/repository/branches",
            params={"branch": branch_name, "ref": ref},
        )
        return _map_branch(data)

    def push_file_changes(
        self,
        project: str,
        branch: str,
        commit_message: str,
        actions: Sequence[CommitAction],
    ) -> str:
        body = {
            "branch": branch,
            "commit_message": commit_message,
            "actions": [_map_commit_action(action) for action in actions],
        }
        data = self._request_json(
            "POST",
            f"/projects/{_encode_project(project)}/repository/commits",
            json_body=body,
        )
        return data["id"]

    def create_merge_request(
        self,
        project: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> MergeRequest:
        body = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title,
            "description": description,
        }
        data = self._request_json(
            "POST",
            f"/projects/{_encode_project(project)}/merge_requests",
            json_body=body,
        )
        return _map_merge_request(project, data)

    def create_merge_request_comment(self, project: str, mr_iid: int, body: str) -> Note:
        data = self._request_json(
            "POST",
            f"/projects/{_encode_project(project)}/merge_requests/{mr_iid}/notes",
            json_body={"body": body},
        )
        return _map_note(data)

    # -- 内部ヘルパー ----------------------------------------------------------

    def _paginated_get(self, path: str, *, params: dict[str, Any] | None = None) -> list[Any]:
        items: list[Any] = []
        page = 1
        while True:
            response = self._request(
                "GET", path, params={**(params or {}), "per_page": _PER_PAGE, "page": page}
            )
            items.extend(response.json())

            next_page = response.headers.get("X-Next-Page", "")
            if not next_page:
                return items
            page = int(next_page)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        return self._request(method, path, params=params, json_body=json_body).json()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> requests.Response:
        url = self._api_base + path

        attempt = 0
        while True:
            response = self._session.request(
                method,
                url,
                headers=self._headers,
                params=params,
                json=json_body,
                timeout=self._timeout,
            )

            if response.status_code < 400:
                return response

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                self._sleep(_retry_wait_seconds(response, attempt, self._backoff_seconds))
                attempt += 1
                continue

            raise GitLabApiError(
                _error_message(response), status_code=response.status_code
            )


def _retry_wait_seconds(
    response: requests.Response, attempt: int, backoff_seconds: float
) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return backoff_seconds * (2**attempt)


def _error_message(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text or f"GitLab API error (status={response.status_code})"

    if isinstance(data, dict):
        message = data.get("message") or data.get("error")
        if message:
            return str(message)

    return response.text or f"GitLab API error (status={response.status_code})"


def _encode_project(project: str) -> str:
    return quote(project, safe="")


def _map_merge_request(project: str, data: dict[str, Any]) -> MergeRequest:
    return MergeRequest(
        project=project,
        iid=data["iid"],
        title=data["title"],
        description=data.get("description") or "",
        state=data["state"],
        source_branch=data["source_branch"],
        target_branch=data["target_branch"],
        sha=data.get("sha") or "",
        author=data["author"]["username"],
        labels=tuple(data.get("labels", ())),
        web_url=data.get("web_url", ""),
    )


def _map_diff(data: dict[str, Any]) -> MergeRequestDiff:
    return MergeRequestDiff(
        old_path=data["old_path"],
        new_path=data["new_path"],
        diff=data.get("diff", ""),
        new_file=data.get("new_file", False),
        renamed_file=data.get("renamed_file", False),
        deleted_file=data.get("deleted_file", False),
    )


def _map_note(data: dict[str, Any]) -> Note:
    return Note(
        id=data["id"],
        body=data["body"],
        author=data["author"]["username"],
        created_at=data["created_at"],
        system=data.get("system", False),
    )


def _map_discussion(data: dict[str, Any]) -> Discussion:
    return Discussion(
        id=str(data["id"]),
        notes=tuple(_map_note(note) for note in data.get("notes", ())),
    )


def _map_branch(data: dict[str, Any]) -> Branch:
    return Branch(
        name=data["name"],
        commit_sha=data["commit"]["id"],
        protected=data.get("protected", False),
    )


def _map_commit_action(action: CommitAction) -> dict[str, Any]:
    data: dict[str, Any] = {"action": action.action.value, "file_path": action.file_path}
    if action.content is not None:
        data["content"] = action.content
    return data


__all__ = ["GitLabRestAdapter"]
