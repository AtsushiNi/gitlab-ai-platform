"""Workspace Manager のインターフェース定義。

方針(M1-6 [#34](https://github.com/AtsushiNi/gitlab-ai-platform/issues/34)、
`docs/architecture.md`「Workspace Manager」、`docs/adr/0004-workspace-manager-design.md`):

- GitLab Adapter(M1-1)・State Store(M1-4)と同じく`typing.Protocol`で抽象化し、実装
  (git実装、Linux/Docker上での将来の差し替え)を呼び出し側(Poller/Runner/CLI)から
  切り離す。
- プロジェクト単位のbare cloneとMR単位のworktreeという2階層のモデルを、呼び出し側からは
  「MR単位のworktreeを用意する/破棄する」という2操作に見せる。bare cloneの存在・更新は
  実装内部の詳細として隠す。
- git操作以外(ビルド・テスト実行など)はしない(`docs/architecture.md`の境界)。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import WorktreeHandle


@runtime_checkable
class WorkspaceManager(Protocol):
    """プロジェクトごとのbare clone、MR単位のworktree作成/更新/破棄、ディスク上限とGCを管理する。"""

    def prepare(self, project: str, mr_iid: int, ref: str) -> WorktreeHandle:
        """指定MRのworktreeを用意する。

        bare cloneが無ければ作成し、あればfetchして最新化する。worktreeが無ければ新規作成し、
        既にあれば`ref`(branch名またはcommit sha)へ`reset --hard`して最新化する。
        新規worktree作成時にディスク上限を超える場合は`collect_garbage`を内部で実行し、
        それでも空き容量を確保できなければ`DiskLimitExceededError`を送出する。
        """
        ...

    def discard(self, project: str, mr_iid: int) -> None:
        """指定MRのworktreeを破棄する。

        対象worktreeが存在しない場合も例外を送出せず、何もしない(`rm -f`と同様の冪等な操作)。
        """
        ...

    def collect_garbage(self) -> list[WorktreeHandle]:
        """ディスク上限を超えている場合、最終利用時刻が古いworktreeから破棄する。

        破棄したworktreeの一覧(破棄直前の状態)を返す。上限以下であれば何もせず空リストを返す。
        """
        ...


__all__ = ["WorkspaceManager"]
