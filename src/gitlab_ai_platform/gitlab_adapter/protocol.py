"""GitLab Adapter のインターフェース定義。

方針(M1-1 [#29](https://github.com/AtsushiNi/gitlab-ai-platform/issues/29)、
`docs/architecture.md`「設計原則」):

- 読み取り・書き込みを`typing.Protocol`で抽象化し、実装(REST, M1-2)を差し替え可能にする。
  構造的部分型(structural subtyping)を使うことで、将来のMCP実装もこのモジュールを
  importせず同じ形に嵌められる。
- 書き込みは許可リスト方式。**このインターフェースにメソッドとして存在する操作だけが
  Adapter経由で呼び出せる**。`merge`・protected branchへの直push・branch削除・管理操作
  (プロジェクト作成/メンバー管理等)は意図的にメソッドとして定義しない
  ( `references/spike-S2-gitlab-rest-api.md` の通り、PATスコープだけでは
  「MR作成は許可するがmergeは禁止」という粒度の制御ができないため、機構をコード側に持たせる)。
  実行時の追加チェック(`push_file_changes`の対象branchがprotectedでないことの検証)は
  実装側(REST実装, [M1-2](https://github.com/AtsushiNi/gitlab-ai-platform/issues/30))が行う
  (`ProtectedBranchError`)。書き込み操作の実行記録(監査ログ)は
  [M1-3](https://github.com/AtsushiNi/gitlab-ai-platform/issues/31)でREST実装側に追加した。
- Issue/MR更新系の書き込み(`update_issue`/`update_merge_request`)は、タイトル・説明の変更
  **のみ**を許可し、close/reopen/merge等の状態遷移は許可しない。GitLab REST APIの
  `state_event`パラメータに相当する引数はメソッドのシグネチャ自体に持たせず、
  「そもそも渡せない」という構造的な制約で担保する
  ([M2-10](https://github.com/AtsushiNi/gitlab-ai-platform/issues/47)、
  `docs/adr/0002-gitlab-adapter-interface.md`の追記参照)。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .types import (
    Branch,
    CommitAction,
    Discussion,
    Issue,
    MergeRequest,
    MergeRequestDiff,
    Note,
)


@runtime_checkable
class GitLabReader(Protocol):
    """読み取り専用の操作。`read_api`スコープのPATのみで動作することを想定する。"""

    def get_version(self) -> str:
        """GitLabのバージョン文字列を返す(`GET /version`または`/metadata`)。"""
        ...

    def list_merge_requests(
        self,
        project: str,
        *,
        labels: Sequence[str] = (),
        state: str = "opened",
    ) -> list[MergeRequest]:
        """指定プロジェクトのMR一覧を取得する。"""
        ...

    def get_merge_request(self, project: str, mr_iid: int) -> MergeRequest:
        """MRの詳細を取得する。"""
        ...

    def get_merge_request_diffs(
        self, project: str, mr_iid: int
    ) -> list[MergeRequestDiff]:
        """MRの差分をファイル単位で取得する(`diffs`エンドポイント。`changes`は使わない)。"""
        ...

    def list_merge_request_discussions(
        self, project: str, mr_iid: int
    ) -> list[Discussion]:
        """MRのコメントを、返信関係を保ったスレッド単位で取得する。"""
        ...

    def list_issues(
        self,
        project: str,
        *,
        labels: Sequence[str] = (),
        state: str = "opened",
    ) -> list[Issue]:
        """指定プロジェクトのIssue一覧を取得する。"""
        ...

    def get_issue(self, project: str, issue_iid: int) -> Issue:
        """Issueの詳細を取得する。"""
        ...


@runtime_checkable
class GitLabWriter(Protocol):
    """書き込み操作。許可リストに載っている操作のみをメソッドとして持つ。

    許可: branch作成 / push(ファイル変更のコミット) / MR作成 / コメント投稿 /
    Issue作成 / Issue更新(タイトル・説明のみ) / MR更新(タイトル・説明のみ)。
    禁止(メソッドとして存在しない): merge、protected branchへの直push、branch削除、管理操作、
    Issue/MRのクローズ・再オープン等の状態遷移(`update_issue`/`update_merge_request`は
    `state_event`相当の引数を一切持たない)。
    """

    def create_branch(self, project: str, branch_name: str, ref: str) -> Branch:
        """`ref`を起点に新しいbranchを作成する。"""
        ...

    def push_file_changes(
        self,
        project: str,
        branch: str,
        commit_message: str,
        actions: Sequence[CommitAction],
    ) -> str:
        """`branch`にファイル変更のコミットをpushし、新しいcommit shaを返す。

        Commits API経由のコミット作成であり、git経由の直接pushではない。実装(REST, M1-2)は
        対象branchがprotectedの場合、GitLab APIへ到達する前に`ProtectedBranchError`を送出して拒否する。
        """
        ...

    def create_merge_request(
        self,
        project: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> MergeRequest:
        """MRを作成する。"""
        ...

    def create_merge_request_comment(
        self, project: str, mr_iid: int, body: str
    ) -> Note:
        """MRにコメントを投稿する。"""
        ...

    def update_merge_request(
        self,
        project: str,
        mr_iid: int,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> MergeRequest:
        """MRのタイトル・説明を更新する。

        `state_event`(close/reopen/merge相当)は引数として存在しない。MRの状態遷移は
        Adapter経由では一切行えない設計であり、これは実行時チェックではなく
        メソッドシグネチャ自体による構造的な制約([#47](https://github.com/AtsushiNi/gitlab-ai-platform/issues/47))。
        """
        ...

    def create_issue(self, project: str, title: str, description: str = "") -> Issue:
        """Issueを作成する。"""
        ...

    def update_issue(
        self,
        project: str,
        issue_iid: int,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> Issue:
        """Issueのタイトル・説明を更新する。

        `state_event`(close/reopen相当)は引数として存在しない。`update_merge_request`と
        同じ理由で、状態遷移を構造的に不可能にしている。
        """
        ...


@runtime_checkable
class GitLabAdapter(GitLabReader, GitLabWriter, Protocol):
    """GitLab Adapterが満たすべき完全なインターフェース。"""


__all__ = ["GitLabAdapter", "GitLabReader", "GitLabWriter"]
