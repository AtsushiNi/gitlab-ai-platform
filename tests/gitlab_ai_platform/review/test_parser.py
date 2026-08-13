import pytest

from gitlab_ai_platform.review import Severity
from gitlab_ai_platform.review.errors import ReviewOutputParseError
from gitlab_ai_platform.review.parser import parse_review_output

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


def test_parses_fenced_json_block_with_surrounding_prose():
    result_text = f"確認事項: 特にありません。\n\n{_VALID_JSON_BLOCK}\n"

    result = parse_review_output(result_text)

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

    result = parse_review_output(result_text)

    assert result.summary == "特に指摘なし"
    assert result.findings == ()


def test_uses_last_fenced_block_when_multiple_present():
    result_text = (
        "例えばこういう形式です:\n"
        '```json\n{"summary": "example", "findings": []}\n```\n\n'
        "実際の結論はこちらです:\n"
        f"{_VALID_JSON_BLOCK}\n"
    )

    result = parse_review_output(result_text)

    assert result.summary == "認証まわりに重大な指摘が1件あります。"


def test_finding_line_can_be_null():
    result_text = (
        '```json\n{"summary": "s", "findings": [{"severity": "minor", "file": "a.py", '
        '"line": null, "rationale": "r", "suggestion": "s"}]}\n```'
    )

    result = parse_review_output(result_text)

    assert result.findings[0].line is None


def test_raises_when_no_json_found():
    with pytest.raises(ReviewOutputParseError) as excinfo:
        parse_review_output("特に指摘なしです。以上です。")

    assert excinfo.value.raw_text == "特に指摘なしです。以上です。"


def test_raises_when_findings_key_missing():
    with pytest.raises(ReviewOutputParseError):
        parse_review_output('```json\n{"summary": "s"}\n```')


def test_raises_when_findings_is_not_a_list():
    with pytest.raises(ReviewOutputParseError):
        parse_review_output('```json\n{"summary": "s", "findings": "not a list"}\n```')


def test_raises_when_severity_is_invalid():
    result_text = (
        '```json\n{"summary": "s", "findings": [{"severity": "urgent", "file": "a.py", '
        '"line": 1, "rationale": "r", "suggestion": "s"}]}\n```'
    )

    with pytest.raises(ReviewOutputParseError):
        parse_review_output(result_text)


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
    import json

    result_text = json.dumps({"summary": "s", "findings": [finding]})

    with pytest.raises(ReviewOutputParseError):
        parse_review_output(result_text)


def test_raises_when_line_is_not_int_or_null():
    result_text = (
        '```json\n{"summary": "s", "findings": [{"severity": "major", "file": "a.py", '
        '"line": "42", "rationale": "r", "suggestion": "s"}]}\n```'
    )

    with pytest.raises(ReviewOutputParseError):
        parse_review_output(result_text)
