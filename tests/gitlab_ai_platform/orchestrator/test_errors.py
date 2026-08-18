from gitlab_ai_platform.orchestrator import MissingAssumptionError, OrchestratorError


def test_missing_assumption_error_is_orchestrator_error():
    assert issubclass(MissingAssumptionError, OrchestratorError)


def test_orchestrator_error_is_exception():
    assert issubclass(OrchestratorError, Exception)
