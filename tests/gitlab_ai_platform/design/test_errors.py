from gitlab_ai_platform.design import DesignError, DesignOutputParseError


def test_design_output_parse_error_is_design_error():
    assert issubclass(DesignOutputParseError, DesignError)


def test_design_output_parse_error_holds_raw_text():
    exc = DesignOutputParseError("bad output", raw_text="not json at all")

    assert str(exc) == "bad output"
    assert exc.raw_text == "not json at all"
