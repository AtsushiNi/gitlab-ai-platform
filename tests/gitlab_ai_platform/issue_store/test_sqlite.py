import sqlite3
import threading

import pytest

from gitlab_ai_platform.issue_store import (
    DuplicateIssueTicketError,
    IssueTicketStoreError,
    SqliteIssueTicketStore,
)


@pytest.fixture
def store():
    # 実DBには繋がず、テストごとに独立したインメモリDBを使う(CLAUDE.mdのテスト方針)。
    s = SqliteIssueTicketStore(":memory:")
    yield s
    s.close()


def test_find_returns_none_for_unknown_record(store):
    assert store.find("group/project", 1) is None


def test_create_then_find_returns_record(store):
    created = store.create("group/project", 1)

    found = store.find("group/project", 1)
    assert found == created
    assert found.project == "group/project"
    assert found.issue_iid == 1
    assert found.ticketed_at is not None


def test_create_rejects_duplicate_project_issue_iid(store):
    store.create("group/project", 1)

    with pytest.raises(DuplicateIssueTicketError):
        store.create("group/project", 1)


def test_records_are_isolated_per_project_and_issue_iid(store):
    store.create("group/project-a", 1)

    assert store.find("group/project-b", 1) is None
    assert store.find("group/project-a", 2) is None


def test_create_does_not_mask_not_null_violation_as_duplicate(store):
    # NOT NULL制約違反(呼び出し側のバグ)は、二重投入(PRIMARY KEY違反)と
    # 区別してそのまま送出されること
    with pytest.raises(sqlite3.IntegrityError) as excinfo:
        store._conn.execute(
            "INSERT INTO issue_tickets (project, issue_iid, ticketed_at) "
            "VALUES (NULL, 1, '2026-08-17T09:00:00')"
        )
    assert not isinstance(excinfo.value, DuplicateIssueTicketError)


def test_store_is_usable_from_a_different_thread_than_the_constructor(store):
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            store.create("group/project", 99)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert errors == []
    assert store.find("group/project", 99) is not None


def test_store_handles_many_concurrent_writers_without_errors(store):
    # 複数Poller稼働時を想定し、複数スレッドが同じインスタンスへ同時にcreateを呼んでも
    # "database is locked"のような非決定的な失敗を起こさないことを確認する
    worker_count = 20
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def worker(n: int) -> None:
        try:
            store.create("group/project", n)
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
        assert store.find("group/project", n) is not None


def test_find_wraps_sqlite_errors_as_issue_ticket_store_error(store):
    # sqlite3の低レベル例外(接続が閉じられている等)がそのまま伝播すると、呼び出し側
    # (Poller等)がIssueTicketStoreErrorだけをcatchする契約をすり抜けてしまう回帰テスト
    store.close()

    with pytest.raises(IssueTicketStoreError):
        store.find("group/project", 1)


def test_create_wraps_sqlite_errors_as_issue_ticket_store_error(store):
    store.close()

    with pytest.raises(IssueTicketStoreError):
        store.create("group/project", 1)
