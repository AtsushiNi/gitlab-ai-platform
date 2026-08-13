"""`StateStore` を満たすSQLite実装。

方針(M1-4 [#32](https://github.com/AtsushiNi/gitlab-ai-platform/issues/32)、
`docs/adr/0003-state-store-interface.md`):

- 標準ライブラリの`sqlite3`のみを使う(ADR-0001が許可する外部依存は`requests`/`pytest`のみで、
  SQLiteアクセスに追加ライブラリは不要)。
- `(project, mr_iid, commit_sha)`をPRIMARY KEYとし、二重レビュー防止の一意制約をDBスキーマで
  機構として保証する。`create`はこの制約違反(`sqlite3.IntegrityError`)を`DuplicateReviewError`に
  変換して送出する。
- `reviewed_at`はSQLite側にTEXT(ISO 8601文字列)で保存し、呼び出し側には`datetime`として返す。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .errors import DuplicateReviewError, RecordNotFoundError
from .types import ReviewRecord, ReviewStatus

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS review_records (
    project TEXT NOT NULL,
    mr_iid INTEGER NOT NULL,
    commit_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    reviewed_at TEXT,
    result_path TEXT,
    PRIMARY KEY (project, mr_iid, commit_sha)
)
"""


class SqliteStateStore:
    """SQLite(標準ライブラリ`sqlite3`)経由で`StateStore`を実装する。"""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.commit()

    def find(self, project: str, mr_iid: int, commit_sha: str) -> ReviewRecord | None:
        row = self._conn.execute(
            "SELECT project, mr_iid, commit_sha, status, reviewed_at, result_path "
            "FROM review_records WHERE project = ? AND mr_iid = ? AND commit_sha = ?",
            (project, mr_iid, commit_sha),
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def create(
        self,
        project: str,
        mr_iid: int,
        commit_sha: str,
        *,
        status: ReviewStatus = ReviewStatus.PENDING,
    ) -> ReviewRecord:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO review_records "
                    "(project, mr_iid, commit_sha, status, reviewed_at, result_path) "
                    "VALUES (?, ?, ?, ?, NULL, NULL)",
                    (project, mr_iid, commit_sha, status.value),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateReviewError(
                f"({project!r}, {mr_iid!r}, {commit_sha!r}) は既にレビュー記録が存在します"
            ) from exc

        return ReviewRecord(
            project=project, mr_iid=mr_iid, commit_sha=commit_sha, status=status
        )

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
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE review_records SET status = ?, reviewed_at = ?, result_path = ? "
                "WHERE project = ? AND mr_iid = ? AND commit_sha = ?",
                (
                    status.value,
                    reviewed_at.isoformat() if reviewed_at is not None else None,
                    result_path,
                    project,
                    mr_iid,
                    commit_sha,
                ),
            )
            if cursor.rowcount == 0:
                raise RecordNotFoundError(
                    f"({project!r}, {mr_iid!r}, {commit_sha!r}) のレビュー記録が見つかりません"
                )

        return ReviewRecord(
            project=project,
            mr_iid=mr_iid,
            commit_sha=commit_sha,
            status=status,
            reviewed_at=reviewed_at,
            result_path=result_path,
        )

    def close(self) -> None:
        self._conn.close()


def _row_to_record(row: tuple) -> ReviewRecord:
    project, mr_iid, commit_sha, status, reviewed_at, result_path = row
    return ReviewRecord(
        project=project,
        mr_iid=mr_iid,
        commit_sha=commit_sha,
        status=ReviewStatus(status),
        reviewed_at=datetime.fromisoformat(reviewed_at) if reviewed_at is not None else None,
        result_path=result_path,
    )


__all__ = ["SqliteStateStore"]
