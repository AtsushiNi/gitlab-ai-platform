from gitlab_ai_platform.review import Finding, ReviewResult, Severity, render_markdown


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
