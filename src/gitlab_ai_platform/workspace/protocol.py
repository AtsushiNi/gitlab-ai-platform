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

M4-8([#114](https://github.com/AtsushiNi/gitlab-ai-platform/issues/114)、ADR-0031)で、
Issue単位のworktreeを扱う`prepare_for_issue`/`discard_for_issue`を追加した。既存の
`prepare`/`discard`(MR単位、シグネチャ・挙動とも変更しない)とは別の名前空間
(`issue-<iid>`、`mr-<iid>`とは完全に分離)を使う専用メソッドとして追加する形にし、
ADR-0029が指摘した「MRとIssueのIID採番が独立しているため、同じ`mr_iid`引数に
Issue IIDを流用すると数値が偶然一致した際にworktreeを`reset --hard`で破損させる」
という衝突リスクを構造的に解消する(ADR-0026/0027と同じ「既存メソッドは変更せず
新メソッドを追加する」方針)。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import IssueWorktreeHandle, WorktreeHandle


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
        MR単位のworktree(`mr-<iid>`)のみを対象とする。Issue単位のworktree
        (`prepare_for_issue`が作成するもの)はこのGCの対象に含まれない(ADR-0031「今後の課題」)。
        """
        ...

    def prepare_for_issue(
        self, project: str, issue_iid: int, ref: str
    ) -> IssueWorktreeHandle:
        """指定Issueのworktreeを用意する(M4-8、ADR-0031)。

        `prepare`(MR単位)と同じ内部機構(bare clone/fetch/`git worktree`)を使うが、
        ワークツリーのパス・ローカルbranch名は`issue-<iid>`という別名前空間を使い、
        `mr-<iid>`とは物理的に衝突しない。`ref`はcreate_branch等で用意した実装用branch名
        (またはcommit sha)を渡す想定(呼び出し側がdefault branchの解決・branch作成を行う)。
        """
        ...

    def discard_for_issue(self, project: str, issue_iid: int) -> None:
        """指定Issueのworktreeを破棄する(M4-8、ADR-0031)。

        `discard`(MR単位)と同じく冪等(対象が存在しなくても例外を送出しない)。
        呼び出し側(実装フェーズのJobHandler)は、ローカルcommitをM4-9のpushフェーズが
        参照できるようにするため、実装成功時は明示的にこのメソッドを呼ばない設計にしている
        (`docs/adr/0033-implement-phase.md`参照)。
        """
        ...


__all__ = ["WorkspaceManager"]
