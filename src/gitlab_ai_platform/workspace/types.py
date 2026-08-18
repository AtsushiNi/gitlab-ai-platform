"""Workspace Manager が読み書きするデータの型。

`docs/architecture.md` の Workspace Manager の責務(プロジェクトごとのbare clone、
MR単位のworktree作成/更新/破棄)に対応する型を定義する。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorktreeHandle:
    """MR単位のworktree1つを表す。

    `path`はチェックアウト済みの作業ディレクトリで、Claude Code Runner(M1-7)は
    このパス配下でheadless実行する。`sha`は`path`で実際にcheckout済みのcommitで、
    `prepare`呼び出し時に指定した`ref`(branch名でもcommit shaでもよい)を解決した結果。
    """

    project: str
    mr_iid: int
    path: Path
    branch: str
    sha: str


@dataclass(frozen=True)
class IssueWorktreeHandle:
    """Issue単位のworktree1つを表す(M4-8、ADR-0031)。

    `WorktreeHandle`(MR単位)と対になる型。フィールド名を`mr_iid`のまま使い回すと
    「MRのIID」という意味が実態と食い違うため、`issue_iid`という別名を持つ専用の型として
    分離した(ADR-0029が指摘した、GitLabのMR/Issueは採番が独立した名前空間であるため
    数値が偶然一致しうるという問題を、型レベルでも混同しないようにするため)。

    `path`/`branch`/`sha`の意味は`WorktreeHandle`と同じ。`branch`は`issue-<iid>`という
    ローカルbranch名になり、`mr-<iid>`とは名前空間が完全に分離される
    (`git_workspace.py`の`_issue_branch_name`参照)。
    """

    project: str
    issue_iid: int
    path: Path
    branch: str
    sha: str


__all__ = ["IssueWorktreeHandle", "WorktreeHandle"]
