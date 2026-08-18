"""push フェーズ専用の軽量なgit問い合わせヘルパー(ADR-0034)。

`GitLabWriter.push_file_changes`(`gitlab_adapter/protocol.py`)はGitLab Commits API経由の
ファイル単位create/update/deleteであり、`git push`そのものではない。実装フェーズ
(`implement/`)がworktree内に残したローカルcommitを、この`CommitAction`の配列へ変換する
必要がある。`implement/git_ops.py`と同じ「Workspace Managerを拡張せず、`worktree_path`に
対して直接`git`を呼ぶ小さなヘルパーをこのモジュールに置く」という設計を踏襲する。

差分の「変更前」の基準点(base)は、`implement`のJob resultに新フィールドを追加せず、
本モジュールが`git merge-base`で都度計算する(ADR-0034「論点1」)。`ai/issue-<issue_iid>`
branchはpushフェーズ自身が実行されるまでリモートへのマージ・rebaseを一切経ないため、
`merge-base(commit_sha, origin/<default_branch>)`は実装用branchが分岐した時点のcommitと
一致する。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from ..gitlab_adapter.types import CommitAction, CommitActionType
from .errors import PushError


def resolve_push_base_sha(
    worktree_path: Path,
    default_branch: str,
    commit_sha: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str:
    """`commit_sha`と`origin/<default_branch>`の共通の祖先(diffのbase)を返す。

    `worktree_path`は`git worktree`で作成済みのディレクトリで、bare repo(`origin`)の
    `refs/remotes/origin/*`を共有して参照できる(`workspace/git_workspace.py`の
    `_sync_bare_repo`が実装フェーズ実行時にfetch済み)。いずれかのgitコマンドが失敗した場合は
    `PushError`を送出する。
    """
    remote_ref = f"refs/remotes/origin/{default_branch}"
    return _run_git(run, ["merge-base", commit_sha, remote_ref], worktree_path).strip()


def compute_commit_actions(
    worktree_path: Path,
    default_branch: str,
    commit_sha: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[CommitAction]:
    """`resolve_push_base_sha`のbaseから`commit_sha`までの変更を`CommitAction`の配列にする。

    `git diff --no-renames --name-status <base> <commit_sha>`で変更ファイル一覧を取得する。
    `--no-renames`により、renameは常に「削除+追加」の組として現れる
    (`CommitActionType`が`create`/`update`/`delete`の3値しか持たないため、ADR-0034)。
    追加・変更ファイルは`git show <commit_sha>:<path>`でcommit時点の内容を取得する
    (worktreeの作業ツリーを直接読まないことで、push実行時点の作業ツリーの状態に依存しない)。
    """
    base_sha = resolve_push_base_sha(worktree_path, default_branch, commit_sha, run=run)
    diff_output = _run_git(
        run,
        ["diff", "--no-renames", "--name-status", base_sha, commit_sha],
        worktree_path,
    )

    actions: list[CommitAction] = []
    for line in diff_output.splitlines():
        line = line.strip("\n")
        if not line:
            continue
        status, file_path = line.split("\t", 1)
        status_code = status[0]

        if status_code == "D":
            actions.append(
                CommitAction(action=CommitActionType.DELETE, file_path=file_path)
            )
            continue

        content = _read_file_at(run, worktree_path, commit_sha, file_path)
        action_type = (
            CommitActionType.CREATE if status_code == "A" else CommitActionType.UPDATE
        )
        actions.append(
            CommitAction(action=action_type, file_path=file_path, content=content)
        )

    return actions


def _read_file_at(
    run: Callable[..., subprocess.CompletedProcess],
    worktree_path: Path,
    commit_sha: str,
    file_path: str,
) -> str:
    return _run_git(run, ["show", f"{commit_sha}:{file_path}"], worktree_path)


def _run_git(
    run: Callable[..., subprocess.CompletedProcess], args: list[str], cwd: Path
) -> str:
    result = run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        raise PushError(
            f"worktree({cwd})に対する`git {' '.join(args)}`が失敗しました: {result.stderr}"
        )
    return result.stdout


__all__ = ["compute_commit_actions", "resolve_push_base_sha"]
