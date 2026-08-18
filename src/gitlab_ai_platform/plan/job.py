"""plan種別のJob(`job/`)のpayload/result構造。

方針(M4-7 [#113](https://github.com/AtsushiNi/gitlab-ai-platform/issues/113)、ADR-0030):

- `design`と同じく、実装計画フェーズを投入する自動化された検出器(Poller相当)はまだ
  存在しない(オーケストレーション本体はM4-10のスコープ)。そのため`build_plan_job_payload`/
  `plan_job_payload_to_args`は`poller/`ではなく本パッケージに置く。M4-10実装時に
  「design完了 → plan投入」の橋渡しをするコード(呼び出し側)から利用される想定
- payloadは設計フェーズ完了時の`Job.result`(`design.build_design_job_result`/
  `build_resolved_design_job_result`が組み立てたもの)から`design_document`/
  `assumed_uncertainties`のみを転記する(`design/job.py`の`build_design_job_payload`が
  要求分析フェーズの`Job.result`から必要なフィールドだけを転記するのと同じパターン)。
  Job同士の直接参照(plan JobがJobRepository経由でdesign Jobを読みに行く)はADR-0022の
  設計を破るため避け、呼び出し側(投入者)が必要なフィールドをpayloadとしてそのまま渡す
- `design/job.py`の`build_design_job_result`・`build_resolved_design_job_result`と同じ役割・
  同じ構造パターン(`complete`/`wait_for_human`共通の結果組み立て、`WAITING_HUMAN`後の
  回答統合)を実装計画フェーズに横展開したもの
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..orchestrator import UncertaintyJudgment, ask_judgments, assume_judgments
from .types import PlanInput, PlanResult, PlanTask


def build_plan_job_payload(
    project: str, issue_iid: int, design_result: Mapping[str, Any]
) -> dict[str, Any]:
    """`plan`種別Jobの`payload`を組み立てる。

    `design_result`は設計フェーズ完了時の`Job.result`(`design.build_design_job_result`/
    `build_resolved_design_job_result`が組み立てたもの)。`design_document`/
    `assumed_uncertainties`のみを転記し、`questions`/`resolved_questions`のようなplanには
    不要なフィールドは含めない(`design.build_design_job_payload`と同じ絞り込み方針)。
    """
    return {
        "project": project,
        "issue_iid": issue_iid,
        "design_document": design_result.get("design_document", ""),
        "assumed_uncertainties": list(design_result.get("assumed_uncertainties", ())),
    }


def plan_job_payload_to_args(
    payload: Mapping[str, Any],
) -> tuple[str, int, PlanInput]:
    """`plan`種別Jobの`payload`から`(project, issue_iid, PlanInput)`を取り出す。

    `cli/dispatcher.py`の`build_plan_handler`が利用する。
    """
    assumed_uncertainties = tuple(
        _format_assumed_uncertainty(item)
        for item in payload.get("assumed_uncertainties", ())
    )
    plan_input = PlanInput(
        design_document=payload.get("design_document", ""),
        assumed_uncertainties=assumed_uncertainties,
    )
    return payload["project"], payload["issue_iid"], plan_input


def _format_assumed_uncertainty(item: Mapping[str, Any]) -> str:
    """`{"question", "severity", "assumption"}`形式の辞書を、プロンプトに埋め込みやすい
    1行の文字列(`"question → assumption"`)に整形する(`design/job.py`と同じロジック)。"""
    return f"{item['question']} → {item['assumption']}"


def build_plan_job_result(
    project: str,
    issue_iid: int,
    plan: PlanResult,
    judgments: Sequence[UncertaintyJudgment],
) -> dict[str, Any]:
    """実装計画フェーズのJob `result`を組み立てる(`complete`/`wait_for_human`共通)。

    - `plan_document`/`tasks`: `plan`(Claude Codeの実装計画結果)から転記
    - `assumed_uncertainties`: `assume_judgments`(`MINOR`かつ`ASSUME`判定の不明点)の一覧
    - `questions`: `ask_judgments`(`CRITICAL`かつ`ASK`判定の不明点)の一覧。空でなければ
      呼び出し側(`cli/dispatcher.py`)がJobを`WAITING_HUMAN`へ遷移させる(ADR-0026)
    """
    return {
        "project": project,
        "issue_iid": issue_iid,
        "plan_document": plan.plan_document,
        "tasks": [_task_to_dict(t) for t in plan.tasks],
        "assumed_uncertainties": [
            _judgment_to_dict(j) for j in assume_judgments(judgments)
        ],
        "questions": [_judgment_to_dict(j) for j in ask_judgments(judgments)],
    }


def _task_to_dict(task: PlanTask) -> dict[str, Any]:
    return {"title": task.title, "description": task.description}


def _judgment_to_dict(judgment: UncertaintyJudgment) -> dict[str, Any]:
    data: dict[str, Any] = {
        "question": judgment.uncertainty.question,
        "severity": judgment.uncertainty.severity.value,
    }
    if judgment.assumption_note is not None:
        data["assumption"] = judgment.assumption_note
    return data


def build_resolved_plan_job_result(
    result: Mapping[str, Any], answers: Sequence[str]
) -> dict[str, Any]:
    """`WAITING_HUMAN`の`result`に人間の回答を統合した新しい`result`を組み立てる。

    `design.build_resolved_design_job_result`と全く同じロジック(`result["questions"]`と同じ
    順序・件数の`answers`を受け取り、`resolved_questions`(`{"question", "severity", "answer"}`)へ
    変換するとともに`assumed_uncertainties`へ合流させる)。`plan_document`/`tasks`等の他フィールドは
    `**result`でそのまま引き継ぐため、plan固有の実装は不要。両フェーズで別々の関数として持つ
    理由はADR-0029「却下した選択肢」・ADR-0030「却下した選択肢」を参照(3つ目の重複時点で
    共通化を検討する方針)。

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
    "build_plan_job_payload",
    "build_plan_job_result",
    "build_resolved_plan_job_result",
    "plan_job_payload_to_args",
]
