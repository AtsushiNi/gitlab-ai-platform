import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from gitlab_ai_platform.orchestrator import UncertaintySeverity
from gitlab_ai_platform.plan import PlanOutputParseError
from gitlab_ai_platform.plan.parser import parse_plan_output
from gitlab_ai_platform.runner import RunResult

_VALID_JSON_BLOCK = """```json
{
  "plan_document": "# 概要\\n実装計画の本文です。",
  "tasks": [
    {"title": "タスク1", "description": "内容1"},
    {"title": "タスク2", "description": "内容2"}
  ],
  "open_questions": [
    {
      "question": "移行データは既存か?",
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
    result_text = f"計画しました。\n\n{_VALID_JSON_BLOCK}\n"

    plan = parse_plan_output(_run_result(result_text))

    assert plan.plan_document == "# 概要\n実装計画の本文です。"
    assert len(plan.tasks) == 2
    assert plan.tasks[0].title == "タスク1"
    assert plan.tasks[0].description == "内容1"
    assert plan.tasks[1].title == "タスク2"

    assert len(plan.uncertainties) == 2
    critical, minor = plan.uncertainties
    assert critical.question == "移行データは既存か?"
    assert critical.severity == UncertaintySeverity.CRITICAL
    assert critical.assumption is None
    assert critical.phase == "plan"

    assert minor.severity == UncertaintySeverity.MINOR
    assert minor.assumption == "一般的な文言を使う"
    assert minor.phase == "plan"


def test_parses_raw_json_without_fence():
    result_text = json.dumps(
        {
            "plan_document": "本文",
            "tasks": [{"title": "タスク1", "description": "内容1"}],
            "open_questions": [],
        }
    )

    plan = parse_plan_output(_run_result(result_text))

    assert plan.plan_document == "本文"
    assert len(plan.tasks) == 1
    assert plan.uncertainties == ()


def test_uses_last_fenced_block_when_multiple_present():
    result_text = (
        "例えばこういう形式です:\n"
        '```json\n{"plan_document": "仮", "tasks": [{"title": "t", "description": "d"}], '
        '"open_questions": []}\n```\n\n'
        "実際の結論はこちらです:\n"
        f"{_VALID_JSON_BLOCK}\n"
    )

    plan = parse_plan_output(_run_result(result_text))

    assert plan.plan_document == "# 概要\n実装計画の本文です。"


def test_raises_when_no_json_found():
    with pytest.raises(PlanOutputParseError) as excinfo:
        parse_plan_output(_run_result("計画しました。以上です。"))

    assert excinfo.value.raw_text == "計画しました。以上です。"


def test_raises_when_plan_document_is_missing():
    payload = {
        "tasks": [{"title": "タスク1", "description": "内容1"}],
        "open_questions": [],
    }

    with pytest.raises(PlanOutputParseError):
        parse_plan_output(_run_result(json.dumps(payload)))


def test_raises_when_plan_document_is_not_a_string():
    payload = {
        "plan_document": 123,
        "tasks": [{"title": "タスク1", "description": "内容1"}],
        "open_questions": [],
    }

    with pytest.raises(PlanOutputParseError):
        parse_plan_output(_run_result(json.dumps(payload)))


def test_raises_when_plan_document_is_blank():
    payload = {
        "plan_document": "   ",
        "tasks": [{"title": "タスク1", "description": "内容1"}],
        "open_questions": [],
    }

    with pytest.raises(PlanOutputParseError):
        parse_plan_output(_run_result(json.dumps(payload)))


def test_raises_when_tasks_is_not_a_list():
    payload = {"plan_document": "本文", "tasks": "not a list", "open_questions": []}

    with pytest.raises(PlanOutputParseError):
        parse_plan_output(_run_result(json.dumps(payload)))


def test_raises_when_tasks_is_empty():
    payload = {"plan_document": "本文", "tasks": [], "open_questions": []}

    with pytest.raises(PlanOutputParseError):
        parse_plan_output(_run_result(json.dumps(payload)))


def test_raises_when_task_title_is_missing():
    payload = {
        "plan_document": "本文",
        "tasks": [{"description": "内容1"}],
        "open_questions": [],
    }

    with pytest.raises(PlanOutputParseError):
        parse_plan_output(_run_result(json.dumps(payload)))


def test_raises_when_task_description_is_blank():
    payload = {
        "plan_document": "本文",
        "tasks": [{"title": "タスク1", "description": "   "}],
        "open_questions": [],
    }

    with pytest.raises(PlanOutputParseError):
        parse_plan_output(_run_result(json.dumps(payload)))


def test_raises_when_open_questions_is_not_a_list():
    payload = {
        "plan_document": "本文",
        "tasks": [{"title": "タスク1", "description": "内容1"}],
        "open_questions": "not a list",
    }

    with pytest.raises(PlanOutputParseError):
        parse_plan_output(_run_result(json.dumps(payload)))


def test_raises_when_severity_is_invalid():
    payload = {
        "plan_document": "本文",
        "tasks": [{"title": "タスク1", "description": "内容1"}],
        "open_questions": [{"question": "q", "severity": "urgent"}],
    }

    with pytest.raises(PlanOutputParseError):
        parse_plan_output(_run_result(json.dumps(payload)))


def test_raises_when_question_is_missing():
    payload = {
        "plan_document": "本文",
        "tasks": [{"title": "タスク1", "description": "内容1"}],
        "open_questions": [{"severity": "critical"}],
    }

    with pytest.raises(PlanOutputParseError):
        parse_plan_output(_run_result(json.dumps(payload)))


def test_raises_when_minor_severity_missing_assumption():
    payload = {
        "plan_document": "本文",
        "tasks": [{"title": "タスク1", "description": "内容1"}],
        "open_questions": [{"question": "q", "severity": "minor"}],
    }

    with pytest.raises(PlanOutputParseError):
        parse_plan_output(_run_result(json.dumps(payload)))


def test_critical_severity_does_not_require_assumption():
    payload = {
        "plan_document": "本文",
        "tasks": [{"title": "タスク1", "description": "内容1"}],
        "open_questions": [{"question": "q", "severity": "critical"}],
    }

    plan = parse_plan_output(_run_result(json.dumps(payload)))

    assert plan.uncertainties[0].assumption is None


def test_raises_when_run_result_is_error_without_inspecting_result_text():
    run_result = _run_result(_VALID_JSON_BLOCK, is_error=True)

    with pytest.raises(PlanOutputParseError) as excinfo:
        parse_plan_output(run_result)

    assert excinfo.value.raw_text == _VALID_JSON_BLOCK


def test_parses_successfully_despite_permission_denials_present():
    plan = parse_plan_output(
        _run_result(_VALID_JSON_BLOCK, permission_denials=({"tool_name": "Bash"},))
    )

    assert plan.plan_document == "# 概要\n実装計画の本文です。"
