from gitlab_ai_platform.issue_store import (
    DuplicateIssueTicketError,
    IssueTicketStoreError,
)


def test_duplicate_issue_ticket_error_is_an_issue_ticket_store_error():
    assert issubclass(DuplicateIssueTicketError, IssueTicketStoreError)
