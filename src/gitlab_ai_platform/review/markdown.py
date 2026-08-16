"""`ReviewResult`を人間可読なMarkdownへ整形する。

`docs/architecture.md`の「JSON(機械可読)とMarkdown(人間可読)の両方を出力する」を満たす、
Markdown側の担当。人間がVS Code(GitLab拡張)で結果を読む運用(`docs/architecture.md`の
データフロー9.)を想定し、重要度の高い指摘から順に並べる。

`comparison`(再レビュー時の前回との突き合わせ結果、M2-2 #81)を渡すと、各指摘に
「新規」「未対応」のバッジを付け、末尾に前回から解消された指摘の一覧を追加する。
`comparison`を渡さない(`None`のままの)呼び出しは従来通りの出力になる。
"""

from __future__ import annotations

from .types import Finding, ReviewComparison, ReviewResult, Severity

# 重要度の並び順(重大なものを先に読めるようにする)。Enum定義順とも一致させている
_SEVERITY_ORDER = (Severity.CRITICAL, Severity.MAJOR, Severity.MINOR)
_SEVERITY_LABELS = {
    Severity.CRITICAL: "Critical",
    Severity.MAJOR: "Major",
    Severity.MINOR: "Minor",
}


def render_markdown(
    result: ReviewResult,
    *,
    project: str,
    mr_iid: int,
    sha: str,
    comparison: ReviewComparison | None = None,
) -> str:
    """`result`を`reviews/<project>/<mr_iid>/<sha>/result.md`向けのMarkdown文字列に整形する。"""
    lines = [
        f"# レビュー結果: {project} !{mr_iid} ({sha[:12]})",
        "",
        "## 概要",
        "",
        result.summary if result.summary else "(要約なし)",
        "",
        f"## 指摘 ({len(result.findings)}件)",
    ]

    if not result.findings:
        lines += ["", "特に指摘なし"]
    else:
        ordered = sorted(
            result.findings,
            key=lambda finding: _SEVERITY_ORDER.index(finding.severity),
        )
        for finding in ordered:
            lines += [
                "",
                *_render_finding(finding, status=_status_label(finding, comparison)),
            ]

    if comparison is not None and comparison.resolved:
        lines += ["", f"## 前回から修正された指摘 ({len(comparison.resolved)}件)"]
        for finding in comparison.resolved:
            lines += ["", *_render_finding(finding, status="修正済み")]

    return "\n".join(lines) + "\n"


def _status_label(finding: Finding, comparison: ReviewComparison | None) -> str | None:
    """今回の指摘1件が、前回との突き合わせで「新規」「未対応」のどちらだったかを返す。

    `comparison`が無い(前回レビューが存在しない)場合は`None`(バッジ無し)。
    """
    if comparison is None:
        return None
    if finding in comparison.new:
        return "新規"
    if finding in comparison.unresolved:
        return "未対応"
    return None


def _render_finding(finding: Finding, *, status: str | None = None) -> list[str]:
    location = (
        f"{finding.file}:{finding.line}" if finding.line is not None else finding.file
    )
    badge = f" [{status}]" if status is not None else ""
    return [
        f"### [{_SEVERITY_LABELS[finding.severity]}]{badge} {location}",
        "",
        f"- **根拠**: {finding.rationale}",
        f"- **改善案**: {finding.suggestion}",
    ]


__all__ = ["render_markdown"]
