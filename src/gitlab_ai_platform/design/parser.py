"""Claude Codeの応答(`RunResult`)から`DesignResult`を組み立てる。

`issue_analysis/parser.py`の`parse_issue_analysis_output`と同じ設計方針
(`docs/specs/review-output.md`のパターンを踏襲、CLAUDE.mdの実装イメージが指示する
「独自のパースルールを一から発明しない」)。`prompts.build_design_instructions`は、応答の末尾に
設計結果をまとめた ```json コードブロックを1つだけ出力するようClaude Codeに指示する。
ここではその指示に実際に従っていた場合の抽出・検証だけを行い、従っていなかった場合は
`DesignOutputParseError`を送出する。想像で埋め合わせず、パース不能なものはパース不能として扱う。
"""

from __future__ import annotations

import json
from typing import Any

from ..logging_ import get_logger
from ..orchestrator.types import Uncertainty, UncertaintySeverity
from ..runner.types import RunResult
from .errors import DesignOutputParseError
from .types import DesignResult

_logger = get_logger(__name__)

# issue_analysis/parser.pyと同じ理由(応答中に自然文の説明が混じっていてもよいよう、末尾の
# ```json ... ``` フェンスを最優先で探す)
_JSON_FENCE_START = "```json"
_JSON_FENCE_END = "```"

# 不明点が生じたフェーズを表す固定値(orchestrator.types.Uncertainty.phase)
_PHASE = "design"


def parse_design_output(run_result: RunResult) -> DesignResult:
    """`run_result`から結果スキーマ(`design_document`/`open_questions`)を抽出する。

    `run_result.is_error`がTrueの場合は`result_text`の中身を解釈せず、その時点で
    `DesignOutputParseError`を送出する。`permission_denials`が空でない場合は(設計自体は
    続行可能なため)警告ログのみ残す。スキーマを満たすJSONを抽出できない場合も
    `DesignOutputParseError`を送出する(`raw_text`に元の`result_text`をそのまま保持するため、
    呼び出し側は失敗時も人間が読める形で内容を確認できる)。
    """
    if run_result.is_error:
        raise DesignOutputParseError(
            "Claude Codeの実行がエラー終了しました"
            f"(terminal_reason={run_result.terminal_reason!r})",
            raw_text=run_result.result_text,
        )
    if run_result.permission_denials:
        _logger.warning(
            "design.permission_denials_present",
            extra={"count": len(run_result.permission_denials)},
        )

    payload = _extract_json_object(run_result.result_text)
    return _build_design_result(payload, raw_text=run_result.result_text)


def _extract_json_object(result_text: str) -> Any:
    candidates = []
    trailing_fence = _extract_trailing_json_fence(result_text)
    if trailing_fence is not None:
        candidates.append(trailing_fence)
    # フェンスが無い応答にも備え、全文をJSONとして解釈するのも試す(issue_analysis/parser.pyと
    # 同じ方針)
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

    raise DesignOutputParseError(
        "Claude Codeの応答から結果スキーマのJSONを抽出できませんでした"
        + (f"({last_error})" if last_error is not None else ""),
        raw_text=result_text,
    )


def _extract_trailing_json_fence(result_text: str) -> str | None:
    """応答末尾の```jsonフェンスの中身を取り出す(`issue_analysis/parser.py`と同じロジック)。"""
    start = result_text.rfind(_JSON_FENCE_START)
    if start == -1:
        return None
    content_start = start + len(_JSON_FENCE_START)
    end = result_text.rfind(_JSON_FENCE_END)
    if end <= content_start:
        return None
    return result_text[content_start:end].strip()


def _build_design_result(payload: Any, *, raw_text: str) -> DesignResult:
    if not isinstance(payload, dict):
        raise DesignOutputParseError(
            "結果JSONがオブジェクト形式ではありませんでした", raw_text=raw_text
        )

    design_document = payload.get("design_document")
    if not isinstance(design_document, str) or not design_document.strip():
        raise DesignOutputParseError(
            "結果JSONの`design_document`が空でない文字列ではありませんでした",
            raw_text=raw_text,
        )

    open_questions_raw = payload.get("open_questions")
    if not isinstance(open_questions_raw, list):
        raise DesignOutputParseError(
            "結果JSONの`open_questions`が配列ではありませんでした", raw_text=raw_text
        )
    uncertainties = tuple(
        _build_uncertainty(item, raw_text=raw_text) for item in open_questions_raw
    )

    return DesignResult(design_document=design_document, uncertainties=uncertainties)


def _build_uncertainty(item: Any, *, raw_text: str) -> Uncertainty:
    if not isinstance(item, dict):
        raise DesignOutputParseError(
            "`open_questions`の要素がオブジェクト形式ではありませんでした",
            raw_text=raw_text,
        )

    question = item.get("question")
    if not isinstance(question, str) or not question:
        raise DesignOutputParseError(
            "`open_questions`の`question`が空でない文字列ではありませんでした",
            raw_text=raw_text,
        )

    severity_raw = item.get("severity")
    try:
        severity = UncertaintySeverity(str(severity_raw).lower())
    except ValueError as exc:
        raise DesignOutputParseError(
            f"`open_questions`の`severity`が不正な値でした: {severity_raw!r}"
            "(critical/minorのいずれかである必要があります)",
            raw_text=raw_text,
        ) from exc

    assumption = item.get("assumption")
    if assumption is not None and not isinstance(assumption, str):
        raise DesignOutputParseError(
            "`open_questions`の`assumption`が文字列でもnullでもありませんでした",
            raw_text=raw_text,
        )
    if severity is UncertaintySeverity.MINOR and not assumption:
        # orchestrator.judge_uncertaintyが同条件で送出するMissingAssumptionErrorより
        # 早期に検知する(issue_analysis/parser.pyと同じ理由)
        raise DesignOutputParseError(
            "`open_questions`の`severity`が`minor`の場合、`assumption`は必須です",
            raw_text=raw_text,
        )

    return Uncertainty(
        question=question, severity=severity, assumption=assumption, phase=_PHASE
    )


__all__ = ["parse_design_output"]
