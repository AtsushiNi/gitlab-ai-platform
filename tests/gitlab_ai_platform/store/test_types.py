import dataclasses
from datetime import datetime

import pytest

from gitlab_ai_platform.store import ReviewRecord, ReviewStatus


def test_review_record_optional_fields_default_to_none():
    record = ReviewRecord(
        project="group/project",
        mr_iid=1,
        commit_sha="abc123",
        status=ReviewStatus.PENDING,
    )

    assert record.reviewed_at is None
    assert record.result_path is None


def test_review_record_is_frozen():
    record = ReviewRecord(
        project="group/project",
        mr_iid=1,
        commit_sha="abc123",
        status=ReviewStatus.PENDING,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        record.status = ReviewStatus.DONE


def test_review_record_holds_reviewed_at_and_result_path():
    reviewed_at = datetime(2026, 8, 13, 12, 0, 0)
    record = ReviewRecord(
        project="group/project",
        mr_iid=1,
        commit_sha="abc123",
        status=ReviewStatus.DONE,
        reviewed_at=reviewed_at,
        result_path="reviews/group/project/1/abc123",
    )

    assert record.reviewed_at == reviewed_at
    assert record.result_path == "reviews/group/project/1/abc123"


def test_review_status_values():
    assert ReviewStatus.PENDING == "pending"
    assert ReviewStatus.RUNNING == "running"
    assert ReviewStatus.DONE == "done"
    assert ReviewStatus.FAILED == "failed"
