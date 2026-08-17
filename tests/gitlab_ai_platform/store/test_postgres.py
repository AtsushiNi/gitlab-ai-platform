"""`PostgresStateStore`(M3-5、docs/adr/0021-state-store-postgresql.md)の契約テスト。

CLAUDE.mdの「外部依存(GitLab API等)に触れるテストはモック/フィクスチャを使い、実サービスへは
繋がない」という方針の例外として、本ファイルは実PostgreSQLサーバーへの接続を必要とする
(ADR-0021「テスト方針」)。`psycopg`が未インストール、または接続先(環境変数
`GITLAB_AI_PLATFORM_TEST_POSTGRES_*`、既定は`localhost:5432`)に接続できない場合は
モジュール全体・各テストをスキップする。CI(GitHub Actions、PostgreSQLサービスコンテナ無し)では
常にスキップされ、`pytest`全体の成功/失敗には影響しない。

ローカルで実行する場合の例(Docker):

```sh
docker run --rm -d --name gitlab-ai-platform-test-postgres \\
    -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
pytest tests/gitlab_ai_platform/store/test_postgres.py
```
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime

import pytest

psycopg = pytest.importorskip("psycopg")

from gitlab_ai_platform.store import (  # noqa: E402
    DuplicateReviewError,
    RecordNotFoundError,
    ReviewStatus,
    StateStoreError,
)
from gitlab_ai_platform.store.postgres import PostgresStateStore  # noqa: E402

_ENV_HOST = "GITLAB_AI_PLATFORM_TEST_POSTGRES_HOST"
_ENV_PORT = "GITLAB_AI_PLATFORM_TEST_POSTGRES_PORT"
_ENV_DBNAME = "GITLAB_AI_PLATFORM_TEST_POSTGRES_DBNAME"
_ENV_USER = "GITLAB_AI_PLATFORM_TEST_POSTGRES_USER"
_ENV_PASSWORD = "GITLAB_AI_PLATFORM_TEST_POSTGRES_PASSWORD"


def _connection_kwargs() -> dict:
    return {
        "host": os.environ.get(_ENV_HOST, "localhost"),
        "port": int(os.environ.get(_ENV_PORT, "5432")),
        "dbname": os.environ.get(_ENV_DBNAME, "postgres"),
        "user": os.environ.get(_ENV_USER, "postgres"),
        "password": os.environ.get(_ENV_PASSWORD, "postgres"),
    }


@pytest.fixture
def store():
    try:
        s = PostgresStateStore(**_connection_kwargs())
    except psycopg.Error as exc:
        pytest.skip(f"PostgreSQLサーバーへ接続できないためスキップします: {exc}")
        return

    # 実サーバーは永続化されるため、SQLiteの":memory:"のような自然な隔離が得られない。
    # 前回実行分の残留データをクリアしてから各テストを始める
    s._conn.execute("DELETE FROM review_records")
    yield s
    s.close()


def test_find_returns_none_for_unknown_record(store):
    assert store.find("group/project", 1, "abc123") is None


def test_create_then_find_returns_pending_record(store):
    created = store.create("group/project", 1, "abc123")

    assert created.status == ReviewStatus.PENDING
    assert created.reviewed_at is None
    assert created.result_path is None

    found = store.find("group/project", 1, "abc123")
    assert found == created


def test_create_accepts_explicit_status(store):
    created = store.create("group/project", 1, "abc123", status=ReviewStatus.RUNNING)

    assert created.status == ReviewStatus.RUNNING


def test_create_rejects_duplicate_project_mr_commit(store):
    store.create("group/project", 1, "abc123")

    with pytest.raises(DuplicateReviewError):
        store.create("group/project", 1, "abc123")


def test_create_allows_same_mr_with_different_commit_sha(store):
    # 同一MRでも新しいcommit_shaなら別レコードとして扱える(再レビューの検出、
    # docs/architecture.mdのデータフロー手順12)
    store.create("group/project", 1, "abc123")
    second = store.create("group/project", 1, "def456")

    assert second.commit_sha == "def456"


def test_update_status_persists_reviewed_at_and_result_path(store):
    store.create("group/project", 1, "abc123")
    reviewed_at = datetime(2026, 8, 13, 12, 0, 0)

    updated = store.update_status(
        "group/project",
        1,
        "abc123",
        ReviewStatus.DONE,
        reviewed_at=reviewed_at,
        result_path="reviews/group/project/1/abc123",
    )

    assert updated.status == ReviewStatus.DONE
    assert updated.reviewed_at == reviewed_at
    assert updated.result_path == "reviews/group/project/1/abc123"

    found = store.find("group/project", 1, "abc123")
    assert found == updated


def test_update_status_raises_for_unknown_record(store):
    with pytest.raises(RecordNotFoundError):
        store.update_status("group/project", 1, "abc123", ReviewStatus.DONE)


def test_records_are_isolated_per_project_mr_commit(store):
    store.create("group/project-a", 1, "abc123")

    assert store.find("group/project-b", 1, "abc123") is None
    assert store.find("group/project-a", 2, "abc123") is None
    assert store.find("group/project-a", 1, "def456") is None


def test_update_status_without_reviewed_at_or_result_path_preserves_existing_values(
    store,
):
    store.create("group/project", 1, "abc123")
    reviewed_at = datetime(2026, 8, 13, 12, 0, 0)
    store.update_status(
        "group/project",
        1,
        "abc123",
        ReviewStatus.DONE,
        reviewed_at=reviewed_at,
        result_path="reviews/group/project/1/abc123",
    )

    updated = store.update_status("group/project", 1, "abc123", ReviewStatus.FAILED)

    assert updated.status == ReviewStatus.FAILED
    assert updated.reviewed_at == reviewed_at
    assert updated.result_path == "reviews/group/project/1/abc123"


def test_create_does_not_mask_not_null_violation_as_duplicate_review(store):
    # NOT NULL制約違反(呼び出し側のバグ)は、二重レビュー(一意制約違反)と
    # 区別してそのまま送出されること(ADR-0021: psycopg3は制約違反の種別を専用の
    # 例外クラスで表現するため、UniqueViolationだけを捕まえれば区別できる)
    with pytest.raises(psycopg.errors.NotNullViolation) as excinfo:
        store._conn.execute(
            "INSERT INTO review_records "
            "(project, mr_iid, commit_sha, status, reviewed_at, result_path) "
            "VALUES (NULL, 1, 'abc123', 'PENDING', NULL, NULL)"
        )
    assert not isinstance(excinfo.value, DuplicateReviewError)


def test_store_is_usable_from_a_different_thread_than_the_constructor(store):
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            store.create("group/project", 99, "thread-sha")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert errors == []
    assert store.find("group/project", 99, "thread-sha") is not None


def test_store_handles_many_concurrent_writers_without_errors(store):
    # M2-1(並列レビュー実行、#80)由来のシナリオをPostgreSQL実装でも回帰確認する
    worker_count = 20
    errors: list[BaseException] = []
    errors_lock = threading.Lock()
    # 複数回のテスト実行(実サーバーが永続化される)でも衝突しないよう、実行ごとに
    # ユニークなproject名を使う
    project = f"group/project-{uuid.uuid4().hex[:12]}"

    def worker(n: int) -> None:
        try:
            store.create(project, n, "concurrent-sha")
            store.update_status(
                project,
                n,
                "concurrent-sha",
                ReviewStatus.DONE,
                reviewed_at=datetime(2026, 8, 13, 12, 0, 0),
                result_path=f"reviews/{project}/{n}/concurrent-sha",
            )
        except BaseException as exc:  # noqa: BLE001
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    for n in range(worker_count):
        record = store.find(project, n, "concurrent-sha")
        assert record is not None
        assert record.status == ReviewStatus.DONE
        assert record.result_path == f"reviews/{project}/{n}/concurrent-sha"


def test_find_wraps_postgres_errors_as_state_store_error(store):
    store.close()

    with pytest.raises(StateStoreError):
        store.find("group/project", 1, "abc123")


def test_create_wraps_postgres_errors_as_state_store_error(store):
    store.close()

    with pytest.raises(StateStoreError):
        store.create("group/project", 1, "abc123")


def test_update_status_wraps_postgres_errors_as_state_store_error(store):
    store.create("group/project", 1, "abc123")
    store.close()

    with pytest.raises(StateStoreError):
        store.update_status("group/project", 1, "abc123", ReviewStatus.DONE)
