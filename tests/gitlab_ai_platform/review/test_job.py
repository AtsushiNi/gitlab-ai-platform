from gitlab_ai_platform.job import JobType
from gitlab_ai_platform.review.job import (
    REVIEW_JOB_TYPE,
    build_review_job_payload,
    build_review_job_result,
    review_job_payload_to_args,
)

_PROJECT = "group/project"
_MR_IID = 1
_SHA = "abc123"


def test_review_job_type_is_review():
    assert REVIEW_JOB_TYPE == JobType.REVIEW


def test_build_review_job_payload_without_sha_omits_sha_key():
    payload = build_review_job_payload(_PROJECT, _MR_IID)

    assert payload == {"project": _PROJECT, "mr_iid": _MR_IID}
    assert "sha" not in payload


def test_build_review_job_payload_with_sha_includes_sha_key():
    payload = build_review_job_payload(_PROJECT, _MR_IID, sha=_SHA)

    assert payload == {"project": _PROJECT, "mr_iid": _MR_IID, "sha": _SHA}


def test_review_job_payload_to_args_round_trips_without_sha():
    payload = build_review_job_payload(_PROJECT, _MR_IID)

    assert review_job_payload_to_args(payload) == (_PROJECT, _MR_IID, None)


def test_review_job_payload_to_args_round_trips_with_sha():
    payload = build_review_job_payload(_PROJECT, _MR_IID, sha=_SHA)

    assert review_job_payload_to_args(payload) == (_PROJECT, _MR_IID, _SHA)


def test_build_review_job_result_contains_result_path():
    result = build_review_job_result(
        _PROJECT, _MR_IID, _SHA, "reviews/group/project/1/abc123"
    )

    assert result == {
        "project": _PROJECT,
        "mr_iid": _MR_IID,
        "sha": _SHA,
        "result_path": "reviews/group/project/1/abc123",
    }
