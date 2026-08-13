from datetime import datetime

import pytest

from gitlab_ai_platform.store import (
    DuplicateReviewError,
    RecordNotFoundError,
    ReviewStatus,
    SqliteStateStore,
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
