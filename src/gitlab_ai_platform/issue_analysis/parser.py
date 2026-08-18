"""Claude Codeの応答(`RunResult`)から`RequirementAnalysis`を組み立てる。

`review/parser.py`の`parse_review_output`と同じ設計方針(`docs/specs/review-output.md`の
パターンを踏襲、CLAUDE.mdの実装イメージが指示する「独自のパースルールを一から発明しない」)。
`prompts.build_issue_analysis_instructions`は、応答の末尾に分析結果をまとめた ```json
コードブロックを1つだけ出力するようClaude Codeに指示する。ここではその指示に実際に従っていた
場合の抽出・検証だけを行い、従っていなかった場合は`IssueAnalysisOutputParseError`を送出する。
想像で埋め合わせず、パース不能なものはパース不能として扱う。
"""

from __future__ import annotations

import json
from typing import Any

from ..logging_ import get_logger
from ..orchestrator.types import Uncertainty, UncertaintySeverity
from ..runner.types import RunResult
from .errors import IssueAnalysisOutputParseError
from .types import RequirementAnalysis

_logger = get_logger(__name__)

# review/parser.pyと同じ理由(応答中に自然文の説明が混じっていてもよいよう、末尾の
# ```json ... ``` フェンスを最優先で探す)
_JSON_FENCE_START = "```json"
_JSON_FENCE_END = "```"

# 不明点が生じたフェーズを表す固定値(orchestrator.types.Uncertainty.phase)
_PHASE = "issue-analysis"


def parse_issue_analysis_output(run_result: RunResult) -> RequirementAnalysis:
    """`run_result`から結果スキーマ(`requirements`/`acceptance_criteria`/`assumptions`/
    `open_questions`)を抽出する。

    `run_result.is_error`がTrueの場合は`result_text`の中身を解釈せず、その時点で
    `IssueAnalysisOutputParseError`を送出する。`permission_denials`が空でない場合は
    (分析自体は続行可能なため)警告ログのみ残す。スキーマを満たすJSONを抽出できない場合も
    `IssueAnalysisOutputParseError`を送出する(`raw_text`に元の`result_text`をそのまま保持する
    ため、呼び出し側は失敗時も人間が読める形で内容を確認できる)。
    """
    if run_result.is_error:
        raise IssueAnalysisOutputParseError(
            "Claude Codeの実行がエラー終了しました"
            f"(terminal_reason={run_result.terminal_reason!r})",
            raw_text=run_result.result_text,
        )
    if run_result.permission_denials:
        _logger.warning(
            "issue_analysis.permission_denials_present",
            extra={"count": len(run_result.permission_denials)},
        )

    payload = _extract_json_object(run_result.result_text)
    return _build_requirement_analysis(payload, raw_text=run_result.result_text)


def _extract_json_object(result_text: str) -> Any:
    candidates = []
    trailing_fence = _extract_trailing_json_fence(result_text)
    if trailing_fence is not None:
        candidates.append(trailing_fence)
    # フェンスが無い応答にも備え、全文をJSONとして解釈するのも試す(review/parser.pyと同じ方針)
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

    raise IssueAnalysisOutputParseError(
        "Claude Codeの応答から結果スキーマのJSONを抽出できませんでした"
        + (f"({last_error})" if last_error is not None else ""),
        raw_text=result_text,
    )


def _extract_trailing_json_fence(result_text: str) -> str | None:
    """応答末尾の```jsonフェンスの中身を取り出す(`review/parser.py`と同じロジック)。

    「最後の```jsonの直後から、応答末尾に最も近い```まで」を1つのブロックとして扱うことで、
    JSON内部の文字列値に```が含まれていても誤って切れないようにする。
    """
    start = result_text.rfind(_JSON_FENCE_START)
    if start == -1:
        return None
    content_start = start + len(_JSON_FENCE_START)
    end = result_text.rfind(_JSON_FENCE_END)
    if end <= content_start:
        return None
    return result_text[content_start:end].strip()


def _build_requirement_analysis(payload: Any, *, raw_text: str) -> RequirementAnalysis:
    if not isinstance(payload, dict):
        raise IssueAnalysisOutputParseError(
            "結果JSONがオブジェクト形式ではありませんでした", raw_text=raw_text
        )

    requirements = _string_list(payload, "requirements", raw_text=raw_text)
    acceptance_criteria = _string_list(
        payload, "acceptance_criteria", raw_text=raw_text
    )
    assumptions = _string_list(payload, "assumptions", raw_text=raw_text)

    open_questions_raw = payload.get("open_questions")
    if not isinstance(open_questions_raw, list):
        raise IssueAnalysisOutputParseError(
            "結果JSONの`open_questions`が配列ではありませんでした", raw_text=raw_text
        )
    uncertainties = tuple(
        _build_uncertainty(item, raw_text=raw_text) for item in open_questions_raw
    )

    return RequirementAnalysis(
        requirements=requirements,
        acceptance_criteria=acceptance_criteria,
        assumptions=assumptions,
        uncertainties=uncertainties,
    )


def _string_list(
    payload: dict[str, Any], key: str, *, raw_text: str
) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise IssueAnalysisOutputParseError(
            f"結果JSONの`{key}`が文字列の配列ではありませんでした", raw_text=raw_text
        )
    return tuple(value)


def _build_uncertainty(item: Any, *, raw_text: str) -> Uncertainty:
    if not isinstance(item, dict):
        raise IssueAnalysisOutputParseError(
            "`open_questions`の要素がオブジェクト形式ではありませんでした",
            raw_text=raw_text,
        )

    question = item.get("question")
    if not isinstance(question, str) or not question:
        raise IssueAnalysisOutputParseError(
            "`open_questions`の`question`が空でない文字列ではありませんでした",
            raw_text=raw_text,
        )

    severity_raw = item.get("severity")
    try:
        severity = UncertaintySeverity(str(severity_raw).lower())
    except ValueError as exc:
        raise IssueAnalysisOutputParseError(
            f"`open_questions`の`severity`が不正な値でした: {severity_raw!r}"
            "(critical/minorのいずれかである必要があります)",
            raw_text=raw_text,
        ) from exc

    assumption = item.get("assumption")
    if assumption is not None and not isinstance(assumption, str):
        raise IssueAnalysisOutputParseError(
            "`open_questions`の`assumption`が文字列でもnullでもありませんでした",
            raw_text=raw_text,
        )
    if severity is UncertaintySeverity.MINOR and not assumption:
        # judge_uncertainty(orchestrator/judgment.py)がMINORかつassumption無しの場合
        # MissingAssumptionErrorを送出する。プロンプトの指示通りClaude Codeが従っていれば
        # ここには来ないはずだが、指示違反を早期に検知するためここでも検証する
        raise IssueAnalysisOutputParseError(
            "`open_questions`の`severity`が`minor`の場合、`assumption`は必須です",
            raw_text=raw_text,
        )

    return Uncertainty(
        question=question, severity=severity, assumption=assumption, phase=_PHASE
    )


__all__ = ["parse_issue_analysis_output"]
