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
- 各ツールの`project`引数は`str | None = None`で省略可能(M2-12フォローアップ
  [#69](https://github.com/AtsushiNi/gitlab-ai-platform/issues/69))。省略された場合、
  ファクトリ関数に渡された`default_project`(MCPサーバー起動時のcwdのgit remoteから
  `default_project.resolve_default_project`で解決された値)にフォールバックする。
  どちらも無ければ`ValueError`(→MCP経由では`ToolError`)にする(`_resolve_project`)。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..gitlab_adapter import CommitAction, CommitActionType, GitLabAdapter
from .serialization import to_jsonable

# 1メソッド=1ファクトリ関数、というマッピングテーブルの型。
# `factory(adapter, default_project)`が、その`adapter`に束縛されたMCPツール本体
# (呼び出し可能オブジェクト)を返す。`default_project`は省略可(デフォルト`None`)。
ToolFactory = Callable[[GitLabAdapter], Callable[..., Any]]


def _resolve_project(project: str | None, default_project: str | None) -> str:
    """ツール呼び出し時の`project`引数を、省略時は`default_project`にフォールバックして解決する。

    どちらも無い場合は`ValueError`を送出する。silent fallbackで別プロジェクトを誤操作しない
    よう、この場合は例外にして呼び出し元(MCPクライアント)に明示的な指定を要求する。
    """
    if project is not None:
        return project
    if default_project is not None:
        return default_project
    raise ValueError(
        "projectが指定されておらず、MCPサーバー起動時のカレントディレクトリのgit remoteからも"
        "自動解決できませんでした。project引数を明示的に指定してください。"
    )


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


def _make_get_version(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    # get_versionはprojectを取らないため、他ファクトリと引数の形を揃えるためだけに
    # default_projectを受け取り、使わない。
    del default_project

    def get_version() -> str:
        """GitLabのバージョン文字列を取得する。"""
        return adapter.get_version()

    return get_version


def _make_list_merge_requests(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    def list_merge_requests(
        project: str | None = None,
        labels: list[str] | None = None,
        state: str = "opened",
    ) -> list[dict[str, Any]]:
        """指定プロジェクトのMR一覧を取得する。projectを省略した場合、MCPサーバー起動時の
        カレントディレクトリのgit remoteから自動検出したデフォルトプロジェクトを使う。"""
        resolved_project = _resolve_project(project, default_project)
        result = adapter.list_merge_requests(
            resolved_project, labels=tuple(labels or ()), state=state
        )
        return [to_jsonable(mr) for mr in result]

    return list_merge_requests


def _make_get_merge_request(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    def get_merge_request(project: str | None = None, *, mr_iid: int) -> dict[str, Any]:
        """MRの詳細を取得する。projectを省略した場合、MCPサーバー起動時のカレントディレクトリの
        git remoteから自動検出したデフォルトプロジェクトを使う。"""
        resolved_project = _resolve_project(project, default_project)
        return to_jsonable(adapter.get_merge_request(resolved_project, mr_iid))

    return get_merge_request


def _make_get_merge_request_diffs(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    def get_merge_request_diffs(
        project: str | None = None, *, mr_iid: int
    ) -> list[dict[str, Any]]:
        """MRの差分をファイル単位で取得する。projectを省略した場合、MCPサーバー起動時の
        カレントディレクトリのgit remoteから自動検出したデフォルトプロジェクトを使う。"""
        resolved_project = _resolve_project(project, default_project)
        return [
            to_jsonable(diff)
            for diff in adapter.get_merge_request_diffs(resolved_project, mr_iid)
        ]

    return get_merge_request_diffs


def _make_list_merge_request_discussions(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    def list_merge_request_discussions(
        project: str | None = None, *, mr_iid: int
    ) -> list[dict[str, Any]]:
        """MRのコメントを、返信関係を保ったスレッド単位で取得する。projectを省略した場合、
        MCPサーバー起動時のカレントディレクトリのgit remoteから自動検出したデフォルト
        プロジェクトを使う。"""
        resolved_project = _resolve_project(project, default_project)
        return [
            to_jsonable(discussion)
            for discussion in adapter.list_merge_request_discussions(resolved_project, mr_iid)
        ]

    return list_merge_request_discussions


def _make_list_issues(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    def list_issues(
        project: str | None = None,
        labels: list[str] | None = None,
        state: str = "opened",
    ) -> list[dict[str, Any]]:
        """指定プロジェクトのIssue一覧を取得する。projectを省略した場合、MCPサーバー起動時の
        カレントディレクトリのgit remoteから自動検出したデフォルトプロジェクトを使う。"""
        resolved_project = _resolve_project(project, default_project)
        result = adapter.list_issues(resolved_project, labels=tuple(labels or ()), state=state)
        return [to_jsonable(issue) for issue in result]

    return list_issues


def _make_get_issue(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    def get_issue(project: str | None = None, *, issue_iid: int) -> dict[str, Any]:
        """Issueの詳細を取得する。projectを省略した場合、MCPサーバー起動時のカレント
        ディレクトリのgit remoteから自動検出したデフォルトプロジェクトを使う。"""
        resolved_project = _resolve_project(project, default_project)
        return to_jsonable(adapter.get_issue(resolved_project, issue_iid))

    return get_issue


# -- GitLabWriter (書き込み7メソッド。ADR-0002の許可リストのみ) -------------------


def _make_create_branch(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    def create_branch(
        project: str | None = None, *, branch_name: str, ref: str
    ) -> dict[str, Any]:
        """`ref`を起点に新しいbranchを作成する。projectを省略した場合、MCPサーバー起動時の
        カレントディレクトリのgit remoteから自動検出したデフォルトプロジェクトを使う。"""
        resolved_project = _resolve_project(project, default_project)
        return to_jsonable(adapter.create_branch(resolved_project, branch_name, ref))

    return create_branch


def _make_push_file_changes(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    def push_file_changes(
        project: str | None = None,
        *,
        branch: str,
        commit_message: str,
        actions: list[dict[str, Any]],
    ) -> str:
        """`branch`にファイル変更のコミットをpushし、新しいcommit shaを返す。projectを
        省略した場合、MCPサーバー起動時のカレントディレクトリのgit remoteから自動検出した
        デフォルトプロジェクトを使う。

        `actions`は`{"action": "create"|"update"|"delete", "file_path": str,
        "content": str | None}`の辞書の配列。protected branchの場合はAdapter側
        (`GitLabRestAdapter`)が`ProtectedBranchError`を送出して拒否する。
        """
        resolved_project = _resolve_project(project, default_project)
        parsed_actions: Sequence[CommitAction] = [
            _parse_commit_action(action) for action in actions
        ]
        return adapter.push_file_changes(
            resolved_project, branch, commit_message, parsed_actions
        )

    return push_file_changes


def _make_create_merge_request(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    def create_merge_request(
        project: str | None = None,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> dict[str, Any]:
        """MRを作成する。projectを省略した場合、MCPサーバー起動時のカレントディレクトリの
        git remoteから自動検出したデフォルトプロジェクトを使う。"""
        resolved_project = _resolve_project(project, default_project)
        return to_jsonable(
            adapter.create_merge_request(
                resolved_project, source_branch, target_branch, title, description=description
            )
        )

    return create_merge_request


def _make_create_merge_request_comment(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    def create_merge_request_comment(
        project: str | None = None, *, mr_iid: int, body: str
    ) -> dict[str, Any]:
        """MRにコメントを投稿する。projectを省略した場合、MCPサーバー起動時のカレント
        ディレクトリのgit remoteから自動検出したデフォルトプロジェクトを使う。"""
        resolved_project = _resolve_project(project, default_project)
        return to_jsonable(adapter.create_merge_request_comment(resolved_project, mr_iid, body))

    return create_merge_request_comment


def _make_update_merge_request(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    def update_merge_request(
        project: str | None = None,
        *,
        mr_iid: int,
        title: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """MRのタイトル・説明を更新する。close/reopen/merge等の状態遷移は行えない
        (`state_event`相当の引数がAdapter側のメソッドシグネチャに存在しないため)。projectを
        省略した場合、MCPサーバー起動時のカレントディレクトリのgit remoteから自動検出した
        デフォルトプロジェクトを使う。"""
        resolved_project = _resolve_project(project, default_project)
        return to_jsonable(
            adapter.update_merge_request(
                resolved_project, mr_iid, title=title, description=description
            )
        )

    return update_merge_request


def _make_create_issue(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    def create_issue(
        project: str | None = None, *, title: str, description: str = ""
    ) -> dict[str, Any]:
        """Issueを作成する。projectを省略した場合、MCPサーバー起動時のカレントディレクトリの
        git remoteから自動検出したデフォルトプロジェクトを使う。"""
        resolved_project = _resolve_project(project, default_project)
        return to_jsonable(adapter.create_issue(resolved_project, title, description=description))

    return create_issue


def _make_update_issue(
    adapter: GitLabAdapter, default_project: str | None = None
) -> Callable[..., Any]:
    def update_issue(
        project: str | None = None,
        *,
        issue_iid: int,
        title: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Issueのタイトル・説明を更新する。close/reopen等の状態遷移は行えない
        (`update_merge_request`と同じ理由)。projectを省略した場合、MCPサーバー起動時の
        カレントディレクトリのgit remoteから自動検出したデフォルトプロジェクトを使う。"""
        resolved_project = _resolve_project(project, default_project)
        return to_jsonable(
            adapter.update_issue(
                resolved_project, issue_iid, title=title, description=description
            )
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

# projectを取る全ツール共通の補足文。MCPクライアント(AI)がproject省略の可否を
# 説明文だけから判断できるよう、各説明文の末尾に付与する。
_PROJECT_OMISSION_NOTE = (
    "projectは省略可。省略時はMCPサーバー起動時のカレントディレクトリのgit remoteから"
    "自動検出したデフォルトプロジェクトを使う。"
)

# 各ツールのMCP上の説明文(GitLab Adapterのspec/protocol.pyのdocstringを踏襲)。
TOOL_DESCRIPTIONS: dict[str, str] = {
    "get_version": "GitLabのバージョン文字列を取得する。",
    "list_merge_requests": f"指定プロジェクトのMR一覧を取得する。{_PROJECT_OMISSION_NOTE}",
    "get_merge_request": f"MRの詳細を取得する。{_PROJECT_OMISSION_NOTE}",
    "get_merge_request_diffs": f"MRの差分をファイル単位で取得する。{_PROJECT_OMISSION_NOTE}",
    "list_merge_request_discussions": (
        f"MRのコメントを、返信関係を保ったスレッド単位で取得する。{_PROJECT_OMISSION_NOTE}"
    ),
    "list_issues": f"指定プロジェクトのIssue一覧を取得する。{_PROJECT_OMISSION_NOTE}",
    "get_issue": f"Issueの詳細を取得する。{_PROJECT_OMISSION_NOTE}",
    "create_branch": f"refを起点に新しいbranchを作成する。{_PROJECT_OMISSION_NOTE}",
    "push_file_changes": (
        "branchにファイル変更のコミットをpushし、新しいcommit shaを返す。"
        "protected branchへの直pushはAdapter側で拒否される。"
        f"{_PROJECT_OMISSION_NOTE}"
    ),
    "create_merge_request": f"MRを作成する。{_PROJECT_OMISSION_NOTE}",
    "create_merge_request_comment": f"MRにコメントを投稿する。{_PROJECT_OMISSION_NOTE}",
    "update_merge_request": (
        "MRのタイトル・説明を更新する(close/reopen/merge等の状態遷移は不可)。"
        f"{_PROJECT_OMISSION_NOTE}"
    ),
    "create_issue": f"Issueを作成する。{_PROJECT_OMISSION_NOTE}",
    "update_issue": (
        f"Issueのタイトル・説明を更新する(close/reopen等の状態遷移は不可)。{_PROJECT_OMISSION_NOTE}"
    ),
}


__all__ = ["ToolFactory", "TOOL_FACTORIES", "TOOL_DESCRIPTIONS"]
