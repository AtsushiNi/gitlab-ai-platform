"""`GitLabAdapter`の各メソッドを1つのMCPツール関数に変換するマッピングテーブル。

方針(M2-12 [#62](https://github.com/AtsushiNi/gitlab-ai-platform/issues/62)、
`docs/adr/0010-gitlab-mcp-tool-bridge.md`、`docs/specs/adapter-mcp-server.md`。
#67で「GitLab Adapter MCP Server」に改称):

- このサーバーは**新しい権限を一切追加しない**。`GitLabAdapter`(Protocol、ADR-0002)に
  既に存在するメソッドだけをツールとして透過的に公開する層であり、`GitLabWriter`の許可リストを
  回避・拡張する経路になってはならない。merge・branch削除・管理操作はAdapter自体にメソッドが
  存在しないため、このモジュールにも対応するツールを追加しない(追加できない)。
- 1メソッド=1ファクトリ関数の対応表(`TOOL_FACTORIES`)として持つ。`GitLabReader`/
  `GitLabWriter`にメソッドが追加された場合、このファイルに対応するファクトリ関数を1つ追加し
  `TOOL_FACTORIES`に登録するだけで拡張できる。M2-10([#47](
  https://github.com/AtsushiNi/gitlab-ai-platform/issues/47))で追加された
  `list_issues`/`get_issue`/`create_issue`/`update_issue`/`update_merge_request`は
  M2-12フォローアップ([#65](https://github.com/AtsushiNi/gitlab-ai-platform/issues/65))で
  対応済み。
- 各ツール関数の引数・戻り値はプリミティブ型/`dict`/`list`のみとし、GitLab Adapterのdataclass
  (`gitlab_adapter/types.py`)はMCPクライアントに直接公開しない
  (`serialization.to_jsonable`で変換する)。
- 認証情報(GitLab PAT等)はどのツール関数の引数にも戻り値にも登場しない。Adapterの具象実装
  (`GitLabRestAdapter`)がコンストラクタで受け取った時点で内部化されており、このモジュールは
  Adapterのメソッド呼び出しを仲介するだけで認証情報そのものには一切触れない。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..gitlab_adapter import CommitAction, CommitActionType, GitLabAdapter
from .serialization import to_jsonable

# 1メソッド=1ファクトリ関数、というマッピングテーブルの型。
# `factory(adapter)`が、その`adapter`に束縛されたMCPツール本体(呼び出し可能オブジェクト)を返す。
ToolFactory = Callable[[GitLabAdapter], Callable[..., Any]]


def _parse_commit_action(raw: dict[str, Any]) -> CommitAction:
    """MCP経由で渡されたJSON(dict)を`CommitAction`に変換する。

    `action`は`CommitActionType`の値("create"/"update"/"delete")の文字列を想定する。
    不正な値は`ValueError`(`CommitActionType(...)`が送出)がそのままツール呼び出しの
    失敗として呼び出し元に伝わる。
    """
    return CommitAction(
        action=CommitActionType(raw["action"]),
        file_path=raw["file_path"],
        content=raw.get("content"),
    )


# -- GitLabReader (読み取り5メソッド) ------------------------------------------


def _make_get_version(adapter: GitLabAdapter) -> Callable[..., Any]:
    def get_version() -> str:
        """GitLabのバージョン文字列を取得する。"""
        return adapter.get_version()

    return get_version


def _make_list_merge_requests(adapter: GitLabAdapter) -> Callable[..., Any]:
    def list_merge_requests(
        project: str,
        labels: list[str] | None = None,
        state: str = "opened",
    ) -> list[dict[str, Any]]:
        """指定プロジェクトのMR一覧を取得する。"""
        result = adapter.list_merge_requests(
            project, labels=tuple(labels or ()), state=state
        )
        return [to_jsonable(mr) for mr in result]

    return list_merge_requests


def _make_get_merge_request(adapter: GitLabAdapter) -> Callable[..., Any]:
    def get_merge_request(project: str, mr_iid: int) -> dict[str, Any]:
        """MRの詳細を取得する。"""
        return to_jsonable(adapter.get_merge_request(project, mr_iid))

    return get_merge_request


def _make_get_merge_request_diffs(adapter: GitLabAdapter) -> Callable[..., Any]:
    def get_merge_request_diffs(project: str, mr_iid: int) -> list[dict[str, Any]]:
        """MRの差分をファイル単位で取得する。"""
        return [to_jsonable(diff) for diff in adapter.get_merge_request_diffs(project, mr_iid)]

    return get_merge_request_diffs


def _make_list_merge_request_discussions(adapter: GitLabAdapter) -> Callable[..., Any]:
    def list_merge_request_discussions(project: str, mr_iid: int) -> list[dict[str, Any]]:
        """MRのコメントを、返信関係を保ったスレッド単位で取得する。"""
        return [
            to_jsonable(discussion)
            for discussion in adapter.list_merge_request_discussions(project, mr_iid)
        ]

    return list_merge_request_discussions


def _make_list_issues(adapter: GitLabAdapter) -> Callable[..., Any]:
    def list_issues(
        project: str,
        labels: list[str] | None = None,
        state: str = "opened",
    ) -> list[dict[str, Any]]:
        """指定プロジェクトのIssue一覧を取得する。"""
        result = adapter.list_issues(project, labels=tuple(labels or ()), state=state)
        return [to_jsonable(issue) for issue in result]

    return list_issues


def _make_get_issue(adapter: GitLabAdapter) -> Callable[..., Any]:
    def get_issue(project: str, issue_iid: int) -> dict[str, Any]:
        """Issueの詳細を取得する。"""
        return to_jsonable(adapter.get_issue(project, issue_iid))

    return get_issue


# -- GitLabWriter (書き込み7メソッド。ADR-0002の許可リストのみ) -------------------


def _make_create_branch(adapter: GitLabAdapter) -> Callable[..., Any]:
    def create_branch(project: str, branch_name: str, ref: str) -> dict[str, Any]:
        """`ref`を起点に新しいbranchを作成する。"""
        return to_jsonable(adapter.create_branch(project, branch_name, ref))

    return create_branch


def _make_push_file_changes(adapter: GitLabAdapter) -> Callable[..., Any]:
    def push_file_changes(
        project: str,
        branch: str,
        commit_message: str,
        actions: list[dict[str, Any]],
    ) -> str:
        """`branch`にファイル変更のコミットをpushし、新しいcommit shaを返す。

        `actions`は`{"action": "create"|"update"|"delete", "file_path": str,
        "content": str | None}`の辞書の配列。protected branchの場合はAdapter側
        (`GitLabRestAdapter`)が`ProtectedBranchError`を送出して拒否する。
        """
        parsed_actions: Sequence[CommitAction] = [
            _parse_commit_action(action) for action in actions
        ]
        return adapter.push_file_changes(project, branch, commit_message, parsed_actions)

    return push_file_changes


def _make_create_merge_request(adapter: GitLabAdapter) -> Callable[..., Any]:
    def create_merge_request(
        project: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> dict[str, Any]:
        """MRを作成する。"""
        return to_jsonable(
            adapter.create_merge_request(
                project, source_branch, target_branch, title, description=description
            )
        )

    return create_merge_request


def _make_create_merge_request_comment(adapter: GitLabAdapter) -> Callable[..., Any]:
    def create_merge_request_comment(project: str, mr_iid: int, body: str) -> dict[str, Any]:
        """MRにコメントを投稿する。"""
        return to_jsonable(adapter.create_merge_request_comment(project, mr_iid, body))

    return create_merge_request_comment


def _make_update_merge_request(adapter: GitLabAdapter) -> Callable[..., Any]:
    def update_merge_request(
        project: str,
        mr_iid: int,
        title: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """MRのタイトル・説明を更新する。close/reopen/merge等の状態遷移は行えない
        (`state_event`相当の引数がAdapter側のメソッドシグネチャに存在しないため)。"""
        return to_jsonable(
            adapter.update_merge_request(project, mr_iid, title=title, description=description)
        )

    return update_merge_request


def _make_create_issue(adapter: GitLabAdapter) -> Callable[..., Any]:
    def create_issue(project: str, title: str, description: str = "") -> dict[str, Any]:
        """Issueを作成する。"""
        return to_jsonable(adapter.create_issue(project, title, description=description))

    return create_issue


def _make_update_issue(adapter: GitLabAdapter) -> Callable[..., Any]:
    def update_issue(
        project: str,
        issue_iid: int,
        title: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Issueのタイトル・説明を更新する。close/reopen等の状態遷移は行えない
        (`update_merge_request`と同じ理由)。"""
        return to_jsonable(
            adapter.update_issue(project, issue_iid, title=title, description=description)
        )

    return update_issue


# `GitLabAdapter`(Protocol)のメソッド名 → ツールファクトリ、の対応表。
# キーの集合は、`GitLabReader`(7) + `GitLabWriter`(7) = 14メソッドの許可リストと
# 完全一致する(`tests/gitlab_ai_platform/adapter_mcp_server/test_server.py`で検証)。
TOOL_FACTORIES: dict[str, ToolFactory] = {
    # -- 読み取り --
    "get_version": _make_get_version,
    "list_merge_requests": _make_list_merge_requests,
    "get_merge_request": _make_get_merge_request,
    "get_merge_request_diffs": _make_get_merge_request_diffs,
    "list_merge_request_discussions": _make_list_merge_request_discussions,
    "list_issues": _make_list_issues,
    "get_issue": _make_get_issue,
    # -- 書き込み(ADR-0002の許可リストのみ) --
    "create_branch": _make_create_branch,
    "push_file_changes": _make_push_file_changes,
    "create_merge_request": _make_create_merge_request,
    "create_merge_request_comment": _make_create_merge_request_comment,
    "update_merge_request": _make_update_merge_request,
    "create_issue": _make_create_issue,
    "update_issue": _make_update_issue,
}

# 各ツールのMCP上の説明文(GitLab Adapterのspec/protocol.pyのdocstringを踏襲)。
TOOL_DESCRIPTIONS: dict[str, str] = {
    "get_version": "GitLabのバージョン文字列を取得する。",
    "list_merge_requests": "指定プロジェクトのMR一覧を取得する。",
    "get_merge_request": "MRの詳細を取得する。",
    "get_merge_request_diffs": "MRの差分をファイル単位で取得する。",
    "list_merge_request_discussions": "MRのコメントを、返信関係を保ったスレッド単位で取得する。",
    "list_issues": "指定プロジェクトのIssue一覧を取得する。",
    "get_issue": "Issueの詳細を取得する。",
    "create_branch": "refを起点に新しいbranchを作成する。",
    "push_file_changes": (
        "branchにファイル変更のコミットをpushし、新しいcommit shaを返す。"
        "protected branchへの直pushはAdapter側で拒否される。"
    ),
    "create_merge_request": "MRを作成する。",
    "create_merge_request_comment": "MRにコメントを投稿する。",
    "update_merge_request": "MRのタイトル・説明を更新する(close/reopen/merge等の状態遷移は不可)。",
    "create_issue": "Issueを作成する。",
    "update_issue": "Issueのタイトル・説明を更新する(close/reopen等の状態遷移は不可)。",
}


__all__ = ["ToolFactory", "TOOL_FACTORIES", "TOOL_DESCRIPTIONS"]
