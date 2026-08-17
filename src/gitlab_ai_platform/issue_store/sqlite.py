"""`IssueTicketStore` を満たすSQLite実装。

方針(M4-1 [#107](https://github.com/AtsushiNi/gitlab-ai-platform/issues/107)、
`docs/adr/0024-issue-poller-dedup.md`):

- 標準ライブラリの`sqlite3`のみを使う(ADR-0001が許可する外部依存は`requests`/`pytest`のみ)。
- `(project, issue_iid)`をPRIMARY KEYとし、二重投入防止の一意制約をDBスキーマで機構として
  保証する。`create`はこの制約違反(`sqlite3.IntegrityError`)を`DuplicateIssueTicketError`に
  変換して送出する(`store.sqlite.SqliteStateStore`と同じ設計)。
- `ticketed_at`はSQLite側にTEXT(ISO 8601文字列)で保存し、呼び出し側には`datetime`として返す。
- 複数Poller稼働時の同時実行を前提に、`threading.Lock`で全メソッドの本体を直列化する
  (`SqliteStateStore`と同じ理由。ただしこのストアは`find`から呼ばれるメソッドがないため
  `RLock`は不要)。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .errors import DuplicateIssueTicketError, IssueTicketStoreError
from .types import IssueTicketRecord

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS issue_tickets (
    project TEXT NOT NULL,
    issue_iid INTEGER NOT NULL,
    ticketed_at TEXT NOT NULL,
    PRIMARY KEY (project, issue_iid)
)
"""


class SqliteIssueTicketStore:
    """SQLite(標準ライブラリ`sqlite3`)経由で`IssueTicketStore`を実装する。"""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        # SqliteStateStoreと同じ理由でcheck_same_thread=Falseにする(接続を作った
        # スレッド以外(複数Poller/複数ワーカースレッド)からの呼び出しを許可する)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.commit()
        self._lock = threading.Lock()

    def find(self, project: str, issue_iid: int) -> IssueTicketRecord | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT project, issue_iid, ticketed_at FROM issue_tickets "
                    "WHERE project = ? AND issue_iid = ?",
                    (project, issue_iid),
                ).fetchone()
            except sqlite3.Error as exc:
                raise IssueTicketStoreError(
                    f"Issue起票記録の照会に失敗しました: {exc}"
                ) from exc
            return _row_to_record(row) if row is not None else None

    def create(self, project: str, issue_iid: int) -> IssueTicketRecord:
        ticketed_at = datetime.now()
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO issue_tickets (project, issue_iid, ticketed_at) "
                        "VALUES (?, ?, ?)",
                        (project, issue_iid, ticketed_at.isoformat()),
                    )
            except sqlite3.IntegrityError as exc:
                # PRIMARY KEY(=一意制約)違反だけをDuplicateIssueTicketErrorに変換する。
                # NOT NULL違反等(呼び出し側の不正な引数)まで二重投入として握りつぶさないため
                if "UNIQUE constraint failed" not in str(exc):
                    raise IssueTicketStoreError(
                        f"Issue起票記録の作成に失敗しました: {exc}"
                    ) from exc
                raise DuplicateIssueTicketError(
                    f"({project!r}, {issue_iid!r}) は既に起票記録が存在します"
                ) from exc
            except sqlite3.Error as exc:
                raise IssueTicketStoreError(
                    f"Issue起票記録の作成に失敗しました: {exc}"
                ) from exc

            return IssueTicketRecord(
                project=project, issue_iid=issue_iid, ticketed_at=ticketed_at
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_record(row: tuple) -> IssueTicketRecord:
    project, issue_iid, ticketed_at = row
    return IssueTicketRecord(
        project=project,
        issue_iid=issue_iid,
        ticketed_at=datetime.fromisoformat(ticketed_at),
    )


__all__ = ["SqliteIssueTicketStore"]
