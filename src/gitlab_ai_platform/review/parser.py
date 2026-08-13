"""Claude Codeの応答(`RunResult.result_text`)から`ReviewResult`を組み立てる。

`prompts.build_review_instructions`は、応答の末尾に指摘一覧をまとめた ```json コードブロックを
1つだけ出力するようClaude Codeに指示する(`docs/specs/prompts.md`)。ここではその指示に
実際に従っていた場合の抽出・検証だけを行い、従っていなかった場合は`ReviewOutputParseError`を
送出する(`errors.py`のdocstring参照)。想像で埋め合わせず、パース不能なものはパース不能として
扱う。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .errors import ReviewOutputParseError
from .types import Finding, ReviewResult, Severity

# 応答の中に自然文の説明が混じっていてもよいよう、```json ... ``` フェンスを最優先で探す。
# 複数ブロックが出力された場合は指示違反だが、「最後のブロックが最終的な結論」という想定で
# 最後の1つを採用する(前置きの途中で例として出したJSONを誤って採用しないため)。
_JSON_FENCE_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)


def parse_review_output(result_text: str) -> ReviewResult:
    """`result_text`から結果スキーマ(`summary`/`findings`)を抽出する。

    スキーマを満たすJSONを抽出できない場合は`ReviewOutputParseError`を送出する
    (`raw_text`に元の`result_text`をそのまま保持するため、呼び出し側は失敗時も
    人間が読める形で内容を確認できる)。
    """
    payload = _extract_json_object(result_text)
    return _build_review_result(payload, raw_text=result_text)


def _extract_json_object(result_text: str) -> Any:
    matches = _JSON_FENCE_RE.findall(result_text)
    candidates = [matches[-1]] if matches else []
    # フェンスが無い応答(指示より短いdiff無しMR等でClaude Codeがコードブロックの
    # マークダウン記法を省略した場合)にも備え、全文をJSONとして解釈するのも試す
    candidates.append(result_text.strip())

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue

    raise ReviewOutputParseError(
        "Claude Codeの応答から結果スキーマのJSONを抽出できませんでした"
        + (f"({last_error})" if last_error is not None else ""),
        raw_text=result_text,
    )


def _build_review_result(payload: Any, *, raw_text: str) -> ReviewResult:
    if not isinstance(payload, dict):
        raise ReviewOutputParseError(
            "結果JSONがオブジェクト形式ではありませんでした", raw_text=raw_text
        )

    findings_raw = payload.get("findings")
    if not isinstance(findings_raw, list):
        raise ReviewOutputParseError(
            "結果JSONの`findings`が配列ではありませんでした", raw_text=raw_text
        )

    findings = tuple(_build_finding(item, raw_text=raw_text) for item in findings_raw)
    summary = payload.get("summary", "")
    if not isinstance(summary, str):
        raise ReviewOutputParseError(
            "結果JSONの`summary`が文字列ではありませんでした", raw_text=raw_text
        )

    return ReviewResult(summary=summary, findings=findings)


def _build_finding(item: Any, *, raw_text: str) -> Finding:
    if not isinstance(item, dict):
        raise ReviewOutputParseError(
            "`findings`の要素がオブジェクト形式ではありませんでした", raw_text=raw_text
        )

    severity_raw = item.get("severity")
    try:
        severity = Severity(str(severity_raw).lower())
    except ValueError as exc:
        raise ReviewOutputParseError(
            f"`findings`の`severity`が不正な値でした: {severity_raw!r}"
            f"(critical/major/minorのいずれかである必要があります)",
            raw_text=raw_text,
        ) from exc

    file_path = item.get("file")
    rationale = item.get("rationale")
    suggestion = item.get("suggestion")
    if not isinstance(file_path, str) or not file_path:
        raise ReviewOutputParseError(
            "`findings`の`file`が空でない文字列ではありませんでした", raw_text=raw_text
        )
    if not isinstance(rationale, str) or not rationale:
        raise ReviewOutputParseError(
            "`findings`の`rationale`が空でない文字列ではありませんでした", raw_text=raw_text
        )
    if not isinstance(suggestion, str) or not suggestion:
        raise ReviewOutputParseError(
            "`findings`の`suggestion`が空でない文字列ではありませんでした", raw_text=raw_text
        )

    line = item.get("line")
    if line is not None and not isinstance(line, int):
        raise ReviewOutputParseError(
            "`findings`の`line`が整数でもnullでもありませんでした", raw_text=raw_text
        )

    return Finding(
        severity=severity, file=file_path, line=line, rationale=rationale, suggestion=suggestion
    )


__all__ = ["parse_review_output"]
