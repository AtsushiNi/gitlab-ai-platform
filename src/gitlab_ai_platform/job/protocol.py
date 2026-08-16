"""Job抽象のインターフェース定義。

方針(M3-1 [#91](https://github.com/AtsushiNi/gitlab-ai-platform/issues/91)、
`docs/architecture.md`「Job抽象・状態機械」、
`docs/adr/0016-job-abstraction.md`):

- Poller(検出)とRunner(実行)の間に挿入される新規レイヤー。State Store(`store/`)は
  `(project, mr_iid, commit_sha)`単位の二重レビュー防止に責務を絞ったまま残し、Jobは
  レビューに限らないタスク種別を横断的に管理する別コンポーネントとして併存させる
  (Jobとの統合・置き換えはしない、ADR-0016「却下した選択肢」)。
- `JobType`は`review`/`issue-analysis`/`design`/`implement`の4値を今のうちに列挙する。
  Jobレコードは永続化されるため、後から列挙値を追加するとDBに保存済みの過去レコードとの
  互換性を考える必要が出るため。M3-1時点で実際にRunnerが処理できるのは`review`のみで、
  他の値は将来(M4)の予約
- `JobStatus`の遷移は`PENDING → RUNNING → (DONE | FAILED | WAITING_HUMAN)`を基本とし、
  `WAITING_HUMAN`からの復帰(`RUNNING`)・却下(`FAILED`)のみ例外的に許可する。許可される
  遷移の一覧は`sqlite.py`の`_ALLOWED_TRANSITIONS`を参照。妥当性チェックは
  `JobRepository`実装側の責務とし、不正な遷移は`InvalidJobTransitionError`を送出する
- `payload`/`result`は種別非依存の`dict[str, Any]`として扱う(JSON互換)。`review`固有の
  フィールド(MR情報等)や将来の`design`/`implement`固有のフィールドはJob抽象そのものに
  持たせない。型ごとの構造は呼び出し側(`review`パッケージ等)が定義する
  (ADR-0016「Jobはペイロード・結果を種別非依存のdictとして持つ」)
- GitLab Adapter(ADR-0002)・State Store(ADR-0003)と同じく`typing.Protocol`を使い、
  `abc.ABC`は使わない。`@runtime_checkable`を付け、テスト・呼び出し側の防御的チェックに
  `isinstance`を使えるようにする
- **この5メソッド(`enqueue`/`get`/`update_status`/`list_by_status`/`close`)のみを
  M3-1のスコープとする。** 取得の排他・可視性タイムアウト・リトライ・デッドレターは
  M3-2(Job Queue)のスコープであり、`claim`のような新メソッドが必要かどうかもM3-2側の
  ADRで判断する(ADR-0016「却下した選択肢」)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class JobType(str, Enum):
    """Jobが扱うタスクの種別。"""

    REVIEW = "review"
    ISSUE_ANALYSIS = "issue-analysis"  # M4で実装
    DESIGN = "design"  # M4で実装
    IMPLEMENT = "implement"  # M4で実装


class JobStatus(str, Enum):
    """Jobの進行状態(状態機械)。"""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class Job:
    """1件のタスク実行を表すレコード。"""

    id: str
    job_type: JobType
    status: JobStatus
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime


@runtime_checkable
class JobRepository(Protocol):
    """タスク種別を横断してJobのライフサイクル(状態機械)を管理する。"""

    def enqueue(self, job_type: JobType, payload: dict[str, Any]) -> Job:
        """新しいJobを`PENDING`状態で作成する。"""
        ...

    def get(self, job_id: str) -> Job | None:
        """指定IDのJobを返す。存在しなければ`None`。"""
        ...

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Job:
        """Jobの状態を更新する。

        許可されていない遷移(モジュールdocstring参照)は`InvalidJobTransitionError`を
        送出する。指定IDのJobが存在しない場合は`JobNotFoundError`を送出する。
        """
        ...

    def list_by_status(self, status: JobStatus) -> list[Job]:
        """指定した状態のJobを一覧する。"""
        ...

    def close(self) -> None:
        """DB接続等の内部リソースを解放する。"""
        ...


__all__ = ["Job", "JobRepository", "JobStatus", "JobType"]
