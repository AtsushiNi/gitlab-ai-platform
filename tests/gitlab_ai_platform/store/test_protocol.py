from datetime import datetime

from gitlab_ai_platform.store import ReviewRecord, ReviewStatus, StateStore

_EXPECTED_PUBLIC_METHODS = {"find", "create", "update_status", "close"}


def _public_methods(protocol_cls: type) -> set[str]:
    return {name for name in dir(protocol_cls) if not name.startswith("_")}


def test_state_store_exposes_only_expected_operations():
    # ビジネスロジックを持たない「単なる状態の記録・照会」(docs/architecture.md)という境界を、
    # 将来メソッドが増えた際にこのテストで検知できるようにする。
    assert _public_methods(StateStore) == _EXPECTED_PUBLIC_METHODS


class _FakeStateStore:
    """Protocolを満たすダミー実装。SQLite/PostgreSQLどちらの実装もこの形になる。"""

    def __init__(self) -> None:
        self._records: dict[tuple[str, int, str], ReviewRecord] = {}

    def find(self, project: str, mr_iid: int, commit_sha: str) -> ReviewRecord | None:
        return self._records.get((project, mr_iid, commit_sha))

    def create(
        self,
        project: str,
        mr_iid: int,
        commit_sha: str,
        *,
        status: ReviewStatus = ReviewStatus.PENDING,
    ) -> ReviewRecord:
        record = ReviewRecord(project=project, mr_iid=mr_iid, commit_sha=commit_sha, status=status)
        self._records[(project, mr_iid, commit_sha)] = record
        return record

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
        record = ReviewRecord(
            project=project,
            mr_iid=mr_iid,
            commit_sha=commit_sha,
            status=status,
            reviewed_at=reviewed_at,
            result_path=result_path,
        )
        self._records[(project, mr_iid, commit_sha)] = record
        return record

    def close(self) -> None:
        pass


def test_fake_state_store_satisfies_protocol():
    store = _FakeStateStore()

    assert isinstance(store, StateStore)
