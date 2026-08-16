import sqlite3
import threading
from datetime import datetime

import pytest

from gitlab_ai_platform.store import (
    DuplicateReviewError,
    RecordNotFoundError,
    ReviewStatus,
    SqliteStateStore,
    StateStoreError,
)


@pytest.fixture
def store():
    # 実DBには繋がず、テストごとに独立したインメモリDBを使う(CLAUDE.mdのテスト方針)。
    s = SqliteStateStore(":memory:")
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
    # reviewed_at/result_pathを省略した更新(例: DONE→FAILEDへの状態遷移のみ)が、
    # 既に記録済みの値を意図せずNULLへ巻き戻さないこと
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
    # NOT NULL制約違反(呼び出し側のバグ)は、二重レビュー(PRIMARY KEY違反)と
    # 区別してそのまま送出されること
    with pytest.raises(sqlite3.IntegrityError) as excinfo:
        store._conn.execute(
            "INSERT INTO review_records "
            "(project, mr_iid, commit_sha, status, reviewed_at, result_path) "
            "VALUES (NULL, 1, 'abc123', 'PENDING', NULL, NULL)"
        )
    assert not isinstance(excinfo.value, DuplicateReviewError)


def test_store_is_usable_from_a_different_thread_than_the_constructor(store):
    # ADR-0003は複数プロセス・複数スレッドからの同時実行下での安全性を前提にしている
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


def test_find_wraps_sqlite_errors_as_state_store_error(store):
    # sqlite3の低レベル例外(接続が閉じられている等)がそのまま伝播すると、
    # 呼び出し側(Poller等)がStateStoreErrorだけをcatchする契約をすり抜けてしまう回帰テスト
    store.close()

    with pytest.raises(StateStoreError):
        store.find("group/project", 1, "abc123")


def test_create_wraps_sqlite_errors_as_state_store_error(store):
    store.close()

    with pytest.raises(StateStoreError):
        store.create("group/project", 1, "abc123")


def test_update_status_wraps_sqlite_errors_as_state_store_error(store):
    store.create("group/project", 1, "abc123")
    store.close()

    with pytest.raises(StateStoreError):
        store.update_status("group/project", 1, "abc123", ReviewStatus.DONE)
