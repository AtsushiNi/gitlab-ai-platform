from gitlab_ai_platform.review import Finding, ReviewResult, Severity, compare_findings


def _finding(
    *,
    file: str = "src/app.py",
    line: int = 1,
    rationale: str,
    suggestion: str = "改善案",
) -> Finding:
    return Finding(
        severity=Severity.MAJOR,
        file=file,
        line=line,
        rationale=rationale,
        suggestion=suggestion,
    )


def test_compare_findings_returns_none_when_no_previous_review():
    current = ReviewResult(summary="s", findings=(_finding(rationale="根拠"),))

    assert compare_findings(None, current) is None


def test_compare_findings_marks_identical_finding_as_unresolved():
    finding_text = (
        "認証チェックが抜けています。トークン検証前にセッションを確立しています。"
    )
    previous = ReviewResult(summary="s", findings=(_finding(rationale=finding_text),))
    current = ReviewResult(summary="s", findings=(_finding(rationale=finding_text),))

    comparison = compare_findings(previous, current)

    assert comparison is not None
    assert comparison.unresolved == current.findings
    assert comparison.new == ()
    assert comparison.resolved == ()


def test_compare_findings_marks_similar_wording_as_unresolved():
    # 再レビューのたびにLLMの言い回しが多少変わっても、同じ指摘は「未対応」として扱いたい
    previous = ReviewResult(
        summary="s",
        findings=(
            _finding(
                rationale="認証チェックが抜けています。トークン検証前にセッションを確立しています。"
            ),
        ),
    )
    current = ReviewResult(
        summary="s",
        findings=(
            _finding(
                rationale="認証のチェックが抜けている状態です。トークンの検証よりも前にセッションが確立されます。"
            ),
        ),
    )

    comparison = compare_findings(previous, current)

    assert comparison is not None
    assert comparison.unresolved == current.findings
    assert comparison.new == ()
    assert comparison.resolved == ()


def test_compare_findings_marks_unmatched_current_finding_as_new():
    # 同一ファイル内でも、文面が全く異なる指摘は別物として扱う(「新規」)
    previous = ReviewResult(
        summary="s",
        findings=(
            _finding(
                rationale="Nullチェックが不足しているため例外が発生する可能性があります"
            ),
        ),
    )
    new_finding = _finding(rationale="APIキーがログに平文で出力されています")
    current = ReviewResult(summary="s", findings=(new_finding,))

    comparison = compare_findings(previous, current)

    assert comparison is not None
    assert comparison.new == (new_finding,)
    assert comparison.unresolved == ()
    assert comparison.resolved == previous.findings


def test_compare_findings_marks_unmatched_previous_finding_as_resolved():
    resolved_finding = _finding(rationale="前回だけ指摘されていた問題")
    previous = ReviewResult(summary="s", findings=(resolved_finding,))
    current = ReviewResult(summary="s", findings=())

    comparison = compare_findings(previous, current)

    assert comparison is not None
    assert comparison.resolved == (resolved_finding,)
    assert comparison.new == ()
    assert comparison.unresolved == ()


def test_compare_findings_does_not_match_across_different_files():
    same_text = "同じ文面の指摘です。改善してください。"
    previous = ReviewResult(
        summary="s", findings=(_finding(file="a.py", rationale=same_text),)
    )
    current_finding = _finding(file="b.py", rationale=same_text)
    current = ReviewResult(summary="s", findings=(current_finding,))

    comparison = compare_findings(previous, current)

    assert comparison is not None
    assert comparison.new == (current_finding,)
    assert comparison.resolved == previous.findings


def test_compare_findings_matches_greedily_by_highest_similarity():
    # 前回2件・今回2件のうち、テキストがより近い組み合わせで対応付けられることを検証する
    previous_a = _finding(
        file="a.py", rationale="Nullチェックが不足しているため例外が発生します"
    )
    previous_b = _finding(
        file="a.py", rationale="ログ出力のフォーマットが他の箇所と異なります"
    )
    current_a = _finding(
        file="a.py", rationale="Nullチェックが無いため例外が発生する可能性があります"
    )
    previous = ReviewResult(summary="s", findings=(previous_a, previous_b))
    current = ReviewResult(summary="s", findings=(current_a,))

    comparison = compare_findings(previous, current)

    assert comparison is not None
    assert comparison.unresolved == (current_a,)
    assert comparison.resolved == (previous_b,)
