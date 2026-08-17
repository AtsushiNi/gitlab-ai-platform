"""`(project, mr_iid, commit_sha)`単位でレビュー状態を記録するState Store(`docs/architecture.md`)。"""

from __future__ import annotations

from .errors import DuplicateReviewError, RecordNotFoundError, StateStoreError
from .factory import build_state_store
from .protocol import StateStore
from .sqlite import SqliteStateStore
from .types import ReviewRecord, ReviewStatus

# PostgreSQL実装(`PostgresStateStore`、M3-5)はここではimportしない。`psycopg`は`postgres`
# extraの任意依存であり(docs/adr/0021-state-store-postgresql.md)、ここでimportすると
# SQLiteのみを使う環境で`psycopg`未インストール時にパッケージ全体のimportが失敗してしまう。
# 必要な場合は`from gitlab_ai_platform.store.postgres import PostgresStateStore`で直接importするか、
# `build_state_store(config)`(内部で遅延import)を使う

__all__ = [
    "DuplicateReviewError",
    "RecordNotFoundError",
    "ReviewRecord",
    "ReviewStatus",
    "SqliteStateStore",
    "StateStore",
    "StateStoreError",
    "build_state_store",
]
