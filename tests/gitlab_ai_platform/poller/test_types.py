import dataclasses

import pytest

from gitlab_ai_platform.poller import DetectedReview, PollError, PollResult


def test_detected_review_is_frozen():
    detected = DetectedReview(project="group/project", mr_iid=1, commit_sha="abc123")

    with pytest.raises(dataclasses.FrozenInstanceError):
        detected.mr_iid = 2


def test_poll_error_mr_iid_defaults_to_none_for_project_level_failures():
    error = PollError(project="group/project", mr_iid=None, message="接続エラー")

    assert error.mr_iid is None


def test_poll_result_holds_created_and_errors():
    detected = DetectedReview(project="group/project", mr_iid=1, commit_sha="abc123")
    error = PollError(project="group/other", mr_iid=None, message="接続エラー")

    result = PollResult(created=(detected,), errors=(error,))

    assert result.created == (detected,)
    assert result.errors == (error,)
