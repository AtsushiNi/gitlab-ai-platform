from gitlab_ai_platform.plan import PlanError, PlanOutputParseError


def test_plan_output_parse_error_is_plan_error():
    assert issubclass(PlanOutputParseError, PlanError)


def test_plan_output_parse_error_holds_raw_text():
    exc = PlanOutputParseError("bad output", raw_text="not json at all")

    assert str(exc) == "bad output"
    assert exc.raw_text == "not json at all"
