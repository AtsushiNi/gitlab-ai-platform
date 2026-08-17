"""`Config`から`StateStore`の具象実装を組み立てるファクトリ。

方針(M3-5 [#95](https://github.com/AtsushiNi/gitlab-ai-platform/issues/95)、
`docs/adr/0020-state-store-postgresql.md`「SQLite実装との共存」):

- 呼び出し側(`cli/single_run.py`・`cli/watch.py`)は`SqliteStateStore`/`PostgresStateStore`を
  直接構築せず、この`build_state_store(config)`を経由することで具象クラスの選択を
  configに閉じ込める。呼び出し側が扱う型は`StateStore`(Protocol)のみ
- `PostgresStateStore`(および依存する`psycopg`)のimportは関数内に留める(遅延import)。
  `store_backend = "sqlite"`(Windows運用の既定)の場合、`psycopg`が未インストールでも
  `gitlab_ai_platform.store`パッケージ全体のimportが失敗しないようにするため
  (ADR-0020: `psycopg`はbase依存ではなく`postgres`extra)
"""

from __future__ import annotations

from ..config import Config
from .protocol import StateStore
from .sqlite import SqliteStateStore


def build_state_store(config: Config) -> StateStore:
    """`config.store_backend`に応じた`StateStore`実装を構築する。

    `store_backend`が`"postgresql"`の場合、`psycopg`(`pip install ".[postgres]"`で導入)が
    必要になる。未インストールの場合は`ImportError`がそのまま送出される。
    """
    if config.store_backend == "postgresql":
        # 遅延import(モジュールdocstring参照)
        from .postgres import PostgresStateStore

        return PostgresStateStore(
            host=config.store_postgres_host,
            port=config.store_postgres_port,
            dbname=config.store_postgres_dbname,
            user=config.store_postgres_user,
            password=config.store_postgres_password,
        )

    return SqliteStateStore(config.state_db_path)


__all__ = ["build_state_store"]
