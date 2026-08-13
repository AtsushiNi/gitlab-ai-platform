"""State Store のインターフェース定義。

方針(M1-4 [#32](https://github.com/AtsushiNi/gitlab-ai-platform/issues/32)、
`docs/architecture.md`「State Store」、`docs/adr/0003-state-store-interface.md`):

- GitLab Adapter(M1-1)と同じく`typing.Protocol`で抽象化し、実装(SQLite, M1-4。将来の
  PostgreSQL移行, M3-5)を差し替え可能にする。呼び出し側(Poller/Runner/CLI)はこの
  Protocol型だけを見て実装し、具象クラスに直接依存しない。
- `(project, mr_iid, commit_sha)`の一意制約による二重レビュー防止は、`create`が
  既存レコードに対して呼ばれた場合に`DuplicateReviewError`を送出するという契約で表現する
  (実際の一意性保証は実装側のDBスキーマ・制約が担う)。
- ビジネスロジック(レビューするか否かの判断)は持たない。単なる状態の記録・照会のみ
  (`docs/architecture.md`のState Storeの境界)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .types import ReviewRecord, ReviewStatus


@runtime_checkable
class StateStore(Protocol):
    """`(project, mr_iid, commit_sha)`単位でレビュー状態を記録・照会する。"""

    def find(self, project: str, mr_iid: int, commit_sha: str) -> ReviewRecord | None:
        """指定commitのレビュー記録を返す。存在しなければ`None`(未処理commitとして扱える)。"""
        ...

    def create(
        self,
        project: str,
        mr_iid: int,
        commit_sha: str,
        *,
        status: ReviewStatus = ReviewStatus.PENDING,
    ) -> ReviewRecord:
        """新しいレビュー記録を作成する。

        同一の`(project, mr_iid, commit_sha)`が既に存在する場合は`DuplicateReviewError`を
        送出し、二重レビューの起票を防ぐ。
        """
        ...

    def update_status(
        self,
        project: str,
        mr_iid: int,
        commit_sha: str,
        status: ReviewStatus,
        *,
        reviewed_at: datetime | None = None,
        result_path: str | None = None,
    ) -> ReviewRecord:
        """既存のレビュー記録の状態を更新する。

        対象レコードが存在しない場合は`RecordNotFoundError`を送出する。
        """
        ...

    def close(self) -> None:
        """DB接続等の内部リソースを解放する。"""
        ...


__all__ = ["StateStore"]
