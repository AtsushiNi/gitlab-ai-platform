"""push と MR 作成フェーズが扱うデータの型。

`plan/types.py`/`implement/types.py`と同じ設計方針。本フェーズはClaude Codeを呼び出さない
(ADR-0034「論点3」)ため、Claude Code応答をパースした結果型(`PlanResult`/`ImplementResult`
に相当するもの)は持たない。`PushInput`は`implement`完了時の`Job`(`payload`/`result`両方)
から組み立てる入力のみを持つ。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PushInput:
    """push と MR 作成フェーズの入力。実装フェーズ(M4-8)完了時の`Job`から組み立てる。

    `implement`のJob **payload**由来のフィールド(`plan_document`)と、Job **result**由来の
    フィールド(`commit_sha`以降)が混在する(ADR-0034「論点2」: `implement`のJob resultには
    `plan_document`が含まれないため、payloadから引き継ぐ必要がある)。
    """

    project: str
    issue_iid: int

    plan_document: str
    """実装計画フェーズの成果物(Markdown)。MR本文の「設計要約」として使う(ADR-0034「論点2」)。"""

    summary: str
    """`ImplementResult.summary`をそのまま転記。MR本文の実装概要として使う。"""

    commit_message: str | None
    """`ImplementResult.commit_message`をそのまま転記。push時のcommit messageとして使う。"""

    commit_sha: str
    """実装フェーズが確認したworktreeの実際のHEAD commit sha。push対象。"""

    remote_branch: str
    """GitLab上の実装用branch名(`ai/issue-<issue_iid>`)。push先・MRのsource_branch。"""

    local_branch: str
    """worktreeのローカルbranch名(`issue-<issue_iid>`)。"""

    worktree_path: str
    """worktreeの絶対パス。diff計算(`push.git_ops`)の対象。"""

    assumed_uncertainties: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    """実装フェーズがASSUME判定した前提の一覧(`{"question", "severity", "assumption"}`)。
    MR本文の「○○と仮定して実装した」として使う(ADR-0034「論点2」)。"""


__all__ = ["PushInput"]
