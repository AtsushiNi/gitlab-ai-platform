"""`ReviewResult`を人間可読なMarkdownへ整形する。

`docs/architecture.md`の「JSON(機械可読)とMarkdown(人間可読)の両方を出力する」を満たす、
Markdown側の担当。人間がVS Code(GitLab拡張)で結果を読む運用(`docs/architecture.md`の
データフロー9.)を想定し、重要度の高い指摘から順に並べる。
"""

from __future__ import annotations

from .types import Finding, ReviewResult, Severity

# 重要度の並び順(重大なものを先に読めるようにする)。Enum定義順とも一致させている
_SEVERITY_ORDER = (Severity.CRITICAL, Severity.MAJOR, Severity.MINOR)
_SEVERITY_LABELS = {
    Severity.CRITICAL: "Critical",
    Severity.MAJOR: "Major",
    Severity.MINOR: "Minor",
}


def render_markdown(result: ReviewResult, *, project: str, mr_iid: int, sha: str) -> str:
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
        return "\n".join(lines) + "\n"

    ordered = sorted(
        result.findings, key=lambda finding: _SEVERITY_ORDER.index(finding.severity)
    )
    for finding in ordered:
        lines += ["", *_render_finding(finding)]

    return "\n".join(lines) + "\n"


def _render_finding(finding: Finding) -> list[str]:
    location = f"{finding.file}:{finding.line}" if finding.line is not None else finding.file
    return [
        f"### [{_SEVERITY_LABELS[finding.severity]}] {location}",
        "",
        f"- **根拠**: {finding.rationale}",
        f"- **改善案**: {finding.suggestion}",
    ]


__all__ = ["render_markdown"]
