import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from gitlab_ai_platform.implement import ImplementOutputParseError
from gitlab_ai_platform.implement.parser import parse_implement_output
from gitlab_ai_platform.orchestrator import UncertaintySeverity
from gitlab_ai_platform.runner import RunResult

_VALID_JSON_BLOCK = """```json
{
  "summary": "タスク1・タスク2を実装しテストが通りました。",
  "commit_message": "タスク1・タスク2を実装",
  "tests_passed": true,
  "open_questions": [
    {
      "question": "既存の命名規則に従いますか?",
      "severity": "critical"
    },
    {
      "question": "エラーハンドリングの粒度は?",
      "severity": "minor",
      "assumption": "既存の例外型をそのまま再送出した"
    }
  ]
}
```"""


def _run_result(
    result_text: str,
    *,
    is_error: bool = False,
    permission_denials: tuple[Mapping[str, Any], ...] = (),
) -> RunResult:
    return RunResult(
        is_error=is_error,
        result_text=result_text,
        session_id="sess-1",
        terminal_reason="completed",
        permission_denials=permission_denials,
        num_turns=1,
        total_cost_usd=0.0,
        timed_out=False,
        duration_seconds=1.0,
        log_path=Path("/tmp/log.json"),
        raw={},
    )


def test_parses_fenced_json_block_with_surrounding_prose():
    result_text = f"実装しました。\n\n{_VALID_JSON_BLOCK}\n"

    result = parse_implement_output(_run_result(result_text))

    assert result.summary == "タスク1・タスク2を実装しテストが通りました。"
    assert result.commit_message == "タスク1・タスク2を実装"
    assert result.tests_passed is True

    assert len(result.uncertainties) == 2
    critical, minor = result.uncertainties
    assert critical.question == "既存の命名規則に従いますか?"
    assert critical.severity == UncertaintySeverity.CRITICAL
    assert critical.assumption is None
    assert critical.phase == "implement"

    assert minor.severity == UncertaintySeverity.MINOR
    assert minor.assumption == "既存の例外型をそのまま再送出した"
    assert minor.phase == "implement"


def test_parses_raw_json_without_fence():
    result_text = json.dumps(
        {
            "summary": "実装完了",
            "commit_message": None,
            "tests_passed": False,
            "open_questions": [],
        }
    )

    result = parse_implement_output(_run_result(result_text))

    assert result.summary == "実装完了"
    assert result.commit_message is None
    assert result.tests_passed is False
    assert result.uncertainties == ()


def test_uses_last_fenced_block_when_multiple_present():
    result_text = (
        "例えばこういう形式です:\n"
        '```json\n{"summary": "仮", "commit_message": null, "tests_passed": false, '
        '"open_questions": []}\n```\n\n'
        "実際の結論はこちらです:\n"
        f"{_VALID_JSON_BLOCK}\n"
    )

    result = parse_implement_output(_run_result(result_text))

    assert result.summary == "タスク1・タスク2を実装しテストが通りました。"


def test_raises_when_no_json_found():
    with pytest.raises(ImplementOutputParseError) as excinfo:
        parse_implement_output(_run_result("実装しました。以上です。"))

    assert excinfo.value.raw_text == "実装しました。以上です。"


def test_raises_when_summary_is_missing():
    payload = {"commit_message": None, "tests_passed": False, "open_questions": []}

    with pytest.raises(ImplementOutputParseError):
        parse_implement_output(_run_result(json.dumps(payload)))


def test_raises_when_summary_is_blank():
    payload = {
        "summary": "   ",
        "commit_message": None,
        "tests_passed": False,
        "open_questions": [],
    }

    with pytest.raises(ImplementOutputParseError):
        parse_implement_output(_run_result(json.dumps(payload)))


def test_raises_when_commit_message_is_not_string_or_null():
    payload = {
        "summary": "実装完了",
        "commit_message": 123,
        "tests_passed": True,
        "open_questions": [],
    }

    with pytest.raises(ImplementOutputParseError):
        parse_implement_output(_run_result(json.dumps(payload)))


def test_raises_when_tests_passed_is_not_boolean():
    payload = {
        "summary": "実装完了",
        "commit_message": "msg",
        "tests_passed": "yes",
        "open_questions": [],
    }

    with pytest.raises(ImplementOutputParseError):
        parse_implement_output(_run_result(json.dumps(payload)))


def test_raises_when_open_questions_is_not_a_list():
    payload = {
        "summary": "実装完了",
        "commit_message": "msg",
        "tests_passed": True,
        "open_questions": "not a list",
    }

    with pytest.raises(ImplementOutputParseError):
        parse_implement_output(_run_result(json.dumps(payload)))


def test_raises_when_severity_is_invalid():
    payload = {
        "summary": "実装完了",
        "commit_message": "msg",
        "tests_passed": True,
        "open_questions": [{"question": "q", "severity": "urgent"}],
    }

    with pytest.raises(ImplementOutputParseError):
        parse_implement_output(_run_result(json.dumps(payload)))


def test_raises_when_minor_severity_missing_assumption():
    payload = {
        "summary": "実装完了",
        "commit_message": "msg",
        "tests_passed": True,
        "open_questions": [{"question": "q", "severity": "minor"}],
    }

    with pytest.raises(ImplementOutputParseError):
        parse_implement_output(_run_result(json.dumps(payload)))


def test_critical_severity_does_not_require_assumption():
    payload = {
        "summary": "実装完了",
        "commit_message": "msg",
        "tests_passed": True,
        "open_questions": [{"question": "q", "severity": "critical"}],
    }

    result = parse_implement_output(_run_result(json.dumps(payload)))

    assert result.uncertainties[0].assumption is None


def test_raises_when_run_result_is_error_without_inspecting_result_text():
    run_result = _run_result(_VALID_JSON_BLOCK, is_error=True)

    with pytest.raises(ImplementOutputParseError) as excinfo:
        parse_implement_output(run_result)

    assert excinfo.value.raw_text == _VALID_JSON_BLOCK


def test_parses_successfully_despite_permission_denials_present():
    result = parse_implement_output(
        _run_result(_VALID_JSON_BLOCK, permission_denials=({"tool_name": "Bash"},))
    )

    assert result.summary == "タスク1・タスク2を実装しテストが通りました。"
