import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from gitlab_ai_platform.issue_analysis import IssueAnalysisOutputParseError
from gitlab_ai_platform.issue_analysis.parser import parse_issue_analysis_output
from gitlab_ai_platform.orchestrator import UncertaintySeverity
from gitlab_ai_platform.runner import RunResult

_VALID_JSON_BLOCK = """```json
{
  "requirements": ["ユーザーがログインできるようにする"],
  "acceptance_criteria": ["メールアドレスとパスワードでログインできる"],
  "assumptions": ["対象ユーザーは登録済みであることを前提とする"],
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
    result_text = f"分析結果です。\n\n{_VALID_JSON_BLOCK}\n"

    analysis = parse_issue_analysis_output(_run_result(result_text))

    assert analysis.requirements == ("ユーザーがログインできるようにする",)
    assert analysis.acceptance_criteria == (
        "メールアドレスとパスワードでログインできる",
    )
    assert analysis.assumptions == ("対象ユーザーは登録済みであることを前提とする",)
    assert len(analysis.uncertainties) == 2

    critical, minor = analysis.uncertainties
    assert critical.question == "パスワード再設定フローも対象ですか?"
    assert critical.severity == UncertaintySeverity.CRITICAL
    assert critical.assumption is None
    assert critical.phase == "issue-analysis"

    assert minor.severity == UncertaintySeverity.MINOR
    assert minor.assumption == "一般的な文言を使う"
    assert minor.phase == "issue-analysis"


def test_parses_raw_json_without_fence():
    result_text = json.dumps(
        {
            "requirements": [],
            "acceptance_criteria": [],
            "assumptions": [],
            "open_questions": [],
        }
    )

    analysis = parse_issue_analysis_output(_run_result(result_text))

    assert analysis.requirements == ()
    assert analysis.uncertainties == ()


def test_uses_last_fenced_block_when_multiple_present():
    result_text = (
        "例えばこういう形式です:\n"
        '```json\n{"requirements": [], "acceptance_criteria": [], '
        '"assumptions": [], "open_questions": []}\n```\n\n'
        "実際の結論はこちらです:\n"
        f"{_VALID_JSON_BLOCK}\n"
    )

    analysis = parse_issue_analysis_output(_run_result(result_text))

    assert analysis.requirements == ("ユーザーがログインできるようにする",)


def test_raises_when_no_json_found():
    with pytest.raises(IssueAnalysisOutputParseError) as excinfo:
        parse_issue_analysis_output(_run_result("分析結果です。以上です。"))

    assert excinfo.value.raw_text == "分析結果です。以上です。"


@pytest.mark.parametrize(
    "missing_field", ["requirements", "acceptance_criteria", "assumptions"]
)
def test_raises_when_string_list_field_missing(missing_field):
    payload = {
        "requirements": ["r"],
        "acceptance_criteria": ["a"],
        "assumptions": ["p"],
        "open_questions": [],
    }
    del payload[missing_field]

    with pytest.raises(IssueAnalysisOutputParseError):
        parse_issue_analysis_output(_run_result(json.dumps(payload)))


def test_raises_when_string_list_field_is_not_a_list_of_strings():
    payload = {
        "requirements": "not a list",
        "acceptance_criteria": [],
        "assumptions": [],
        "open_questions": [],
    }

    with pytest.raises(IssueAnalysisOutputParseError):
        parse_issue_analysis_output(_run_result(json.dumps(payload)))


def test_raises_when_open_questions_is_not_a_list():
    payload = {
        "requirements": [],
        "acceptance_criteria": [],
        "assumptions": [],
        "open_questions": "not a list",
    }

    with pytest.raises(IssueAnalysisOutputParseError):
        parse_issue_analysis_output(_run_result(json.dumps(payload)))


def test_raises_when_severity_is_invalid():
    payload = {
        "requirements": [],
        "acceptance_criteria": [],
        "assumptions": [],
        "open_questions": [{"question": "q", "severity": "urgent"}],
    }

    with pytest.raises(IssueAnalysisOutputParseError):
        parse_issue_analysis_output(_run_result(json.dumps(payload)))


def test_raises_when_question_is_missing():
    payload = {
        "requirements": [],
        "acceptance_criteria": [],
        "assumptions": [],
        "open_questions": [{"severity": "critical"}],
    }

    with pytest.raises(IssueAnalysisOutputParseError):
        parse_issue_analysis_output(_run_result(json.dumps(payload)))


def test_raises_when_minor_severity_missing_assumption():
    # judge_uncertainty(orchestrator)がMissingAssumptionErrorを送出する前に、
    # パーサー自身がプロンプトの指示違反として早期検知する
    payload = {
        "requirements": [],
        "acceptance_criteria": [],
        "assumptions": [],
        "open_questions": [{"question": "q", "severity": "minor"}],
    }

    with pytest.raises(IssueAnalysisOutputParseError):
        parse_issue_analysis_output(_run_result(json.dumps(payload)))


def test_critical_severity_does_not_require_assumption():
    payload = {
        "requirements": [],
        "acceptance_criteria": [],
        "assumptions": [],
        "open_questions": [{"question": "q", "severity": "critical"}],
    }

    analysis = parse_issue_analysis_output(_run_result(json.dumps(payload)))

    assert analysis.uncertainties[0].assumption is None


def test_raises_when_run_result_is_error_without_inspecting_result_text():
    run_result = _run_result(_VALID_JSON_BLOCK, is_error=True)

    with pytest.raises(IssueAnalysisOutputParseError) as excinfo:
        parse_issue_analysis_output(run_result)

    assert excinfo.value.raw_text == _VALID_JSON_BLOCK


def test_parses_successfully_despite_permission_denials_present():
    analysis = parse_issue_analysis_output(
        _run_result(_VALID_JSON_BLOCK, permission_denials=({"tool_name": "Bash"},))
    )

    assert analysis.requirements == ("ユーザーがログインできるようにする",)
