from gitlab_ai_platform.implement import (
    ImplementationNotCommittedError,
    ImplementError,
    ImplementOutputParseError,
)


def test_implement_output_parse_error_is_implement_error():
    assert issubclass(ImplementOutputParseError, ImplementError)


def test_implement_output_parse_error_holds_raw_text():
    exc = ImplementOutputParseError("bad output", raw_text="not json at all")

    assert str(exc) == "bad output"
    assert exc.raw_text == "not json at all"


def test_implementation_not_committed_error_is_implement_error():
    assert issubclass(ImplementationNotCommittedError, ImplementError)
