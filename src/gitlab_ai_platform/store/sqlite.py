"""`StateStore` を満たすSQLite実装。

方針(M1-4 [#32](https://github.com/AtsushiNi/gitlab-ai-platform/issues/32)、
`docs/adr/0003-state-store-interface.md`):

- 標準ライブラリの`sqlite3`のみを使う(ADR-0001が許可する外部依存は`requests`/`pytest`のみで、
  SQLiteアクセスに追加ライブラリは不要)。
- `(project, mr_iid, commit_sha)`をPRIMARY KEYとし、二重レビュー防止の一意制約をDBスキーマで
  機構として保証する。`create`はこの制約違反(`sqlite3.IntegrityError`)を`DuplicateReviewError`に
  変換して送出する。
- `reviewed_at`はSQLite側にTEXT(ISO 8601文字列)で保存し、呼び出し側には`datetime`として返す。
- 並列レビュー実行(M2-1 [#80](https://github.com/AtsushiNi/gitlab-ai-platform/issues/80)、
  `docs/adr/0015-parallel-review-execution.md`)以降、複数のワーカースレッドが同じ`_conn`
  (単一のコネクション)を共有して同時に呼び出す。SQLite自体がスレッドセーフにビルドされて
  いるかはプラットフォーム依存で当てにできないため、`threading.RLock`で全メソッドの本体を
  明示的に直列化する(`sqlite3.OperationalError: database is locked`のような非決定的な
  失敗をSQLiteのビルド設定に依存させないため)。`update_status`が内部で`find`を呼ぶため
  (再入)、単純な`Lock`ではなく`RLock`を使う。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .errors import DuplicateReviewError, RecordNotFoundError, StateStoreError
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
        # ADR-0003は「複数プロセス・複数スレッドからの同時実行下でも二重起票が起きない」ことを
        # 前提にしている。check_same_thread=Falseにしないと、接続を作ったスレッド以外からの
        # 呼び出しがProgrammingErrorで落ちてしまい、この前提を満たせない
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.commit()
        # 全メソッドの本体を直列化するロック(モジュールdocstring参照)。update_statusが
        # 内部でfindを呼ぶ(再入する)ためRLockを使う
        self._lock = threading.RLock()

    def find(self, project: str, mr_iid: int, commit_sha: str) -> ReviewRecord | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT project, mr_iid, commit_sha, status, reviewed_at, result_path "
                    "FROM review_records WHERE project = ? AND mr_iid = ? "
                    "AND commit_sha = ?",
                    (project, mr_iid, commit_sha),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StateStoreError(
                    f"レビュー記録の照会に失敗しました: {exc}"
                ) from exc
            return _row_to_record(row) if row is not None else None

    def create(
        self,
        project: str,
        mr_iid: int,
        commit_sha: str,
        *,
        status: ReviewStatus = ReviewStatus.PENDING,
    ) -> ReviewRecord:
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO review_records "
                        "(project, mr_iid, commit_sha, status, reviewed_at, result_path) "
                        "VALUES (?, ?, ?, ?, NULL, NULL)",
                        (project, mr_iid, commit_sha, status.value),
                    )
            except sqlite3.IntegrityError as exc:
                # PRIMARY KEY(=一意制約)違反だけをDuplicateReviewErrorに変換する。NOT NULL
                # 違反等(呼び出し側が不正な引数を渡したバグ)まで二重レビューとして
                # 握りつぶさないため
                if "UNIQUE constraint failed" not in str(exc):
                    raise StateStoreError(
                        f"レビュー記録の作成に失敗しました: {exc}"
                    ) from exc
                raise DuplicateReviewError(
                    f"({project!r}, {mr_iid!r}, {commit_sha!r}) は既にレビュー記録が"
                    "存在します"
                ) from exc
            except sqlite3.Error as exc:
                raise StateStoreError(
                    f"レビュー記録の作成に失敗しました: {exc}"
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
        with self._lock:
            try:
                with self._conn:
                    # reviewed_at/result_pathを指定しなかった呼び出し(None)は「変更しない」を
                    # 意味する。COALESCEで既存値を維持し、指定した場合だけ新しい値で上書きする
                    cursor = self._conn.execute(
                        "UPDATE review_records SET status = ?, "
                        "reviewed_at = COALESCE(?, reviewed_at), "
                        "result_path = COALESCE(?, result_path) "
                        "WHERE project = ? AND mr_iid = ? AND commit_sha = ?",
                        (
                            status.value,
                            reviewed_at.isoformat()
                            if reviewed_at is not None
                            else None,
                            result_path,
                            project,
                            mr_iid,
                            commit_sha,
                        ),
                    )
            except sqlite3.Error as exc:
                raise StateStoreError(
                    f"レビュー記録の更新に失敗しました: {exc}"
                ) from exc

            if cursor.rowcount == 0:
                raise RecordNotFoundError(
                    f"({project!r}, {mr_iid!r}, {commit_sha!r}) の"
                    "レビュー記録が見つかりません"
                )

            # RLockのため再入可能: 他スレッドがこの間に割り込むことはない(直前のUPDATEと
            # 同じ状態をそのまま読み返せる)
            return self.find(project, mr_iid, commit_sha)  # type: ignore[return-value]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_record(row: tuple) -> ReviewRecord:
    project, mr_iid, commit_sha, status, reviewed_at, result_path = row
    return ReviewRecord(
        project=project,
        mr_iid=mr_iid,
        commit_sha=commit_sha,
        status=ReviewStatus(status),
        reviewed_at=datetime.fromisoformat(reviewed_at)
        if reviewed_at is not None
        else None,
        result_path=result_path,
    )


__all__ = ["SqliteStateStore"]
