"""実装フェーズ専用の軽量なgit問い合わせヘルパー。

Workspace Manager(`workspace/`)は「bare clone/worktreeの用意・破棄」という境界に責務を
絞っており(`docs/adr/0004-workspace-manager-design.md`「git操作以外(ビルド・テスト実行など)は
しない」、`docs/adr/0031-issue-workspace.md`)、用意済みworktree内部の状態(HEADが指す
commit・作業ツリーが汚れていないか)を問い合わせる手段を持たない。実装フェーズは
Claude Code実行の前後でこれらを比較し、「実際にローカルcommitが行われたか」を
Claude Codeの自己申告に頼らず構造的に確認する必要があるため(ADR-0033)、
Workspace Managerを拡張せず、worktree_pathに対して直接`git`を呼ぶ小さなヘルパーを
このモジュールに置く。

`GitWorkspaceManager`(`workspace/git_workspace.py`)と同じく、subprocess呼び出しは
`run`引数で差し替え可能にし、テストで実gitに繋がず検証できるようにする。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import ImplementError


@dataclass(frozen=True)
class WorktreeState:
    """ある時点でのworktreeの状態(HEADのcommit sha、作業ツリーが汚れていないか)。"""

    head_sha: str
    is_clean: bool


def read_worktree_state(
    worktree_path: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> WorktreeState:
    """`worktree_path`の現在のHEAD commit shaと、作業ツリーが汚れていないかを返す。

    `git rev-parse HEAD`と`git status --porcelain`をそれぞれ1回だけ呼ぶ。いずれかが
    非ゼロ終了した場合(worktreeが破損している等)は`ImplementError`を送出する。
    """
    head_sha = _run_git(run, ["rev-parse", "HEAD"], worktree_path).strip()
    status = _run_git(run, ["status", "--porcelain"], worktree_path)
    return WorktreeState(head_sha=head_sha, is_clean=not status.strip())


def _run_git(
    run: Callable[..., subprocess.CompletedProcess], args: list[str], cwd: Path
) -> str:
    result = run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        raise ImplementError(
            f"worktree({cwd})に対する`git {' '.join(args)}`が失敗しました: {result.stderr}"
        )
    return result.stdout


__all__ = ["WorktreeState", "read_worktree_state"]
