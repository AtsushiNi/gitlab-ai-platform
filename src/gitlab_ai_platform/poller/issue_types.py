"""Issue Poller が扱うデータの型。

ポーリング1サイクル分の結果(新規起票・継続可能なエラー)を表す型を定義する
(`docs/adr/0025-issue-poller-dedup.md`)。MR Poller(`poller/types.py`)と対になる構成だが、
Issueには`commit_sha`に相当する版の概念がないため、別の型として定義する(既存の
`DetectedReview`/`PollError`/`PollResult`を流用・変更しない)。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DetectedIssue:
    """1回のポーリングで新たに起票された(Issue Ticket Storeにレコードを作成し、
    無人実行Jobをキューへ投入した)Issue。"""

    project: str
    issue_iid: int
    job_id: str


@dataclass(frozen=True)
class IssuePollError:
    """1回のポーリングサイクル中に発生した、走査を継続可能なエラー。

    `issue_iid`はプロジェクト単位の走査(Issue一覧取得)自体が失敗した場合は`None`になる。
    """

    project: str
    issue_iid: int | None
    message: str


@dataclass(frozen=True)
class IssuePollResult:
    """1回のポーリングサイクル(全対象プロジェクトの走査)の結果。"""

    created: tuple[DetectedIssue, ...]
    errors: tuple[IssuePollError, ...]


__all__ = ["DetectedIssue", "IssuePollError", "IssuePollResult"]
