"""Issue Ticket Store のインターフェース定義。

方針(M4-1 [#107](https://github.com/AtsushiNi/gitlab-ai-platform/issues/107)、
`docs/adr/0024-issue-poller-dedup.md`):

- State Store(`store/protocol.py`)と同じ`typing.Protocol`による抽象化パターンを踏襲するが、
  別コンポーネントとして新設する(State Storeを拡張しない)。State Storeの主キー
  `(project, mr_iid, commit_sha)`はレビュー固有であり、Issueには`commit_sha`に相当する
  「版」の概念がないため、無理に同じテーブル・同じProtocolに寄せるとレビュー以外のJob種別が
  増えるたびにState Storeのスキーマを歪めることになる(ADR-0016がState StoreとJobの統合を
  却下した理由と同じ判断)。
- `(project, issue_iid)`の一意制約による二重投入防止は、`create`が既存レコードに対して
  呼ばれた場合に`DuplicateIssueTicketError`を送出するという契約で表現する(実際の一意性保証は
  実装側のDBスキーマ・制約が担う、`StateStore.create`と同じ設計)。
- ビジネスロジック(Issueを無人実行すべきか否かの判断)は持たない。単なる状態の記録・照会のみ。
- `status`のような進行状態は持たない。Issueの無人実行の進行状態はJob
  (`job/protocol.py`の`JobStatus`)が単独で管理する(`docs/adr/0024-issue-poller-dedup.md`
  「決定」参照)。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import IssueTicketRecord


@runtime_checkable
class IssueTicketStore(Protocol):
    """`(project, issue_iid)`単位で無人実行Jobの起票済み状態を記録・照会する。"""

    def find(self, project: str, issue_iid: int) -> IssueTicketRecord | None:
        """指定Issueの起票記録を返す。存在しなければ`None`(未起票として扱える)。"""
        ...

    def create(self, project: str, issue_iid: int) -> IssueTicketRecord:
        """新しい起票記録を作成する。

        同一の`(project, issue_iid)`が既に存在する場合は`DuplicateIssueTicketError`を
        送出し、二重投入を防ぐ。
        """
        ...

    def close(self) -> None:
        """DB接続等の内部リソースを解放する。"""
        ...


__all__ = ["IssueTicketStore"]
