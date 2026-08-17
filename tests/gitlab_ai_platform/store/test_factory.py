"""`build_state_store`(M3-5、docs/adr/0021-state-store-postgresql.md)のテスト。

`store_backend = "postgresql"`のケースは、実際に接続を試みる前に
`PostgresStateStore.__init__`が呼ばれた時点で失敗しうる(実PostgreSQLサーバーが必要)ため、
`PostgresStateStore`自体をフェイクに差し替えて「正しい引数で構築されたか」だけを検証する
(ADR-0021「テスト方針」)。実接続は`test_postgres.py`側で扱う。
"""

from __future__ import annotations

from gitlab_ai_platform.config import Config
from gitlab_ai_platform.store import SqliteStateStore, build_state_store


def _config(**overrides) -> Config:
    kwargs = dict(
        gitlab_url="https://gitlab.example.com",
        gitlab_token="secret-token",
        projects=["group/project-a"],
        poll_interval_seconds=60,
        max_parallel=5,
        review_label="レビュー待ち",
        workspace_root="workspace",
        workspace_max_disk_mb=5000,
        runner_log_dir="logs/runner",
        runner_timeout_seconds=1800,
        reviews_root="reviews",
        state_db_path=":memory:",
        store_backend="sqlite",
        store_postgres_host="localhost",
        store_postgres_port=5432,
        store_postgres_dbname="gitlab_ai_platform",
        store_postgres_user="gitlab_ai_platform",
        store_postgres_password="",
        job_db_path=":memory:",
        webhook_enabled=False,
        webhook_host="127.0.0.1",
        webhook_port=8088,
        webhook_path="/webhook",
        webhook_secret_token="",
        api_host="127.0.0.1",
        api_port=8090,
        api_token="",
    )
    kwargs.update(overrides)
    return Config.from_raw(**kwargs)


def test_build_state_store_returns_sqlite_by_default():
    config = _config(store_backend="sqlite")

    store = build_state_store(config)
    try:
        assert isinstance(store, SqliteStateStore)
    finally:
        store.close()


def test_build_state_store_returns_postgres_when_configured(monkeypatch):
    captured: dict = {}

    class _FakePostgresStateStore:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    # PostgresStateStoreはfactory.py内で遅延importされるため、実体である
    # gitlab_ai_platform.store.postgres モジュール側のクラスをフェイクに差し替える
    monkeypatch.setattr(
        "gitlab_ai_platform.store.postgres.PostgresStateStore",
        _FakePostgresStateStore,
    )

    config = _config(
        store_backend="postgresql",
        store_postgres_host="db.example.com",
        store_postgres_port=5433,
        store_postgres_dbname="custom-db",
        store_postgres_user="custom-user",
        store_postgres_password="pw",
    )

    store = build_state_store(config)

    assert isinstance(store, _FakePostgresStateStore)
    assert captured == {
        "host": "db.example.com",
        "port": 5433,
        "dbname": "custom-db",
        "user": "custom-user",
        "password": "pw",
    }
