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
- 書き込み操作(`GitLabWriter`の4メソッド)は、呼び出し結果(成功/protected branchによる拒否/
  エラー)を構造化ログとして記録する([M1-3](https://github.com/AtsushiNi/gitlab-ai-platform/issues/31)、
  X-1セキュリティレビューの証跡)。commit本文やコメント本文など任意長・機微になりうる内容は
  含めず、操作対象を特定できる識別子(project/branch/mr_iid等)のみを記録する。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import quote

import requests

from ..logging_ import get_logger
from .errors import GitLabApiError, ProtectedBranchError
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

_logger = get_logger(__name__)


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
        return _require(data, "version")

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
        try:
            data = self._request_json(
                "POST",
                f"/projects/{_encode_project(project)}/repository/branches",
                params={"branch": branch_name, "ref": ref},
            )
        except GitLabApiError:
            self._record_write(
                "create_branch", status="error", project=project, branch_name=branch_name, ref=ref
            )
            raise

        self._record_write(
            "create_branch", status="success", project=project, branch_name=branch_name, ref=ref
        )
        return _map_branch(data)

    def push_file_changes(
        self,
        project: str,
        branch: str,
        commit_message: str,
        actions: Sequence[CommitAction],
    ) -> str:
        try:
            self._reject_if_branch_protected(project, branch)
        except ProtectedBranchError:
            self._record_write(
                "push_file_changes", status="rejected_protected_branch", project=project, branch=branch
            )
            raise

        body = {
            "branch": branch,
            "commit_message": commit_message,
            "actions": [_map_commit_action(action) for action in actions],
        }
        try:
            data = self._request_json(
                "POST",
                f"/projects/{_encode_project(project)}/repository/commits",
                json_body=body,
            )
        except GitLabApiError:
            self._record_write(
                "push_file_changes",
                status="error",
                project=project,
                branch=branch,
                action_count=len(actions),
            )
            raise

        self._record_write(
            "push_file_changes",
            status="success",
            project=project,
            branch=branch,
            action_count=len(actions),
        )
        return _require(data, "id")

    def _reject_if_branch_protected(self, project: str, branch: str) -> None:
        """protected branchへの直pushを、AdapterがGitLabへ書き込む前に拒否する。

        M1-1のprotocol.pyが「実行時の追加チェックは実装側(M1-2)の責務」と定めている
        `push_file_changes`の対象branch検証。許可リスト(メソッドとして存在しない)だけでは
        「どのbranchに対してか」までは絞り込めないため、ここで別途ガードする。
        """
        data = self._request_json(
            "GET",
            f"/projects/{_encode_project(project)}/repository/branches/{quote(branch, safe='')}",
        )
        if data.get("protected", False):
            raise ProtectedBranchError(
                f"branch '{branch}' はprotectedのため push_file_changes を拒否しました"
            )

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
        try:
            data = self._request_json(
                "POST",
                f"/projects/{_encode_project(project)}/merge_requests",
                json_body=body,
            )
        except GitLabApiError:
            self._record_write(
                "create_merge_request",
                status="error",
                project=project,
                source_branch=source_branch,
                target_branch=target_branch,
            )
            raise

        mr = _map_merge_request(project, data)
        self._record_write(
            "create_merge_request",
            status="success",
            project=project,
            source_branch=source_branch,
            target_branch=target_branch,
            mr_iid=mr.iid,
        )
        return mr

    def create_merge_request_comment(self, project: str, mr_iid: int, body: str) -> Note:
        try:
            data = self._request_json(
                "POST",
                f"/projects/{_encode_project(project)}/merge_requests/{mr_iid}/notes",
                json_body={"body": body},
            )
        except GitLabApiError:
            self._record_write(
                "create_merge_request_comment", status="error", project=project, mr_iid=mr_iid
            )
            raise

        note = _map_note(data)
        self._record_write(
            "create_merge_request_comment",
            status="success",
            project=project,
            mr_iid=mr_iid,
            note_id=note.id,
        )
        return note

    def _record_write(self, operation: str, *, status: str, **fields: Any) -> None:
        """書き込み操作の実行結果を監査ログとして記録する(M1-3、X-1の証跡)。

        `logging_`モジュール(M0-3)のJSON構造化ログに`operation`/`status`等を
        `extra`として乗せる。commit本文やコメント本文は含めず、対象を特定できる
        識別子のみを記録する。
        """
        _logger.info("gitlab_adapter.write", extra={"operation": operation, "status": status, **fields})

    # -- 内部ヘルパー ----------------------------------------------------------

    def _paginated_get(self, path: str, *, params: dict[str, Any] | None = None) -> list[Any]:
        items: list[Any] = []
        page = 1
        # ページ間でリトライ予算を共有する(ページ数が多いほどリトライ回数が
        # 際限なく増えるのを防ぐため、1回のlist系呼び出し全体でmax_retries回までとする)
        retry_budget = [self._max_retries]
        while True:
            response = self._request(
                "GET",
                path,
                params={**(params or {}), "per_page": _PER_PAGE, "page": page},
                retry_budget=retry_budget,
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
        retry_budget: list[int] | None = None,
    ) -> requests.Response:
        url = self._api_base + path
        # retry_budgetが渡されない単発呼び出しは、その呼び出し専用の予算を持つ
        # (_paginated_getはページ間で同じlistを渡し、予算を共有する)
        budget = retry_budget if retry_budget is not None else [self._max_retries]

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

            # GET以外(副作用のある書き込み操作)は、レート制限(429、GitLab側で
            # 未処理のまま拒否される)以外はリトライしない。5xxはサーバー側で処理が
            # 部分的に完了している可能性があり、再送すると重複実行になりかねない
            retryable = response.status_code in _RETRYABLE_STATUS_CODES
            if method != "GET" and response.status_code != 429:
                retryable = False

            if retryable and budget[0] > 0:
                self._sleep(_retry_wait_seconds(response, attempt, self._backoff_seconds))
                budget[0] -= 1
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


def _require(data: dict[str, Any], key: str) -> Any:
    """必須フィールドを取り出す。欠けていればGitLabApiErrorに変換する。

    GitLab APIが返す2xxレスポンスでも、匿名化済みユーザーが絡むデータなど
    想定外の形式になることがある。呼び出し側は`GitLabAdapterError`系だけを
    catchすればよいという`errors.py`の方針を守るため、生の`KeyError`を
    そのまま外へ漏らさない。
    """
    try:
        return data[key]
    except KeyError as exc:
        raise GitLabApiError(
            f"GitLab APIレスポンスの形式が不正です(不足フィールド: {exc})"
        ) from exc


def _map_merge_request(project: str, data: dict[str, Any]) -> MergeRequest:
    return MergeRequest(
        project=project,
        iid=_require(data, "iid"),
        title=_require(data, "title"),
        description=data.get("description") or "",
        state=_require(data, "state"),
        source_branch=_require(data, "source_branch"),
        target_branch=_require(data, "target_branch"),
        sha=data.get("sha") or "",
        author=_require(_require(data, "author"), "username"),
        labels=tuple(data.get("labels", ())),
        web_url=data.get("web_url", ""),
    )


def _map_diff(data: dict[str, Any]) -> MergeRequestDiff:
    return MergeRequestDiff(
        old_path=_require(data, "old_path"),
        new_path=_require(data, "new_path"),
        diff=data.get("diff", ""),
        new_file=data.get("new_file", False),
        renamed_file=data.get("renamed_file", False),
        deleted_file=data.get("deleted_file", False),
    )


def _map_note(data: dict[str, Any]) -> Note:
    return Note(
        id=_require(data, "id"),
        body=_require(data, "body"),
        author=_require(_require(data, "author"), "username"),
        created_at=_require(data, "created_at"),
        system=data.get("system", False),
    )


def _map_discussion(data: dict[str, Any]) -> Discussion:
    return Discussion(
        id=str(_require(data, "id")),
        notes=tuple(_map_note(note) for note in data.get("notes", ())),
    )


def _map_branch(data: dict[str, Any]) -> Branch:
    return Branch(
        name=_require(data, "name"),
        commit_sha=_require(_require(data, "commit"), "id"),
        protected=data.get("protected", False),
    )


def _map_commit_action(action: CommitAction) -> dict[str, Any]:
    data: dict[str, Any] = {"action": action.action.value, "file_path": action.file_path}
    if action.content is not None:
        data["content"] = action.content
    return data


__all__ = ["GitLabRestAdapter"]
