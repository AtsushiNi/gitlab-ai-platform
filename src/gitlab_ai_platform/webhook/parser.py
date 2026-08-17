"""GitLab Webhookペイロード(JSON)のパース。

方針(M3-6 [#96](https://github.com/AtsushiNi/gitlab-ai-platform/issues/96)、
`docs/adr/0018-webhook-receiver.md`「扱うイベントはMerge Request Hookのみ」):

- `object_kind`が`"merge_request"`以外(Push Hook等)のペイロードはエラーにせず`None`を返す
  (`server.py`側は`None`を「無視してよいイベント」として`200 OK`で応答する)。
- `state`が`"opened"`以外(closed/merged)のMRイベントも`None`を返す。MR Poller
  (`list_merge_requests(state="opened")`)と同じ絞り込み条件に揃える。
- `object_attributes`/`project`等の必須フィールドが欠けている場合(GitLab側の仕様変更や
  想定外のペイロード)は`WebhookPayloadError`を送出する。GitLab側の設定ミス(Merge Request
  Hook以外を有効化している)とは区別し、`400 Bad Request`として扱えるようにするため。
"""

from __future__ import annotations

from typing import Any

from .errors import WebhookPayloadError
from .types import ParsedMergeRequestEvent

_MERGE_REQUEST_OBJECT_KIND = "merge_request"
_OPENED_STATE = "opened"


def parse_merge_request_event(payload: Any) -> ParsedMergeRequestEvent | None:
    """Merge Request Hookペイロードをパースする。対象外のイベントは`None`を返す。"""
    if not isinstance(payload, dict):
        raise WebhookPayloadError("Webhookペイロードがオブジェクト(dict)ではありません")

    if payload.get("object_kind") != _MERGE_REQUEST_OBJECT_KIND:
        # Push Hook等、Merge Request Hook以外のイベント種別は対象外として無視する
        return None

    try:
        object_attributes = payload["object_attributes"]
        project = payload["project"]
        state = object_attributes["state"]
        mr_iid = object_attributes["iid"]
        project_path = project["path_with_namespace"]
    except (KeyError, TypeError) as exc:
        raise WebhookPayloadError(
            f"Merge Request Hookペイロードの形式が不正です: {exc}"
        ) from exc

    if state != _OPENED_STATE:
        # クローズ/マージ済みMRのイベント(action: close/merge等)は対象外
        return None

    if not isinstance(mr_iid, int) or isinstance(mr_iid, bool):
        raise WebhookPayloadError(
            f"object_attributes.iid が整数ではありません: {mr_iid!r}"
        )

    last_commit = object_attributes.get("last_commit")
    if not isinstance(last_commit, dict) or "id" not in last_commit:
        # MR作成直後等、last_commitを持たないイベントは対象外として無視する
        # (レビュー対象となる新規commitが存在しない)
        return None
    commit_sha = last_commit["id"]

    labels = tuple(
        label["title"]
        for label in payload.get("labels", []) or ()
        if isinstance(label, dict) and isinstance(label.get("title"), str)
    )

    return ParsedMergeRequestEvent(
        project=project_path, mr_iid=mr_iid, commit_sha=commit_sha, labels=labels
    )


__all__ = ["parse_merge_request_event"]
