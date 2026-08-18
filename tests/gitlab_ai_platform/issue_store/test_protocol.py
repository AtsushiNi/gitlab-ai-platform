from datetime import datetime

from gitlab_ai_platform.issue_store import IssueTicketRecord, IssueTicketStore

_EXPECTED_PUBLIC_METHODS = {"find", "create", "close"}


def _public_methods(protocol_cls: type) -> set[str]:
    return {name for name in dir(protocol_cls) if not name.startswith("_")}


def test_issue_ticket_store_exposes_only_expected_operations():
    # 「ビジネスロジックを持たない、単なる状態の記録・照会」という境界(StateStoreと同じ方針)を、
    # 将来メソッドが増えた際にこのテストで検知できるようにする。
    assert _public_methods(IssueTicketStore) == _EXPECTED_PUBLIC_METHODS


class _FakeIssueTicketStore:
    """Protocolを満たすダミー実装。"""

    def __init__(self) -> None:
        self._records: dict[tuple[str, int], IssueTicketRecord] = {}

    def find(self, project: str, issue_iid: int) -> IssueTicketRecord | None:
        return self._records.get((project, issue_iid))

    def create(self, project: str, issue_iid: int) -> IssueTicketRecord:
        record = IssueTicketRecord(
            project=project, issue_iid=issue_iid, ticketed_at=datetime.now()
        )
        self._records[(project, issue_iid)] = record
        return record

    def close(self) -> None:
        pass


def test_fake_issue_ticket_store_satisfies_protocol():
    store = _FakeIssueTicketStore()

    assert isinstance(store, IssueTicketStore)
