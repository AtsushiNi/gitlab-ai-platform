import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from gitlab_ai_platform.design import DesignOutputParseError
from gitlab_ai_platform.design.parser import parse_design_output
from gitlab_ai_platform.orchestrator import UncertaintySeverity
from gitlab_ai_platform.runner import RunResult

_VALID_JSON_BLOCK = """```json
{
  "design_document": "# 責務\\n設計文書の本文です。",
  "open_questions": [
    {
      "question": "パスワード再設定フローも対象ですか?",
      "severity": "critical"
    },
    {
      "question": "エラーメッセージの文言は?",
      "severity": "minor",
      "assumption": "一般的な文言を使う"
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
    result_text = f"設計しました。\n\n{_VALID_JSON_BLOCK}\n"

    design = parse_design_output(_run_result(result_text))

    assert design.design_document == "# 責務\n設計文書の本文です。"
    assert len(design.uncertainties) == 2

    critical, minor = design.uncertainties
    assert critical.question == "パスワード再設定フローも対象ですか?"
    assert critical.severity == UncertaintySeverity.CRITICAL
    assert critical.assumption is None
    assert critical.phase == "design"

    assert minor.severity == UncertaintySeverity.MINOR
    assert minor.assumption == "一般的な文言を使う"
    assert minor.phase == "design"


def test_parses_raw_json_without_fence():
    result_text = json.dumps({"design_document": "本文", "open_questions": []})

    design = parse_design_output(_run_result(result_text))

    assert design.design_document == "本文"
    assert design.uncertainties == ()


def test_uses_last_fenced_block_when_multiple_present():
    result_text = (
        "例えばこういう形式です:\n"
        '```json\n{"design_document": "仮", "open_questions": []}\n```\n\n'
        "実際の結論はこちらです:\n"
        f"{_VALID_JSON_BLOCK}\n"
    )

    design = parse_design_output(_run_result(result_text))

    assert design.design_document == "# 責務\n設計文書の本文です。"


def test_raises_when_no_json_found():
    with pytest.raises(DesignOutputParseError) as excinfo:
        parse_design_output(_run_result("設計しました。以上です。"))

    assert excinfo.value.raw_text == "設計しました。以上です。"


def test_raises_when_design_document_is_missing():
    payload = {"open_questions": []}

    with pytest.raises(DesignOutputParseError):
        parse_design_output(_run_result(json.dumps(payload)))


def test_raises_when_design_document_is_not_a_string():
    payload = {"design_document": 123, "open_questions": []}

    with pytest.raises(DesignOutputParseError):
        parse_design_output(_run_result(json.dumps(payload)))


def test_raises_when_design_document_is_blank():
    payload = {"design_document": "   ", "open_questions": []}

    with pytest.raises(DesignOutputParseError):
        parse_design_output(_run_result(json.dumps(payload)))


def test_raises_when_open_questions_is_not_a_list():
    payload = {"design_document": "本文", "open_questions": "not a list"}

    with pytest.raises(DesignOutputParseError):
        parse_design_output(_run_result(json.dumps(payload)))


def test_raises_when_severity_is_invalid():
    payload = {
        "design_document": "本文",
        "open_questions": [{"question": "q", "severity": "urgent"}],
    }

    with pytest.raises(DesignOutputParseError):
        parse_design_output(_run_result(json.dumps(payload)))


def test_raises_when_question_is_missing():
    payload = {
        "design_document": "本文",
        "open_questions": [{"severity": "critical"}],
    }

    with pytest.raises(DesignOutputParseError):
        parse_design_output(_run_result(json.dumps(payload)))


def test_raises_when_minor_severity_missing_assumption():
    payload = {
        "design_document": "本文",
        "open_questions": [{"question": "q", "severity": "minor"}],
    }

    with pytest.raises(DesignOutputParseError):
        parse_design_output(_run_result(json.dumps(payload)))


def test_critical_severity_does_not_require_assumption():
    payload = {
        "design_document": "本文",
        "open_questions": [{"question": "q", "severity": "critical"}],
    }

    design = parse_design_output(_run_result(json.dumps(payload)))

    assert design.uncertainties[0].assumption is None


def test_raises_when_run_result_is_error_without_inspecting_result_text():
    run_result = _run_result(_VALID_JSON_BLOCK, is_error=True)

    with pytest.raises(DesignOutputParseError) as excinfo:
        parse_design_output(run_result)

    assert excinfo.value.raw_text == _VALID_JSON_BLOCK


def test_parses_successfully_despite_permission_denials_present():
    design = parse_design_output(
        _run_result(_VALID_JSON_BLOCK, permission_denials=({"tool_name": "Bash"},))
    )

    assert design.design_document == "# 責務\n設計文書の本文です。"
