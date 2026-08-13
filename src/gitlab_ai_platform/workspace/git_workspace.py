"""`WorkspaceManager` を満たすgit実装。

方針(M1-6 [#34](https://github.com/AtsushiNi/gitlab-ai-platform/issues/34)、
`docs/adr/0004-workspace-manager-design.md`、`references/spike-S3-git-worktree-windows.md`):

- プロジェクト単位で `<root>/repos/<slug>.git` にbare cloneを1つ持ち、MR単位で
  `<root>/worktrees/<slug>/mr-<iid>/` にworktreeを作成する。
- bare repoへのfetchは `refs/heads/*:refs/remotes/origin/*` のリモートトラッキング方式のみを使う。
  `refs/heads/*:refs/heads/*` のような直接上書き方式は、稼働中のworktreeが対象branchを
  checkoutしていると拒否されるため使わない(Spike S-3 §4.2)。各worktreeの最新化は
  worktree自身の中で対象ref/commitへ`reset --hard`する。
- `git clone --bare`は初回clone時点の全branchを`refs/heads/*`に直接コピーするため、
  以降このrefs/heads/*はfetchで更新されず(上記の方式のため)取り残されてstaleになる。
  branch名を解決する際は`refs/remotes/origin/<ref>`を優先し、存在しなければ`ref`をそのまま
  使う(commit shaが渡された場合はこちらにフォールバックする)ことで、branch名指定時も
  常に最新のcommitを見るようにする(`_resolve_ref`)。
- worktreeのディレクトリ名は`mr-<iid>`のような短い識別子に留め、branch名やプロジェクト名の
  スラッシュをそのままパスに含めない(Spike S-3 §7、Windowsのパス長制限対策)。
- ディスク上限はworktree配下(作業コピー)の合計サイズのみで判定する。bare repo(objectの
  共有ストア)はGCの対象にしない。
- 認証(PAT/SSH)の詳細はこのモジュールの責務外。呼び出し側が`git_config`経由で
  `credential.helper`/`core.sshCommand`等を注入する(Spike S-3 §8)。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from ..logging_ import get_logger
from .errors import DiskLimitExceededError, GitCommandError
from .types import WorktreeHandle

_logger = get_logger(__name__)


class GitWorkspaceManager:
    """git(bare clone + `git worktree`)経由で`WorkspaceManager`を実装する。"""

    def __init__(
        self,
        root: Path | str,
        clone_url_for: Callable[[str], str],
        *,
        max_disk_bytes: int,
        git_config: Mapping[str, str] | None = None,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        """
        `clone_url_for`はproject名(`group/project`形式)からbare clone用のURLを組み立てる関数。
        GitLab側のURL構築はこのモジュールの責務外とし、呼び出し側(config層)に委ねる。

        `git_config`は全てのgitコマンド呼び出しに`-c key=value`として渡す追加設定
        (`credential.helper`/`core.sshCommand`等の認証設定を想定)。
        """
        self._root = Path(root)
        self._repos_dir = self._root / "repos"
        self._worktrees_dir = self._root / "worktrees"
        self._clone_url_for = clone_url_for
        self._max_disk_bytes = max_disk_bytes
        self._git_config = dict(git_config or {})
        self._run = run

        self._repos_dir.mkdir(parents=True, exist_ok=True)
        self._worktrees_dir.mkdir(parents=True, exist_ok=True)

    def prepare(self, project: str, mr_iid: int, ref: str) -> WorktreeHandle:
        bare_path = self._bare_path(project)
        worktree_path = self._worktree_path(project, mr_iid)
        branch_name = _branch_name(mr_iid)

        if not bare_path.exists():
            self._run_git(
                ["clone", "--bare", self._clone_url_for(project), str(bare_path)],
                cwd=self._repos_dir,
            )

        # 新規clone直後でも、clone完了からこの時点までにpushされた分を取りこぼさないよう
        # 常にfetchする。既存bare repoの場合も含め、常にrefs/remotes/origin/*のみを更新する
        self._run_git(["fetch", "origin", "+refs/heads/*:refs/remotes/origin/*"], cwd=bare_path)
        # クラッシュ等でworktreeディレクトリだけが消えた場合の自己復旧(Spike S-3 §5)。
        # 以降の worktree add で「既に登録済み」エラーになるのを防ぐ
        self._run_git(["worktree", "prune"], cwd=bare_path)

        resolved_ref = self._resolve_ref(bare_path, ref)

        if worktree_path.exists():
            self._run_git(["reset", "--hard", resolved_ref], cwd=worktree_path)
        else:
            self._ensure_disk_budget()
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
            self._run_git(
                ["worktree", "add", "-B", branch_name, str(worktree_path), resolved_ref],
                cwd=bare_path,
            )

        # GCのLRU判定に使う最終利用時刻を更新する
        os.utime(worktree_path, None)

        sha = self._run_git(["rev-parse", "HEAD"], cwd=worktree_path).strip()
        return WorktreeHandle(
            project=project, mr_iid=mr_iid, path=worktree_path, branch=branch_name, sha=sha
        )

    def discard(self, project: str, mr_iid: int) -> None:
        self._discard_worktree(project, mr_iid, self._worktree_path(project, mr_iid))

    def collect_garbage(self) -> list[WorktreeHandle]:
        removed: list[WorktreeHandle] = []
        while self._disk_usage_bytes() > self._max_disk_bytes:
            candidate = self._oldest_worktree()
            if candidate is None:
                break
            self._discard_worktree(candidate.project, candidate.mr_iid, candidate.path)
            removed.append(candidate)
            _logger.info(
                "workspace.gc_evict",
                extra={"project": candidate.project, "mr_iid": candidate.mr_iid},
            )
        return removed

    # -- 内部ヘルパー ----------------------------------------------------------

    def _ensure_disk_budget(self) -> None:
        if self._disk_usage_bytes() <= self._max_disk_bytes:
            return
        self.collect_garbage()
        if self._disk_usage_bytes() > self._max_disk_bytes:
            raise DiskLimitExceededError(
                f"ディスク上限({self._max_disk_bytes}バイト)を超過しており、"
                "既存worktreeを破棄してもなお新規worktree用の空き容量を確保できませんでした"
            )

    def _discard_worktree(self, project: str, mr_iid: int, path: Path) -> None:
        bare_path = self._bare_path(project)
        branch_name = _branch_name(mr_iid)

        if path.exists():
            if bare_path.exists():
                self._run_git(["worktree", "remove", "--force", str(path)], cwd=bare_path)
            else:
                shutil.rmtree(path)

        if bare_path.exists():
            try:
                self._run_git(["branch", "-D", branch_name], cwd=bare_path)
            except GitCommandError:
                # branchが既に存在しない(discardの二重呼び出し等)場合は無視する。
                # worktree自体の破棄は上で完了しているため、ここは後片付けの追加処理
                pass

    def _oldest_worktree(self) -> WorktreeHandle | None:
        candidates = [
            wt_dir
            for slug_dir in self._worktrees_dir.iterdir()
            if slug_dir.is_dir()
            for wt_dir in slug_dir.iterdir()
            if wt_dir.is_dir() and wt_dir.name.startswith("mr-")
        ]
        if not candidates:
            return None

        oldest = min(candidates, key=lambda p: p.stat().st_mtime)
        project = _deslugify_project(oldest.parent.name)
        mr_iid = int(oldest.name.removeprefix("mr-"))
        try:
            sha = self._run_git(["rev-parse", "HEAD"], cwd=oldest).strip()
        except GitCommandError:
            sha = ""
        return WorktreeHandle(
            project=project, mr_iid=mr_iid, path=oldest, branch=oldest.name, sha=sha
        )

    def _resolve_ref(self, bare_path: Path, ref: str) -> str:
        # `refs/remotes/origin/<ref>`が存在すればそちらを優先する(clone直後のstaleな
        # refs/heads/<ref>ではなく、常にfetch済みの最新commitを見るため)。存在しなければ
        # commit shaが渡されたとみなし、`ref`をそのまま使う
        remote_ref = f"refs/remotes/origin/{ref}"
        probe = self._run(
            ["git", "rev-parse", "--verify", "--quiet", remote_ref],
            cwd=str(bare_path),
            capture_output=True,
            text=True,
        )
        return remote_ref if probe.returncode == 0 else ref

    def _disk_usage_bytes(self) -> int:
        return _dir_size_bytes(self._worktrees_dir)

    def _bare_path(self, project: str) -> Path:
        return self._repos_dir / f"{_slugify_project(project)}.git"

    def _worktree_path(self, project: str, mr_iid: int) -> Path:
        return self._worktrees_dir / _slugify_project(project) / _branch_name(mr_iid)

    def _run_git(self, args: list[str], *, cwd: Path) -> str:
        config_args = [
            arg for key, value in self._git_config.items() for arg in ("-c", f"{key}={value}")
        ]
        command = ["git", *config_args, *args]
        result = self._run(
            command, cwd=str(cwd), capture_output=True, text=True
        )
        if result.returncode != 0:
            raise GitCommandError(
                f"gitコマンドが失敗しました: {' '.join(args)}",
                command=command,
                returncode=result.returncode,
                stderr=result.stderr,
            )
        return result.stdout


def _branch_name(mr_iid: int) -> str:
    return f"mr-{mr_iid}"


def _slugify_project(project: str) -> str:
    # `group/subgroup/project`のようなスラッシュ区切りを、深いディレクトリ階層を作らずに
    # 1階層のディレクトリ名へ落とし込む(Spike S-3 §7、Windowsのパス長制限対策)
    return project.replace("/", "__")


def _deslugify_project(slug: str) -> str:
    return slug.replace("__", "/")


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            total += entry.stat().st_size
    return total


__all__ = ["GitWorkspaceManager"]
