"""Webhook受信サーバーが扱うデータの型。

GitLabの生のWebhookペイロード(JSON)を、Poller/State Storeが理解できる最小限の型に
変換した結果を表す(`docs/adr/0018-webhook-receiver.md`「扱うイベントはMerge Request Hookのみ」)。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedMergeRequestEvent:
    """Merge Request Hookペイロードから抽出した、起票判断に必要な最小限の情報。

    `poller.types.DetectedReview`と似た形だが、こちらは「レビュー対象になりうる候補」
    (レビュー待ちラベルが付いた開いているMR)であり、まだState Storeへの起票は行っていない
    (`server.py`が`labels`を`review_label`と突き合わせたうえで
    `poller.ticket_if_unprocessed`を呼ぶ)。
    """

    project: str
    mr_iid: int
    commit_sha: str
    labels: tuple[str, ...]


__all__ = ["ParsedMergeRequestEvent"]
