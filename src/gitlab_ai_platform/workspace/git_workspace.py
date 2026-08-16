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

並列レビュー実行(M2-1 [#80](https://github.com/AtsushiNi/gitlab-ai-platform/issues/80)、
`docs/adr/0015-parallel-review-execution.md`)以降、`GitWorkspaceManager`は複数のワーカー
スレッドから同時に呼ばれる。project単位のロック(`_project_lock`)で以下を守る:

- 同一project(=同一bare repo)への`clone`/`fetch`/`worktree prune`/`worktree add`/
  `reset --hard`が複数スレッドから同時に実行されないこと(bare repoの`.git/worktrees/`
  メタデータやref更新が競合すると破損しうるため)。異なるprojectは別ロックのため
  真に並行実行できる
- ロックが保護するのは`prepare`/`discard`本体(git操作)のみで、Claude Code Runnerの
  実行(呼び出し側が`prepare`の戻り値を受け取った後に行う、本来時間のかかる処理)は
  ロックの外で行われる。並列化の効果はここで確保する
- GC(`collect_garbage`/`_ensure_disk_budget`)は退避対象のprojectロックを
  `acquire(blocking=False)`で試みる。他スレッドが操作中(ロック取得できない)候補は
  スキップして次に古いものを試す。ブロッキング待ちをしないのは、A→B, B→Aのような
  ロック待ちの循環(デッドロック)を構造的に起こさないため(詳細はADR-0015参照)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .._paths import deslugify_project, slugify_project
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
        git_config: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        """
        `clone_url_for`はproject名(`group/project`形式)からbare clone用のURLを組み立てる関数。
        GitLab側のURL構築はこのモジュールの責務外とし、呼び出し側(config層)に委ねる。

        `git_config`は全てのgitコマンド呼び出しに`-c key=value`として渡す追加設定
        (`credential.helper`/`core.sshCommand`等の認証設定を想定)。`Mapping`(キー毎に
        1個)のほか、同じキーを複数回渡したい場合(例: 既存の`credential.helper`を
        空値でクリアしてから新しい値を設定する)のために`(key, value)`のタプル列も
        受け付ける。
        """
        self._root = Path(root)
        self._repos_dir = self._root / "repos"
        self._worktrees_dir = self._root / "worktrees"
        self._clone_url_for = clone_url_for
        self._max_disk_bytes = max_disk_bytes
        self._git_config: tuple[tuple[str, str], ...] = tuple(
            git_config.items()
            if isinstance(git_config, Mapping)
            else (git_config or ())
        )
        self._run = run
        # project(bare repo)単位のロック。同一projectへのgit操作(clone/fetch/worktree
        # add/reset等)が複数スレッドから同時に走るのを防ぐ(M2-1、ADR-0015)。ロック自体の
        # 生成をスレッドセーフにするための guard を別途持つ。
        # RLock(再入可能)にしているのは、prepare中のディスク上限チェック(GC)が同一project
        # 内の別MR(=同じロック)を退避する正当なケースを許すため(同一スレッドからの再入は
        # 許可し、他スレッドからの取得のみ非ブロッキングで弾く)
        self._project_locks: dict[str, threading.RLock] = {}
        self._project_locks_guard = threading.Lock()

        self._repos_dir.mkdir(parents=True, exist_ok=True)
        self._worktrees_dir.mkdir(parents=True, exist_ok=True)

    def prepare(self, project: str, mr_iid: int, ref: str) -> WorktreeHandle:
        with self._project_lock(project):
            return self._prepare_locked(project, mr_iid, ref)

    def discard(self, project: str, mr_iid: int) -> None:
        with self._project_lock(project):
            self._discard_worktree(
                project, mr_iid, self._worktree_path(project, mr_iid)
            )

    def collect_garbage(self) -> list[WorktreeHandle]:
        removed: list[WorktreeHandle] = []
        if self._disk_usage_bytes() <= self._max_disk_bytes:
            return removed

        for candidate in self._worktrees_sorted_by_age():
            if self._disk_usage_bytes() <= self._max_disk_bytes:
                break

            lock = self._project_lock(candidate.project)
            # ブロッキング待ちをしない: 他スレッドがこのprojectを操作中(prepare/discard
            # 実行中)なら安全のため退避をスキップし、次に古い候補を試す。ブロッキングで
            # 待つと「自分のprojectロックを保持したままGC中に他projectのロック待ちをする」
            # 別スレッドと循環待ち(デッドロック)になりうるため(ADR-0015参照)
            if not lock.acquire(blocking=False):
                continue
            try:
                # ロック取得までの間に既に破棄済み(別スレッドのGC等)なら何もしない
                if not candidate.path.exists():
                    continue
                self._discard_worktree(
                    candidate.project, candidate.mr_iid, candidate.path
                )
                removed.append(candidate)
                _logger.info(
                    "workspace.gc_evict",
                    extra={"project": candidate.project, "mr_iid": candidate.mr_iid},
                )
            finally:
                lock.release()
        return removed

    # -- 内部ヘルパー ----------------------------------------------------------

    def _project_lock(self, project: str) -> threading.RLock:
        with self._project_locks_guard:
            lock = self._project_locks.get(project)
            if lock is None:
                lock = threading.RLock()
                self._project_locks[project] = lock
            return lock

    def _prepare_locked(self, project: str, mr_iid: int, ref: str) -> WorktreeHandle:
        # 呼び出し元の`prepare`が`_project_lock(project)`を保持した状態でのみ呼ばれる想定
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
        self._run_git(
            ["fetch", "origin", "+refs/heads/*:refs/remotes/origin/*"], cwd=bare_path
        )
        # クラッシュ等でworktreeディレクトリだけが消えた場合の自己復旧(Spike S-3 §5)。
        # 以降の worktree add で「既に登録済み」エラーになるのを防ぐ
        self._run_git(["worktree", "prune"], cwd=bare_path)

        resolved_ref = self._resolve_ref(bare_path, ref)

        if worktree_path.exists():
            # 予算チェック(GC)の対象に自分自身が選ばれないよう、reset前に
            # 最終利用時刻を更新しておく(GCはLRU=最終利用時刻が古いものから破棄するため)
            os.utime(worktree_path, None)
            self._ensure_disk_budget()
            self._run_git(["reset", "--hard", resolved_ref], cwd=worktree_path)
        else:
            self._ensure_disk_budget()
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
            self._run_git(
                [
                    "worktree",
                    "add",
                    "-B",
                    branch_name,
                    str(worktree_path),
                    resolved_ref,
                ],
                cwd=bare_path,
            )
            # GCのLRU判定に使う最終利用時刻を更新する
            os.utime(worktree_path, None)

        sha = self._run_git(["rev-parse", "HEAD"], cwd=worktree_path).strip()
        return WorktreeHandle(
            project=project,
            mr_iid=mr_iid,
            path=worktree_path,
            branch=branch_name,
            sha=sha,
        )

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
                self._run_git(
                    ["worktree", "remove", "--force", str(path)], cwd=bare_path
                )
            else:
                shutil.rmtree(path)

        if bare_path.exists():
            try:
                self._run_git(["branch", "-D", branch_name], cwd=bare_path)
            except GitCommandError:
                # branchが既に存在しない(discardの二重呼び出し等)場合は無視する。
                # worktree自体の破棄は上で完了しているため、ここは後片付けの追加処理
                pass

    def _worktrees_sorted_by_age(self) -> list[WorktreeHandle]:
        # GCの退避候補を最終利用時刻の古い順に並べて返す(LRU)。並行GC(M2-1)では
        # 先頭からロック取得を試み、取得できない(他スレッドが操作中の)候補は
        # 呼び出し側でスキップして次点を試す前提のため、1件だけでなく全件を返す
        candidates = [
            wt_dir
            for slug_dir in self._worktrees_dir.iterdir()
            if slug_dir.is_dir()
            for wt_dir in slug_dir.iterdir()
            if wt_dir.is_dir() and wt_dir.name.startswith("mr-")
        ]
        candidates.sort(key=lambda p: p.stat().st_mtime)

        handles = []
        for wt_dir in candidates:
            project = _deslugify_project(wt_dir.parent.name)
            mr_iid = int(wt_dir.name.removeprefix("mr-"))
            try:
                sha = self._run_git(["rev-parse", "HEAD"], cwd=wt_dir).strip()
            except GitCommandError:
                sha = ""
            handles.append(
                WorktreeHandle(
                    project=project,
                    mr_iid=mr_iid,
                    path=wt_dir,
                    branch=wt_dir.name,
                    sha=sha,
                )
            )
        return handles

    def _resolve_ref(self, bare_path: Path, ref: str) -> str:
        # `refs/remotes/origin/<ref>`が存在すればそちらを優先する(clone直後のstaleな
        # refs/heads/<ref>ではなく、常にfetch済みの最新commitを見るため)。存在しなければ
        # commit shaが渡されたとみなし、`ref`をそのまま使う。
        # `--quiet`のため終了コード非0は「存在しない」を意味する想定だが、_run_gitと
        # 同じく`git_config`の`-c`引数も適用する(safe.directory等の認証・環境設定が
        # このプローブだけ効かず、無関係な失敗を「refが存在しない」と誤解釈しないため)
        remote_ref = f"refs/remotes/origin/{ref}"
        probe = self._run(
            [
                "git",
                *self._config_args(),
                "rev-parse",
                "--verify",
                "--quiet",
                remote_ref,
            ],
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

    def _config_args(self) -> list[str]:
        return [
            arg for key, value in self._git_config for arg in ("-c", f"{key}={value}")
        ]

    def _run_git(self, args: list[str], *, cwd: Path) -> str:
        command = ["git", *self._config_args(), *args]
        result = self._run(command, cwd=str(cwd), capture_output=True, text=True)
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


# runner/subprocess_runner.pyも同じ規則(project→1階層ディレクトリ名)を必要とするため、
# `_paths`モジュールに集約したものをそのまま使う(以前はモジュールごとに同じコードを
# 複製していた)
_slugify_project = slugify_project
_deslugify_project = deslugify_project


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            total += entry.stat().st_size
    return total


__all__ = ["GitWorkspaceManager"]
