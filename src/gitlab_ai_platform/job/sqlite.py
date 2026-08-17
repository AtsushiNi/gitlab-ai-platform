"""`JobRepository` を満たすSQLite実装。

方針(M3-1 [#91](https://github.com/AtsushiNi/gitlab-ai-platform/issues/91)、
M3-2 [#92](https://github.com/AtsushiNi/gitlab-ai-platform/issues/92)、
`docs/adr/0016-job-abstraction.md`、`docs/adr/0017-job-queue.md`):

- 標準ライブラリの`sqlite3`のみを使う(ADR-0001が許可する外部依存は`requests`/`pytest`のみ)。
- `payload`/`result`はJSON文字列としてTEXTカラムに保存し、アプリケーション側で
  `json.dumps`/`json.loads`する(ORMは使わない)。
- 既存レビュー処理(M2-1、`cli/worker_pool.py`)は複数のワーカースレッドから並行に呼ばれるため、
  `store/sqlite.py`(`SqliteStateStore`)と同じく`threading.RLock`で全メソッドの本体を
  直列化する(`update_status`が内部で`get`を呼ぶため`RLock`を使う)。
- M3-2(ADR-0017): 取得の排他(`claim`)・可視性タイムアウト・リトライ・デッドレターを追加した。
  `claim`は「対象を選ぶ→更新する」を1つのUPDATE文で行うことでプロセス内外の競合を防ぎ、
  `PRAGMA busy_timeout`で複数プロセスからの同時書き込みを待たせる(即座に
  `database is locked`にしない)。既存5メソッド(`enqueue`/`get`/`update_status`/
  `list_by_status`/`close`)の挙動は変更していない(`enqueue`はキーワード専用引数
  `max_attempts`の追加のみ)。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import (
    InvalidJobTransitionError,
    JobError,
    JobNotFoundError,
    LeaseLostError,
)
from .protocol import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    Job,
    JobStatus,
    JobType,
)

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT {DEFAULT_MAX_ATTEMPTS},
    lease_owner TEXT,
    lease_token TEXT,
    lease_expires_at TEXT,
    dead_letter_at TEXT
)
"""
_CREATE_INDEX_STATUS_SQL = "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"
# ADR-0016の見出しは「job_type/status双方にインデックスを張ったテーブル」としているため、
# 決定済みのSQL例(status用のみ)に加えてjob_type用も張る
_CREATE_INDEX_JOB_TYPE_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_jobs_job_type ON jobs(job_type)"
)
# 可視性タイムアウトの回収(claim実行時に期限切れRUNNINGを探す)で使うインデックス(ADR-0017)
_CREATE_INDEX_LEASE_EXPIRES_AT_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_jobs_lease_expires_at ON jobs(lease_expires_at)"
)

# M3-1時点のJob DB(新カラムを持たない)を後方互換でアップグレードするためのカラム一覧
# (ADR-0017)。新規DBは_CREATE_TABLE_SQLの時点で全カラムを持つため、このマイグレーションは
# 既存カラムが揃っていれば実質何もしない
_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("max_attempts", f"INTEGER NOT NULL DEFAULT {DEFAULT_MAX_ATTEMPTS}"),
    ("lease_owner", "TEXT"),
    ("lease_token", "TEXT"),
    ("lease_expires_at", "TEXT"),
    ("dead_letter_at", "TEXT"),
)

# 許可される状態遷移(`docs/adr/0016-job-abstraction.md`)。DONE/FAILEDは終端状態で
# 遷移先を持たない。`fail`のリトライパス(RUNNING → PENDING)はキュー内部専用の遷移で、
# ここには含めない(`docs/adr/0017-job-queue.md`「決定」参照)
_ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.RUNNING}),
    JobStatus.RUNNING: frozenset(
        {JobStatus.DONE, JobStatus.FAILED, JobStatus.WAITING_HUMAN}
    ),
    JobStatus.WAITING_HUMAN: frozenset({JobStatus.RUNNING, JobStatus.FAILED}),
    JobStatus.DONE: frozenset(),
    JobStatus.FAILED: frozenset(),
}

_SELECT_COLUMNS = (
    "id, job_type, status, payload, result, error, created_at, updated_at, "
    "attempts, max_attempts, lease_owner, lease_expires_at, dead_letter_at"
)


class SqliteJobRepository:
    """SQLite(標準ライブラリ`sqlite3`)経由で`JobRepository`を実装する。"""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        # State Store(store/sqlite.py)と同じ理由でcheck_same_thread=Falseにする
        # (接続を作ったスレッド以外からの呼び出しを許容する必要があるため)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        # 複数プロセスが同時に書き込もうとした場合、即座にOperationalError
        # (database is locked)にせず、一定時間は待って再試行させる(ADR-0015が課題視した
        # 問題への対処。`claim`のUPDATE文はこのタイムアウト内で解決する想定、
        # `docs/adr/0017-job-queue.md`)
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute(_CREATE_TABLE_SQL)
        _migrate_schema(self._conn)
        self._conn.execute(_CREATE_INDEX_STATUS_SQL)
        self._conn.execute(_CREATE_INDEX_JOB_TYPE_SQL)
        self._conn.execute(_CREATE_INDEX_LEASE_EXPIRES_AT_SQL)
        self._conn.commit()
        # 全メソッドの本体を直列化するロック(モジュールdocstring参照)。update_statusが
        # 内部でgetを呼ぶ(再入する)ためRLockを使う
        self._lock = threading.RLock()

    def enqueue(
        self,
        job_type: JobType,
        payload: dict[str, Any],
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> Job:
        with self._lock:
            job_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            try:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO jobs "
                        "(id, job_type, status, payload, result, error, "
                        "created_at, updated_at, attempts, max_attempts, "
                        "lease_owner, lease_token, lease_expires_at, dead_letter_at) "
                        "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, 0, ?, "
                        "NULL, NULL, NULL, NULL)",
                        (
                            job_id,
                            job_type.value,
                            JobStatus.PENDING.value,
                            json.dumps(payload, ensure_ascii=False),
                            now.isoformat(),
                            now.isoformat(),
                            max_attempts,
                        ),
                    )
            except sqlite3.Error as exc:
                raise JobError(f"Jobの起票に失敗しました: {exc}") from exc

            return Job(
                id=job_id,
                job_type=job_type,
                status=JobStatus.PENDING,
                payload=payload,
                result=None,
                error=None,
                created_at=now,
                updated_at=now,
                attempts=0,
                max_attempts=max_attempts,
                lease_owner=None,
                lease_expires_at=None,
                dead_letter_at=None,
            )

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM jobs WHERE id = ?",
                    (job_id,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise JobError(f"Jobの照会に失敗しました: {exc}") from exc
            return _row_to_job(row) if row is not None else None

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Job:
        with self._lock:
            current = self.get(job_id)
            if current is None:
                raise JobNotFoundError(f"job_id={job_id!r} のJobが見つかりません")

            allowed = _ALLOWED_TRANSITIONS[current.status]
            if status not in allowed:
                raise InvalidJobTransitionError(
                    f"{current.status.value!r} から {status.value!r} への遷移は"
                    "許可されていません"
                )

            now = datetime.now(UTC)
            try:
                with self._conn:
                    self._conn.execute(
                        "UPDATE jobs SET status = ?, "
                        "result = COALESCE(?, result), "
                        "error = COALESCE(?, error), "
                        "updated_at = ? WHERE id = ?",
                        (
                            status.value,
                            json.dumps(result, ensure_ascii=False)
                            if result is not None
                            else None,
                            error,
                            now.isoformat(),
                            job_id,
                        ),
                    )
            except sqlite3.Error as exc:
                raise JobError(f"Jobの状態更新に失敗しました: {exc}") from exc

            # RLockのため再入可能: 他スレッドがこの間に割り込むことはない
            updated = self.get(job_id)
            assert updated is not None
            return updated

    def list_by_status(self, status: JobStatus) -> list[Job]:
        with self._lock:
            try:
                rows = self._conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM jobs WHERE status = ? "
                    "ORDER BY created_at",
                    (status.value,),
                ).fetchall()
            except sqlite3.Error as exc:
                raise JobError(f"Jobの一覧取得に失敗しました: {exc}") from exc
            return [_row_to_job(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def claim(
        self,
        worker_id: str,
        *,
        job_types: Sequence[JobType] | None = None,
        visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    ) -> Job | None:
        with self._lock:
            now = datetime.now(UTC)
            # 可視性タイムアウトを過ぎたRUNNINGを先に回収してからでないと、まだ生きている
            # (はずの)Jobを新規取得できてしまう(`docs/adr/0017-job-queue.md`)
            self._reclaim_expired_locked(now)

            lease_token = str(uuid.uuid4())
            lease_expires_at = now + timedelta(seconds=visibility_timeout_seconds)
            set_params: tuple[Any, ...] = (
                JobStatus.RUNNING.value,
                worker_id,
                lease_token,
                lease_expires_at.isoformat(),
                now.isoformat(),
            )
            type_filter_sql = ""
            select_params: tuple[Any, ...] = (JobStatus.PENDING.value,)
            if job_types:
                placeholders = ",".join("?" for _ in job_types)
                type_filter_sql = f" AND job_type IN ({placeholders})"
                select_params = (
                    JobStatus.PENDING.value,
                    *(t.value for t in job_types),
                )

            try:
                with self._conn:
                    # 「対象を選ぶ→更新する」を1つのUPDATE文にまとめることで、SQLiteの
                    # 文単位のアトミック性だけでTOCTOU競合を防ぐ(`docs/adr/0017-job-queue.md`
                    # 「決定」参照。2段階(SELECT→UPDATE)にすると別コネクションの割り込みを
                    # 防ぐために明示的なトランザクション制御が必要になる)
                    self._conn.execute(
                        "UPDATE jobs SET status = ?, lease_owner = ?, lease_token = ?, "
                        "lease_expires_at = ?, attempts = attempts + 1, updated_at = ? "
                        "WHERE id = ("
                        f"  SELECT id FROM jobs WHERE status = ?{type_filter_sql} "
                        "  ORDER BY created_at LIMIT 1"
                        ")",
                        (*set_params, *select_params),
                    )
                # lease_tokenは呼び出しごとに一意なため、直後にこれで引けば他のclaim呼び出し
                # (別スレッド・別プロセス)の結果と混同しない
                row = self._conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM jobs WHERE lease_token = ?",
                    (lease_token,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise JobError(f"Jobの取得(claim)に失敗しました: {exc}") from exc

            return _row_to_job(row) if row is not None else None

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    ) -> Job:
        with self._lock:
            self._require_leased_locked(job_id, worker_id)
            now = datetime.now(UTC)
            new_expires_at = now + timedelta(seconds=visibility_timeout_seconds)
            try:
                with self._conn:
                    self._conn.execute(
                        "UPDATE jobs SET lease_expires_at = ?, updated_at = ? "
                        "WHERE id = ?",
                        (new_expires_at.isoformat(), now.isoformat(), job_id),
                    )
            except sqlite3.Error as exc:
                raise JobError(f"リースの延長(heartbeat)に失敗しました: {exc}") from exc
            updated = self.get(job_id)
            assert updated is not None
            return updated

    def complete(
        self, job_id: str, worker_id: str, result: dict[str, Any] | None = None
    ) -> Job:
        with self._lock:
            self._require_leased_locked(job_id, worker_id)
            # RUNNING → DONEは既存のupdate_status(ADR-0016で許可済みの遷移)をそのまま使う
            self.update_status(job_id, JobStatus.DONE, result=result)
            self._clear_lease_locked(job_id)
            updated = self.get(job_id)
            assert updated is not None
            return updated

    def fail(
        self, job_id: str, worker_id: str, error: str, *, retry: bool = True
    ) -> Job:
        with self._lock:
            self._require_leased_locked(job_id, worker_id)
            return self._fail_locked(job_id, error, retry)

    def list_dead_letters(self) -> list[Job]:
        with self._lock:
            try:
                rows = self._conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM jobs "
                    "WHERE dead_letter_at IS NOT NULL ORDER BY created_at"
                ).fetchall()
            except sqlite3.Error as exc:
                raise JobError(
                    f"デッドレターJobの一覧取得に失敗しました: {exc}"
                ) from exc
            return [_row_to_job(row) for row in rows]

    def _reclaim_expired_locked(self, now: datetime) -> None:
        """可視性タイムアウトを過ぎたRUNNINGのJobを回収する(`docs/adr/0017-job-queue.md`)。

        リトライ上限未満ならPENDINGへ戻し、上限に達していればFAILED+デッドレターとして
        確定する(`_fail_locked`を再利用)。`claim`の本体UPDATEより先に呼ぶ。
        """
        try:
            rows = self._conn.execute(
                "SELECT id FROM jobs WHERE status = ? "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                (JobStatus.RUNNING.value, now.isoformat()),
            ).fetchall()
        except sqlite3.Error as exc:
            raise JobError(f"可視性タイムアウトの回収に失敗しました: {exc}") from exc

        for (job_id,) in rows:
            self._fail_locked(
                job_id,
                "可視性タイムアウト: claim元からの完了/失敗の報告がないまま"
                "期限を過ぎました",
                retry=True,
            )

    def _fail_locked(self, job_id: str, error: str, retry: bool) -> Job:
        """`fail`/可視性タイムアウト回収の共通ロジック。呼び出し前提: `self._lock`保持済み。"""
        current = self.get(job_id)
        if current is None:
            raise JobNotFoundError(f"job_id={job_id!r} のJobが見つかりません")

        now = datetime.now(UTC)
        if retry and current.attempts < current.max_attempts:
            # リトライ可能: RUNNING → PENDING。ADR-0016の遷移表にはないキュー内部専用の
            # 遷移のため、update_status(_ALLOWED_TRANSITIONS)は経由せず直接更新する
            # (`docs/adr/0017-job-queue.md`「決定」参照)
            try:
                with self._conn:
                    self._conn.execute(
                        "UPDATE jobs SET status = ?, lease_owner = NULL, "
                        "lease_token = NULL, lease_expires_at = NULL, error = ?, "
                        "updated_at = ? WHERE id = ?",
                        (JobStatus.PENDING.value, error, now.isoformat(), job_id),
                    )
            except sqlite3.Error as exc:
                raise JobError(f"Jobの再試行への遷移に失敗しました: {exc}") from exc
        else:
            # リトライ不可 or 上限到達: RUNNING → FAILED(既存update_statusを再利用)し、
            # デッドレターとして確定する
            self.update_status(job_id, JobStatus.FAILED, error=error)
            try:
                with self._conn:
                    self._conn.execute(
                        "UPDATE jobs SET dead_letter_at = ?, lease_owner = NULL, "
                        "lease_token = NULL, lease_expires_at = NULL, updated_at = ? "
                        "WHERE id = ?",
                        (now.isoformat(), now.isoformat(), job_id),
                    )
            except sqlite3.Error as exc:
                raise JobError(f"デッドレター化に失敗しました: {exc}") from exc

        updated = self.get(job_id)
        assert updated is not None
        return updated

    def _require_leased_locked(self, job_id: str, worker_id: str) -> Job:
        """`job_id`が`worker_id`によってclaim中であることを検証する。

        呼び出し前提: `self._lock`保持済み。リースが無い/別workerが保持している場合は
        `LeaseLostError`を送出する(`docs/adr/0017-job-queue.md`)。
        """
        current = self.get(job_id)
        if current is None:
            raise JobNotFoundError(f"job_id={job_id!r} のJobが見つかりません")
        if current.lease_owner != worker_id:
            raise LeaseLostError(
                f"job_id={job_id!r} のリースは worker_id={worker_id!r} が保持していません"
                f"(現在のリース所有者: {current.lease_owner!r})"
            )
        return current

    def _clear_lease_locked(self, job_id: str) -> None:
        now = datetime.now(UTC)
        try:
            with self._conn:
                self._conn.execute(
                    "UPDATE jobs SET lease_owner = NULL, lease_token = NULL, "
                    "lease_expires_at = NULL, updated_at = ? WHERE id = ?",
                    (now.isoformat(), job_id),
                )
        except sqlite3.Error as exc:
            raise JobError(f"リース情報のクリアに失敗しました: {exc}") from exc


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """M3-1時点のJob DB(新カラムを持たない)へ、不足しているカラムのみ追加する。

    新規DBは`_CREATE_TABLE_SQL`の時点で全カラムを持つため、この関数は実質何もしない
    (`docs/adr/0017-job-queue.md`)。
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    for name, ddl in _MIGRATION_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {ddl}")
    conn.commit()


def _row_to_job(row: tuple) -> Job:
    (
        job_id,
        job_type,
        status,
        payload,
        result,
        error,
        created_at,
        updated_at,
        attempts,
        max_attempts,
        lease_owner,
        lease_expires_at,
        dead_letter_at,
    ) = row
    return Job(
        id=job_id,
        job_type=JobType(job_type),
        status=JobStatus(status),
        payload=json.loads(payload),
        result=json.loads(result) if result is not None else None,
        error=error,
        created_at=datetime.fromisoformat(created_at),
        updated_at=datetime.fromisoformat(updated_at),
        attempts=attempts,
        max_attempts=max_attempts,
        lease_owner=lease_owner,
        lease_expires_at=(
            datetime.fromisoformat(lease_expires_at)
            if lease_expires_at is not None
            else None
        ),
        dead_letter_at=(
            datetime.fromisoformat(dead_letter_at)
            if dead_letter_at is not None
            else None
        ),
    )


__all__ = ["SqliteJobRepository"]
