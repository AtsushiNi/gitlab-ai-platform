from gitlab_ai_platform.review import ReviewError, ReviewOutputParseError


def test_review_output_parse_error_is_review_error():
    assert issubclass(ReviewOutputParseError, ReviewError)


def test_review_output_parse_error_holds_raw_text():
    exc = ReviewOutputParseError("bad output", raw_text="not json at all")

    assert str(exc) == "bad output"
    assert exc.raw_text == "not json at all"
