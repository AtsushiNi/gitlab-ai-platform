"""MR Poller が扱うデータの型。

ポーリング1サイクル分の結果(新規起票・継続可能なエラー)を表す型を定義する
(`docs/architecture.md` の MR Poller の責務)。GitLab MRやレビュー記録そのものの型は
`gitlab_adapter.types.MergeRequest` / `store.types.ReviewRecord` をそのまま再利用し、
Poller独自の型として作り直さない。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DetectedReview:
    """1回のポーリングで新たに起票された(State Storeにレコードを作成した)レビュー。"""

    project: str
    mr_iid: int
    commit_sha: str


@dataclass(frozen=True)
class PollError:
    """1回のポーリングサイクル中に発生した、走査を継続可能なエラー。

    `mr_iid`はプロジェクト単位の走査(MR一覧取得)自体が失敗した場合は`None`になる。
    """

    project: str
    mr_iid: int | None
    message: str


@dataclass(frozen=True)
class PollResult:
    """1回のポーリングサイクル(全対象プロジェクトの走査)の結果。"""

    created: tuple[DetectedReview, ...]
    errors: tuple[PollError, ...]


__all__ = ["DetectedReview", "PollError", "PollResult"]
