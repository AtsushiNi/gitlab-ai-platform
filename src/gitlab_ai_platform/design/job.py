"""design種別のJob(`job/`)のpayload/result構造。

方針(M4-6 [#112](https://github.com/AtsushiNi/gitlab-ai-platform/issues/112)、ADR-0029):

- `issue-analysis`と異なり、設計フェーズを投入する自動化された検出器(Poller相当)はまだ
  存在しない(オーケストレーション本体はM4-10のスコープ)。そのため`build_design_job_payload`/
  `design_job_payload_to_args`は`poller/`ではなく本パッケージに置く。M4-10実装時に
  「issue-analysis完了 → design投入」の橋渡しをするコード(呼び出し側)から利用される想定
- payloadは要求分析フェーズ完了時の`Job.result`(`issue_analysis.build_issue_analysis_job_result`/
  `build_resolved_issue_analysis_job_result`が組み立てたもの)をそのまま/一部流用できる形にする。
  Job同士の直接参照(design JobがJobRepository経由でissue-analysis Jobを読みに行く)は
  `JobHandler`が「呼び出し側のJob種別を知らない」というADR-0022の設計を破るため避け、
  呼び出し側(投入者)が必要なフィールドをpayloadとしてそのまま渡す設計にした
- `review/job.py`の`build_review_job_result`・`issue_analysis/job.py`の
  `build_issue_analysis_job_result`/`build_resolved_issue_analysis_job_result`と同じ役割・
  同じ構造パターン(`complete`/`wait_for_human`共通の結果組み立て、`WAITING_HUMAN`後の
  回答統合)をdesignフェーズに横展開したもの。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..orchestrator import UncertaintyJudgment, ask_judgments, assume_judgments
from .types import DesignInput, DesignResult


def build_design_job_payload(
    project: str, issue_iid: int, analysis_result: Mapping[str, Any]
) -> dict[str, Any]:
    """`design`種別Jobの`payload`を組み立てる。

    `analysis_result`は要求分析フェーズ完了時の`Job.result`
    (`issue_analysis.build_issue_analysis_job_result`/`build_resolved_issue_analysis_job_result`が
    組み立てたもの)。`requirements`/`acceptance_criteria`/`assumptions`/`assumed_uncertainties`
    のみを転記し、`project`/`issue_iid`不足情報`questions`/`resolved_questions`のようなdesignには
    不要なフィールドは含めない(design_job_payload_to_argsが必要とするキーだけに絞ることで、
    payloadの契約を明確にする)。
    """
    return {
        "project": project,
        "issue_iid": issue_iid,
        "requirements": list(analysis_result.get("requirements", ())),
        "acceptance_criteria": list(analysis_result.get("acceptance_criteria", ())),
        "assumptions": list(analysis_result.get("assumptions", ())),
        "assumed_uncertainties": list(analysis_result.get("assumed_uncertainties", ())),
    }


def design_job_payload_to_args(
    payload: Mapping[str, Any],
) -> tuple[str, int, DesignInput]:
    """`design`種別Jobの`payload`から`(project, issue_iid, DesignInput)`を取り出す。

    `cli/dispatcher.py`の`build_design_handler`が利用する。
    """
    assumed_uncertainties = tuple(
        _format_assumed_uncertainty(item)
        for item in payload.get("assumed_uncertainties", ())
    )
    design_input = DesignInput(
        requirements=tuple(payload.get("requirements", ())),
        acceptance_criteria=tuple(payload.get("acceptance_criteria", ())),
        assumptions=tuple(payload.get("assumptions", ())),
        assumed_uncertainties=assumed_uncertainties,
    )
    return payload["project"], payload["issue_iid"], design_input


def _format_assumed_uncertainty(item: Mapping[str, Any]) -> str:
    """`{"question", "severity", "assumption"}`形式の辞書を、プロンプトに埋め込みやすい
    1行の文字列(`"question → assumption"`)に整形する。"""
    return f"{item['question']} → {item['assumption']}"


def build_design_job_result(
    project: str,
    issue_iid: int,
    design: DesignResult,
    judgments: Sequence[UncertaintyJudgment],
) -> dict[str, Any]:
    """設計フェーズのJob `result`を組み立てる(`complete`/`wait_for_human`共通)。

    - `design_document`: `design`(Claude Codeの設計結果)から転記
    - `assumed_uncertainties`: `assume_judgments`(`MINOR`かつ`ASSUME`判定の不明点)の一覧
    - `questions`: `ask_judgments`(`CRITICAL`かつ`ASK`判定の不明点)の一覧。空でなければ
      呼び出し側(`cli/dispatcher.py`)がJobを`WAITING_HUMAN`へ遷移させる(ADR-0026)
    """
    return {
        "project": project,
        "issue_iid": issue_iid,
        "design_document": design.design_document,
        "assumed_uncertainties": [
            _judgment_to_dict(j) for j in assume_judgments(judgments)
        ],
        "questions": [_judgment_to_dict(j) for j in ask_judgments(judgments)],
    }


def _judgment_to_dict(judgment: UncertaintyJudgment) -> dict[str, Any]:
    data: dict[str, Any] = {
        "question": judgment.uncertainty.question,
        "severity": judgment.uncertainty.severity.value,
    }
    if judgment.assumption_note is not None:
        data["assumption"] = judgment.assumption_note
    return data


def build_resolved_design_job_result(
    result: Mapping[str, Any], answers: Sequence[str]
) -> dict[str, Any]:
    """`WAITING_HUMAN`の`result`に人間の回答を統合した新しい`result`を組み立てる。

    `issue_analysis.job.build_resolved_issue_analysis_job_result`と全く同じロジック
    (`result["questions"]`と同じ順序・件数の`answers`を受け取り、`resolved_questions`
    (`{"question", "severity", "answer"}`)へ変換するとともに`assumed_uncertainties`へ合流させる)。
    `design_document`等の他フィールドは`**result`でそのまま引き継ぐため、design固有の実装は
    不要。両フェーズで別々の関数として持つ理由はADR-0029「却下した選択肢」を参照(ADR-0028も
    「design/implementが独自の統合関数を持つ必要がある」と予告していた)。

    `answers`の件数が`result["questions"]`と一致しない場合は`ValueError`を送出する。
    """
    questions = result.get("questions", [])
    if len(answers) != len(questions):
        raise ValueError(
            f"answersの件数({len(answers)})がquestionsの件数({len(questions)})と"
            "一致しません"
        )

    resolved_questions = [
        {"question": q["question"], "severity": q["severity"], "answer": answer}
        for q, answer in zip(questions, answers, strict=True)
    ]
    merged_assumed_uncertainties = [
        *result.get("assumed_uncertainties", []),
        *(
            {"question": q["question"], "severity": q["severity"], "assumption": answer}
            for q, answer in zip(questions, answers, strict=True)
        ),
    ]

    return {
        **result,
        "questions": [],
        "resolved_questions": resolved_questions,
        "assumed_uncertainties": merged_assumed_uncertainties,
    }


__all__ = [
    "build_design_job_payload",
    "build_design_job_result",
    "build_resolved_design_job_result",
    "design_job_payload_to_args",
]
