"""`JobRepository` を満たすSQLite実装。

方針(M3-1 [#91](https://github.com/AtsushiNi/gitlab-ai-platform/issues/91)、
`docs/adr/0016-job-abstraction.md`):

- 標準ライブラリの`sqlite3`のみを使う(ADR-0001が許可する外部依存は`requests`/`pytest`のみ)。
- ADR-0016時点では**単一プロセス・逐次実行を前提にした最小のSQLite実装**とする。取得の排他
  (`claim`)・可視性タイムアウト・リトライ・デッドレターはM3-2(Job Queue)のスコープで、
  この実装を土台に拡張する(作り直さない)。
- `payload`/`result`はJSON文字列としてTEXTカラムに保存し、アプリケーション側で
  `json.dumps`/`json.loads`する(ORMは使わない)。
- 既存レビュー処理(M2-1、`cli/worker_pool.py`)は複数のワーカースレッドから並行に呼ばれるため、
  `store/sqlite.py`(`SqliteStateStore`)と同じく`threading.RLock`で全メソッドの本体を
  直列化する(`update_status`が内部で`get`を呼ぶため`RLock`を使う)。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import InvalidJobTransitionError, JobError, JobNotFoundError
from .protocol import Job, JobStatus, JobType

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
_CREATE_INDEX_STATUS_SQL = "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"
# ADR-0016の見出しは「job_type/status双方にインデックスを張ったテーブル」としているため、
# 決定済みのSQL例(status用のみ)に加えてjob_type用も張る
_CREATE_INDEX_JOB_TYPE_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_jobs_job_type ON jobs(job_type)"
)

# 許可される状態遷移(`docs/adr/0016-job-abstraction.md`)。DONE/FAILEDは終端状態で
# 遷移先を持たない
_ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.RUNNING}),
    JobStatus.RUNNING: frozenset(
        {JobStatus.DONE, JobStatus.FAILED, JobStatus.WAITING_HUMAN}
    ),
    JobStatus.WAITING_HUMAN: frozenset({JobStatus.RUNNING, JobStatus.FAILED}),
    JobStatus.DONE: frozenset(),
    JobStatus.FAILED: frozenset(),
}


class SqliteJobRepository:
    """SQLite(標準ライブラリ`sqlite3`)経由で`JobRepository`を実装する。"""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        # State Store(store/sqlite.py)と同じ理由でcheck_same_thread=Falseにする
        # (接続を作ったスレッド以外からの呼び出しを許容する必要があるため)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.execute(_CREATE_INDEX_STATUS_SQL)
        self._conn.execute(_CREATE_INDEX_JOB_TYPE_SQL)
        self._conn.commit()
        # 全メソッドの本体を直列化するロック(モジュールdocstring参照)。update_statusが
        # 内部でgetを呼ぶ(再入する)ためRLockを使う
        self._lock = threading.RLock()

    def enqueue(self, job_type: JobType, payload: dict[str, Any]) -> Job:
        with self._lock:
            job_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            try:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO jobs "
                        "(id, job_type, status, payload, result, error, "
                        "created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)",
                        (
                            job_id,
                            job_type.value,
                            JobStatus.PENDING.value,
                            json.dumps(payload, ensure_ascii=False),
                            now.isoformat(),
                            now.isoformat(),
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
            )

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT id, job_type, status, payload, result, error, "
                    "created_at, updated_at FROM jobs WHERE id = ?",
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
                    "SELECT id, job_type, status, payload, result, error, "
                    "created_at, updated_at FROM jobs WHERE status = ? "
                    "ORDER BY created_at",
                    (status.value,),
                ).fetchall()
            except sqlite3.Error as exc:
                raise JobError(f"Jobの一覧取得に失敗しました: {exc}") from exc
            return [_row_to_job(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_job(row: tuple) -> Job:
    job_id, job_type, status, payload, result, error, created_at, updated_at = row
    return Job(
        id=job_id,
        job_type=JobType(job_type),
        status=JobStatus(status),
        payload=json.loads(payload),
        result=json.loads(result) if result is not None else None,
        error=error,
        created_at=datetime.fromisoformat(created_at),
        updated_at=datetime.fromisoformat(updated_at),
    )


__all__ = ["SqliteJobRepository"]
