from gitlab_ai_platform.issue_analysis import (
    IssueAnalysisError,
    IssueAnalysisOutputParseError,
)


def test_issue_analysis_output_parse_error_is_issue_analysis_error():
    assert issubclass(IssueAnalysisOutputParseError, IssueAnalysisError)


def test_issue_analysis_output_parse_error_holds_raw_text():
    exc = IssueAnalysisOutputParseError("bad output", raw_text="not json at all")

    assert str(exc) == "bad output"
    assert exc.raw_text == "not json at all"
