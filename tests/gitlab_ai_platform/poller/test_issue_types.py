import dataclasses

import pytest

from gitlab_ai_platform.poller import DetectedIssue, IssuePollError, IssuePollResult


def test_detected_issue_is_frozen():
    detected = DetectedIssue(project="group/project", issue_iid=1, job_id="job-1")

    with pytest.raises(dataclasses.FrozenInstanceError):
        detected.issue_iid = 2


def test_issue_poll_error_issue_iid_defaults_to_none_for_project_level_failures():
    error = IssuePollError(
        project="group/project", issue_iid=None, message="接続エラー"
    )

    assert error.issue_iid is None


def test_issue_poll_result_holds_created_and_errors():
    detected = DetectedIssue(project="group/project", issue_iid=1, job_id="job-1")
    error = IssuePollError(project="group/other", issue_iid=None, message="接続エラー")

    result = IssuePollResult(created=(detected,), errors=(error,))

    assert result.created == (detected,)
    assert result.errors == (error,)
