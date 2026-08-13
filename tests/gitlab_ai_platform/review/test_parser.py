import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from gitlab_ai_platform.review import Severity
from gitlab_ai_platform.review.errors import ReviewOutputParseError
from gitlab_ai_platform.review.parser import parse_review_output
from gitlab_ai_platform.runner import RunResult

_VALID_JSON_BLOCK = """```json
{
  "summary": "認証まわりに重大な指摘が1件あります。",
  "findings": [
    {
      "severity": "critical",
      "file": "src/app/auth.py",
      "line": 42,
      "rationale": "トークン検証前にセッションを確立している。",
      "suggestion": "検証の後に移動する。"
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
    result_text = f"確認事項: 特にありません。\n\n{_VALID_JSON_BLOCK}\n"

    result = parse_review_output(_run_result(result_text))

    assert result.summary == "認証まわりに重大な指摘が1件あります。"
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.severity == Severity.CRITICAL
    assert finding.file == "src/app/auth.py"
    assert finding.line == 42
    assert finding.rationale == "トークン検証前にセッションを確立している。"
    assert finding.suggestion == "検証の後に移動する。"


def test_parses_raw_json_without_fence():
    result_text = '{"summary": "特に指摘なし", "findings": []}'

    result = parse_review_output(_run_result(result_text))

    assert result.summary == "特に指摘なし"
    assert result.findings == ()


def test_uses_last_fenced_block_when_multiple_present():
    result_text = (
        "例えばこういう形式です:\n"
        '```json\n{"summary": "example", "findings": []}\n```\n\n'
        "実際の結論はこちらです:\n"
        f"{_VALID_JSON_BLOCK}\n"
    )

    result = parse_review_output(_run_result(result_text))

    assert result.summary == "認証まわりに重大な指摘が1件あります。"


def test_parses_json_block_whose_suggestion_contains_nested_code_fence():
    # suggestionフィールドの中にコード例(```)が含まれていても、応答末尾に最も近い
    # ```で閉じフェンスを正しく認識できることの回帰テスト
    payload = {
        "summary": "s",
        "findings": [
            {
                "severity": "minor",
                "file": "a.py",
                "line": 1,
                "rationale": "r",
                "suggestion": "次のように直してください:\n```python\nx = 1\n```",
            }
        ],
    }
    result_text = f"確認事項: 特にありません。\n\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```"

    result = parse_review_output(_run_result(result_text))

    assert "```python" in result.findings[0].suggestion


def test_finding_line_can_be_null():
    result_text = (
        '```json\n{"summary": "s", "findings": [{"severity": "minor", "file": "a.py", '
        '"line": null, "rationale": "r", "suggestion": "s"}]}\n```'
    )

    result = parse_review_output(_run_result(result_text))

    assert result.findings[0].line is None


def test_raises_when_no_json_found():
    with pytest.raises(ReviewOutputParseError) as excinfo:
        parse_review_output(_run_result("特に指摘なしです。以上です。"))

    assert excinfo.value.raw_text == "特に指摘なしです。以上です。"


def test_raises_when_findings_key_missing():
    with pytest.raises(ReviewOutputParseError):
        parse_review_output(_run_result('```json\n{"summary": "s"}\n```'))


def test_raises_when_findings_is_not_a_list():
    with pytest.raises(ReviewOutputParseError):
        parse_review_output(
            _run_result('```json\n{"summary": "s", "findings": "not a list"}\n```')
        )


def test_raises_when_severity_is_invalid():
    result_text = (
        '```json\n{"summary": "s", "findings": [{"severity": "urgent", "file": "a.py", '
        '"line": 1, "rationale": "r", "suggestion": "s"}]}\n```'
    )

    with pytest.raises(ReviewOutputParseError):
        parse_review_output(_run_result(result_text))


@pytest.mark.parametrize("missing_field", ["file", "rationale", "suggestion"])
def test_raises_when_required_field_missing(missing_field):
    finding = {
        "severity": "major",
        "file": "a.py",
        "line": 1,
        "rationale": "r",
        "suggestion": "s",
    }
    del finding[missing_field]

    result_text = json.dumps({"summary": "s", "findings": [finding]})

    with pytest.raises(ReviewOutputParseError):
        parse_review_output(_run_result(result_text))


def test_raises_when_line_is_not_int_or_null():
    result_text = (
        '```json\n{"summary": "s", "findings": [{"severity": "major", "file": "a.py", '
        '"line": "42", "rationale": "r", "suggestion": "s"}]}\n```'
    )

    with pytest.raises(ReviewOutputParseError):
        parse_review_output(_run_result(result_text))


def test_raises_when_line_is_bool():
    # boolはPythonではintのサブクラスなので、"line": true が誤って通らないことの回帰テスト
    result_text = (
        '```json\n{"summary": "s", "findings": [{"severity": "major", "file": "a.py", '
        '"line": true, "rationale": "r", "suggestion": "s"}]}\n```'
    )

    with pytest.raises(ReviewOutputParseError):
        parse_review_output(_run_result(result_text))


def test_raises_when_run_result_is_error_without_inspecting_result_text():
    # is_error=Trueの場合、result_textにたまたま有効なJSONが残っていても採用しない
    # (runner/types.pyの「result_textの内容だけで成否判定してはならない」契約)
    run_result = _run_result(_VALID_JSON_BLOCK, is_error=True)

    with pytest.raises(ReviewOutputParseError) as excinfo:
        parse_review_output(run_result)

    assert excinfo.value.raw_text == _VALID_JSON_BLOCK


def test_parses_successfully_despite_permission_denials_present():
    # permission_denialsは警告ログの対象ではあるが、レビュー結果自体は
    # 引き続き利用可能なので解析は続行する
    result = parse_review_output(
        _run_result(_VALID_JSON_BLOCK, permission_denials=({"tool_name": "Bash"},))
    )

    assert result.summary == "認証まわりに重大な指摘が1件あります。"
