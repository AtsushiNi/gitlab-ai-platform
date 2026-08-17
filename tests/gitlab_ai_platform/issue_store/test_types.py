import dataclasses
from datetime import datetime

import pytest

from gitlab_ai_platform.issue_store import IssueTicketRecord


def test_issue_ticket_record_is_frozen():
    record = IssueTicketRecord(
        project="group/project", issue_iid=1, ticketed_at=datetime(2026, 8, 17, 9, 0, 0)
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        record.issue_iid = 2


def test_issue_ticket_record_holds_ticketed_at():
    ticketed_at = datetime(2026, 8, 17, 9, 0, 0)
    record = IssueTicketRecord(
        project="group/project", issue_iid=1, ticketed_at=ticketed_at
    )

    assert record.project == "group/project"
    assert record.issue_iid == 1
    assert record.ticketed_at == ticketed_at
