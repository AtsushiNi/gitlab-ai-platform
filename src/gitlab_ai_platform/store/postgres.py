"""`StateStore` を満たすPostgreSQL実装。

方針(M3-5 [#95](https://github.com/AtsushiNi/gitlab-ai-platform/issues/95)、
`docs/adr/0020-state-store-postgresql.md`):

- ドライバは`psycopg`(psycopg3)の`binary`extra(`pyproject.toml`の`postgres`extra)を使う。
  Windowsのオフライン制約下でもコンパイル済みwheelで導入できる(ADR-0020参照)
- スキーマは`sqlite.py`とほぼ同一のDDLをそのまま使う(ADR-0003が「ANSI標準のDDLに近い形」に
  とどめていたため。プレースホルダ構文の違い(`?` → `%s`)以外に差分はない)
- `reviewed_at`はSQLite実装と同じくTEXT(ISO 8601文字列)として保存する。PostgreSQL固有の
  `TIMESTAMPTZ`型は使わない(ADR-0020「却下した選択肢」: 両実装で入出力契約を完全に揃えるため)
- 二重レビュー防止の一意制約違反は`psycopg.errors.UniqueViolation`を`DuplicateReviewError`に
  変換して表現する(SQLite実装の`sqlite3.IntegrityError`+メッセージ判定と同じ契約)
- 並行アクセスは`sqlite.py`と同じく単一コネクション+`threading.RLock`で直列化する
  (ADR-0020: psycopg3のコネクションはスレッドセーフではなく、現状の`max_parallel`規模では
  コネクションプールを導入するメリットが小さいため)
- コネクションは`autocommit=True`で開く。psycopg3はデフォルト(`autocommit=False`)だと
  エラー発生後のトランザクションが中断状態(aborted)のまま残り、明示的な`rollback()`を
  呼ばない限り同じコネクション上の後続クエリがすべて失敗する。単一コネクションを
  `RLock`越しに使い回す本実装ではこの状態管理が煩雑になるため、各文が個別に即時コミットされる
  `autocommit=True`を選び、SQLite実装の`with self._conn:`(文単位のトランザクション)と
  同等の粒度に揃えた
"""

from __future__ import annotations

import threading
from datetime import datetime

import psycopg
from psycopg import errors as pg_errors

from .errors import DuplicateReviewError, RecordNotFoundError, StateStoreError
from .types import ReviewRecord, ReviewStatus

# sqlite.pyの_CREATE_TABLE_SQLと意図的に同一のDDL(ADR-0020「スキーマはSQLite実装と
# ほぼ同一のDDLをそのまま使う」)。PostgreSQLでもTEXT/INTEGER/複合PRIMARY KEYは
# そのまま使えるため、方言差の吸収は不要だった
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


class PostgresStateStore:
    """PostgreSQL(`psycopg`)経由で`StateStore`を実装する。"""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        dbname: str,
        user: str,
        password: str = "",
    ) -> None:
        # モジュールdocstring参照: autocommit=Trueで、エラー後のトランザクション中断状態を
        # 気にせず済むようにする
        self._conn = psycopg.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
            autocommit=True,
        )
        self._conn.execute(_CREATE_TABLE_SQL)
        # 全メソッドの本体を直列化するロック(モジュールdocstring参照)。update_statusが
        # 内部でfindを呼ぶ(再入する)ためRLockを使う(sqlite.pyと同じ理由)
        self._lock = threading.RLock()

    def find(self, project: str, mr_iid: int, commit_sha: str) -> ReviewRecord | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT project, mr_iid, commit_sha, status, reviewed_at, result_path "
                    "FROM review_records WHERE project = %s AND mr_iid = %s "
                    "AND commit_sha = %s",
                    (project, mr_iid, commit_sha),
                ).fetchone()
            except psycopg.Error as exc:
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
                self._conn.execute(
                    "INSERT INTO review_records "
                    "(project, mr_iid, commit_sha, status, reviewed_at, result_path) "
                    "VALUES (%s, %s, %s, %s, NULL, NULL)",
                    (project, mr_iid, commit_sha, status.value),
                )
            except pg_errors.UniqueViolation as exc:
                # PRIMARY KEY(=一意制約)違反のみをDuplicateReviewErrorに変換する。psycopg3は
                # 制約違反の種別を専用の例外クラスで表現するため、SQLite実装のような
                # メッセージ文字列の部分一致判定は不要(ADR-0020)
                raise DuplicateReviewError(
                    f"({project!r}, {mr_iid!r}, {commit_sha!r}) は既にレビュー記録が"
                    "存在します"
                ) from exc
            except psycopg.Error as exc:
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
                # reviewed_at/result_pathを指定しなかった呼び出し(None)は「変更しない」を
                # 意味する。COALESCEで既存値を維持し、指定した場合だけ新しい値で上書きする
                # (sqlite.pyと同じ方針)
                cursor = self._conn.execute(
                    "UPDATE review_records SET status = %s, "
                    "reviewed_at = COALESCE(%s, reviewed_at), "
                    "result_path = COALESCE(%s, result_path) "
                    "WHERE project = %s AND mr_iid = %s AND commit_sha = %s",
                    (
                        status.value,
                        reviewed_at.isoformat() if reviewed_at is not None else None,
                        result_path,
                        project,
                        mr_iid,
                        commit_sha,
                    ),
                )
            except psycopg.Error as exc:
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


__all__ = ["PostgresStateStore"]
