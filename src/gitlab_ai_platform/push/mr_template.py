"""push フェーズが組み立てるMRタイトル・本文のテンプレート。

Issue #115本文の要求(MRテンプレートの設計は本フェーズで新規に必要な唯一の実装)に基づき、
「対応Issue」「設計要約」「○○と仮定して実装した」の3項目を必須で含む。「設計要約」は
`plan_document`(実装計画フェーズの成果物)を使う(ADR-0034「論点2」)。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# GitLabのMRタイトルに実務上の長さ制限は無いが、一覧表示で見切れないよう短く保つ
_MAX_TITLE_SUMMARY_LENGTH = 72


def build_merge_request_title(issue_iid: int, summary: str) -> str:
    """MRタイトルを組み立てる。`summary`の先頭行を短く切り詰めて使う決定的な処理。"""
    first_line = next(
        (line.strip() for line in summary.splitlines() if line.strip()), ""
    )
    if not first_line:
        first_line = "実装"
    if len(first_line) > _MAX_TITLE_SUMMARY_LENGTH:
        first_line = first_line[: _MAX_TITLE_SUMMARY_LENGTH - 1] + "…"
    return f"[Issue #{issue_iid}] {first_line}"


def build_merge_request_description(
    issue_iid: int,
    *,
    plan_document: str,
    summary: str,
    assumed_uncertainties: Sequence[Mapping[str, Any]],
) -> str:
    """MR本文(Markdown)を組み立てる。

    「対応Issue」「設計要約」「○○と仮定して実装した」を必須項目として含む
    (Issue #115本文の要求)。`Closes #<issue_iid>`はGitLabのIssueクローズキーワードでもあり、
    MRマージ時にIssueを自動クローズする効果も兼ねる。
    """
    sections = [
        "## 対応Issue",
        "",
        f"Closes #{issue_iid}",
        "",
        "## 設計要約",
        "",
        plan_document.strip() or "(実装計画フェーズの記録がありません)",
        "",
        "## 実装概要",
        "",
        summary.strip() or "(要約がありません)",
        "",
        "## 仮定して実装した内容",
        "",
        _format_assumed_uncertainties(assumed_uncertainties),
        "",
        "---",
        "",
        "このMRはAI(Claude Code)による無人実行トラックで作成されました。"
        "マージ前に必ず人間のレビューを受けてください。",
    ]
    return "\n".join(sections)


def _format_assumed_uncertainties(items: Sequence[Mapping[str, Any]]) -> str:
    if not items:
        return "特になし(実装にあたって仮定した前提はありませんでした)"
    lines = [
        f"- {item.get('question', '')} → **{item.get('assumption', '')}**と仮定して実装した"
        for item in items
    ]
    return "\n".join(lines)


__all__ = ["build_merge_request_description", "build_merge_request_title"]
