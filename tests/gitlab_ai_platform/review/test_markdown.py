from gitlab_ai_platform.review import (
    Finding,
    ReviewComparison,
    ReviewResult,
    Severity,
    render_markdown,
)


def test_renders_no_findings_case():
    result = ReviewResult(summary="特に指摘なし", findings=())

    md = render_markdown(
        result, project="group/project", mr_iid=1, sha="abcdef0123456789"
    )

    assert "group/project" in md
    assert "!1" in md
    assert "abcdef012345" in md
    assert "特に指摘なし" in md


def test_orders_findings_by_severity_critical_first():
    result = ReviewResult(
        summary="s",
        findings=(
            Finding(
                severity=Severity.MINOR,
                file="a.py",
                line=1,
                rationale="ra",
                suggestion="sa",
            ),
            Finding(
                severity=Severity.CRITICAL,
                file="b.py",
                line=2,
                rationale="rb",
                suggestion="sb",
            ),
            Finding(
                severity=Severity.MAJOR,
                file="c.py",
                line=3,
                rationale="rc",
                suggestion="sc",
            ),
        ),
    )

    md = render_markdown(result, project="group/project", mr_iid=1, sha="abc123")

    assert md.index("b.py") < md.index("c.py") < md.index("a.py")


def test_includes_rationale_and_suggestion():
    result = ReviewResult(
        summary="s",
        findings=(
            Finding(
                severity=Severity.MAJOR,
                file="src/app.py",
                line=10,
                rationale="根拠のテキスト",
                suggestion="改善案のテキスト",
            ),
        ),
    )

    md = render_markdown(result, project="group/project", mr_iid=1, sha="abc123")

    assert "src/app.py:10" in md
    assert "根拠のテキスト" in md
    assert "改善案のテキスト" in md


def test_finding_without_line_omits_colon_suffix():
    result = ReviewResult(
        summary="s",
        findings=(
            Finding(
                severity=Severity.MINOR,
                file="src/app.py",
                line=None,
                rationale="r",
                suggestion="s",
            ),
        ),
    )

    md = render_markdown(result, project="group/project", mr_iid=1, sha="abc123")

    assert "src/app.py:None" not in md


def test_comparison_omitted_keeps_output_unchanged():
    # comparisonを渡さない既存の呼び出しは、バッジ・追加セクション無しの従来通りの出力になる
    result = ReviewResult(
        summary="s",
        findings=(
            Finding(
                severity=Severity.MAJOR,
                file="a.py",
                line=1,
                rationale="r",
                suggestion="s",
            ),
        ),
    )

    md = render_markdown(result, project="group/project", mr_iid=1, sha="abc123")

    assert "[新規]" not in md
    assert "[未対応]" not in md
    assert "修正済み" not in md


def test_comparison_marks_new_and_unresolved_findings_with_badges():
    new_finding = Finding(
        severity=Severity.CRITICAL, file="a.py", line=1, rationale="ra", suggestion="sa"
    )
    unresolved_finding = Finding(
        severity=Severity.MINOR, file="b.py", line=2, rationale="rb", suggestion="sb"
    )
    result = ReviewResult(summary="s", findings=(new_finding, unresolved_finding))
    comparison = ReviewComparison(
        new=(new_finding,), unresolved=(unresolved_finding,), resolved=()
    )

    md = render_markdown(
        result, project="group/project", mr_iid=1, sha="abc123", comparison=comparison
    )

    assert "[新規]" in md
    assert "[未対応]" in md


def test_comparison_appends_resolved_section():
    resolved_finding = Finding(
        severity=Severity.MAJOR,
        file="c.py",
        line=3,
        rationale="解消された指摘の根拠",
        suggestion="改善案",
    )
    result = ReviewResult(summary="特に指摘なし", findings=())
    comparison = ReviewComparison(new=(), unresolved=(), resolved=(resolved_finding,))

    md = render_markdown(
        result, project="group/project", mr_iid=1, sha="abc123", comparison=comparison
    )

    assert "前回から修正された指摘 (1件)" in md
    assert "解消された指摘の根拠" in md
    assert "[修正済み]" in md


def test_comparison_without_resolved_findings_omits_section():
    result = ReviewResult(summary="s", findings=())
    comparison = ReviewComparison(new=(), unresolved=(), resolved=())

    md = render_markdown(
        result, project="group/project", mr_iid=1, sha="abc123", comparison=comparison
    )

    assert "前回から修正された指摘" not in md
