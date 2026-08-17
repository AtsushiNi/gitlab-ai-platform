import pytest

from gitlab_ai_platform.webhook.errors import WebhookPayloadError
from gitlab_ai_platform.webhook.parser import parse_merge_request_event
from gitlab_ai_platform.webhook.types import ParsedMergeRequestEvent


def _merge_request_payload(
    *,
    state: str = "opened",
    iid: int = 1,
    project_path: str = "group/project",
    commit_sha: str | None = "sha-1",
    labels: list[dict] | None = None,
) -> dict:
    object_attributes: dict = {"iid": iid, "state": state}
    if commit_sha is not None:
        object_attributes["last_commit"] = {"id": commit_sha}

    payload: dict = {
        "object_kind": "merge_request",
        "object_attributes": object_attributes,
        "project": {"path_with_namespace": project_path},
    }
    if labels is not None:
        payload["labels"] = labels
    return payload


def test_parse_merge_request_event_returns_none_for_other_object_kinds():
    payload = {"object_kind": "push", "ref": "refs/heads/main"}

    assert parse_merge_request_event(payload) is None


def test_parse_merge_request_event_extracts_project_iid_sha_and_labels():
    payload = _merge_request_payload(
        labels=[{"title": "レビュー待ち"}, {"title": "bug"}]
    )

    parsed = parse_merge_request_event(payload)

    assert parsed == ParsedMergeRequestEvent(
        project="group/project",
        mr_iid=1,
        commit_sha="sha-1",
        labels=("レビュー待ち", "bug"),
    )


def test_parse_merge_request_event_defaults_to_empty_labels_when_missing():
    payload = _merge_request_payload(labels=None)

    parsed = parse_merge_request_event(payload)

    assert parsed is not None
    assert parsed.labels == ()


def test_parse_merge_request_event_ignores_label_entries_without_title():
    payload = _merge_request_payload(
        labels=[{"title": "レビュー待ち"}, {"id": 99}, "not-a-dict"]
    )

    parsed = parse_merge_request_event(payload)

    assert parsed is not None
    assert parsed.labels == ("レビュー待ち",)


@pytest.mark.parametrize("state", ["closed", "merged", "locked"])
def test_parse_merge_request_event_returns_none_for_non_opened_state(state):
    payload = _merge_request_payload(state=state)

    assert parse_merge_request_event(payload) is None


def test_parse_merge_request_event_returns_none_when_last_commit_missing():
    # MR作成直後等、last_commitを持たないイベントは対象外(レビュー対象commitがまだ無い)
    payload = _merge_request_payload(commit_sha=None)

    assert parse_merge_request_event(payload) is None


def test_parse_merge_request_event_raises_when_payload_is_not_a_dict():
    with pytest.raises(WebhookPayloadError):
        parse_merge_request_event(["not", "a", "dict"])


def test_parse_merge_request_event_raises_when_object_attributes_missing():
    payload = {
        "object_kind": "merge_request",
        "project": {"path_with_namespace": "group/project"},
    }

    with pytest.raises(WebhookPayloadError):
        parse_merge_request_event(payload)


def test_parse_merge_request_event_raises_when_project_missing():
    payload = {
        "object_kind": "merge_request",
        "object_attributes": {"iid": 1, "state": "opened"},
    }

    with pytest.raises(WebhookPayloadError):
        parse_merge_request_event(payload)


def test_parse_merge_request_event_raises_when_iid_is_not_an_int():
    payload = _merge_request_payload()
    payload["object_attributes"]["iid"] = "1"

    with pytest.raises(WebhookPayloadError):
        parse_merge_request_event(payload)
