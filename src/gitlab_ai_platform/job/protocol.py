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
- M3-1のスコープは`enqueue`/`get`/`update_status`/`list_by_status`/`close`の5メソッドのみ
  だった。M3-2(Job Queue、[#92](https://github.com/AtsushiNi/gitlab-ai-platform/issues/92)、
  `docs/adr/0017-job-queue.md`)で、取得の排他・可視性タイムアウト・リトライ・デッドレターを
  実現する`claim`/`heartbeat`/`complete`/`fail`/`list_dead_letters`を追加した。既存5メソッドの
  シグネチャ・挙動(`enqueue`はキーワード専用引数`max_attempts`の追加のみ)は変更していない
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

# Jobが1回のclaimで失敗した際、デフォルトで何回まで再試行を許すか(ADR-0017)。
# 0回目のclaimも1カウントするため、既定値3は「初回+再試行2回」を意味する
DEFAULT_MAX_ATTEMPTS = 3

# claimしたが完了/失敗の報告がないJobを、可視性タイムアウトとしてPENDINGへ戻すまでの
# 既定秒数(ADR-0017)。Claude Codeのheadless実行は数分〜数十分かかりうるため、Runner
# Dispatcher(M3-3)は`heartbeat`でこれより短い間隔でリースを延長する運用を想定する
DEFAULT_VISIBILITY_TIMEOUT_SECONDS = 600


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
    """1件のタスク実行を表すレコード。

    `attempts`以降のフィールドはM3-2(ADR-0017)で追加したキュー関連の付帯情報。
    既存フィールドの並び・型は変更せず末尾にデフォルト値付きで追加しているため、
    キーワード引数での構築は無改修で動く。
    """

    id: str
    job_type: JobType
    status: JobStatus
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    attempts: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    dead_letter_at: datetime | None = None


@runtime_checkable
class JobRepository(Protocol):
    """タスク種別を横断してJobのライフサイクル(状態機械)を管理する。"""

    def enqueue(
        self,
        job_type: JobType,
        payload: dict[str, Any],
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> Job:
        """新しいJobを`PENDING`状態で作成する。

        `max_attempts`は`claim`によるリトライの上限(ADR-0017)。
        """
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

    def claim(
        self,
        worker_id: str,
        *,
        job_types: Sequence[JobType] | None = None,
        visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    ) -> Job | None:
        """`PENDING`のJobを1件、他のworkerと排他して取得し`RUNNING`へ遷移させる(ADR-0017)。

        取得前に、可視性タイムアウトを過ぎた`RUNNING`のJob(claimされたまま完了/失敗の
        報告がないもの)を回収する(リトライ上限未満なら`PENDING`へ戻し、上限に達していれば
        `FAILED`かつデッドレターとして確定する)。`job_types`を指定すると、その種別のみを
        対象にする。取得できるJobがなければ`None`を返す。
        """
        ...

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    ) -> Job:
        """claim済みJobのリース(可視性タイムアウトの期限)を延長する。

        `worker_id`が現在のリース所有者と一致しない場合は`LeaseLostError`を送出する。
        """
        ...

    def complete(
        self, job_id: str, worker_id: str, result: dict[str, Any] | None = None
    ) -> Job:
        """claim済みJobを正常終了として報告し`DONE`へ遷移させる。

        `worker_id`が現在のリース所有者と一致しない場合は`LeaseLostError`を送出する。
        """
        ...

    def fail(
        self, job_id: str, worker_id: str, error: str, *, retry: bool = True
    ) -> Job:
        """claim済みJobの失敗を報告する。

        `retry=True`(既定)かつリトライ上限未満であれば`PENDING`へ戻し再試行対象にする。
        `retry=False`、またはリトライ上限に達している場合は`FAILED`へ遷移させ、デッドレター
        として確定する。`worker_id`が現在のリース所有者と一致しない場合は`LeaseLostError`を
        送出する。
        """
        ...

    def list_dead_letters(self) -> list[Job]:
        """デッドレター化した(リトライ上限を超えて失敗が確定した)Jobを一覧する。"""
        ...


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_VISIBILITY_TIMEOUT_SECONDS",
    "Job",
    "JobRepository",
    "JobStatus",
    "JobType",
]
