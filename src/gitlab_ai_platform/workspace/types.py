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


__all__ = ["WorktreeHandle"]
