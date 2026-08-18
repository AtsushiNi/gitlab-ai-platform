"""issue-analysis種別のJob(`job/`)のresult構造。

方針(M4-3 [#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109)、ADR-0026):

- payloadの組み立て・分解(`build_issue_analysis_job_payload`/`issue_analysis_job_payload_to_args`)
  は`poller/issue_poller.py`(M4-1, [#107](https://github.com/AtsushiNi/gitlab-ai-platform/issues/107))
  に既に実装済みのため、ここでは再利用し重複実装しない。ここで定義するのは`result`の組み立てのみ。
- `review/job.py`の`build_review_job_result`と同じ役割だが、issue-analysisは`complete`(通常の
  完了)・`wait_for_human`(人間の確認待ち)のどちらの結果にも同じ構造の辞書を使う(ADR-0026)。
  `questions`(`ask_judgments`の結果)は`wait_for_human`のときのみ非空になり、`complete`のときは
  必ず空配列になる(`requires_human`がFalseの場合のみ`complete`を呼ぶため)。

M4-5([#111](https://github.com/AtsushiNi/gitlab-ai-platform/issues/111)、
[ADR-0028](../../../docs/adr/0028-waiting-human-answer-integration.md)):

- `build_resolved_issue_analysis_job_result`は、`WAITING_HUMAN`で人間に提示した`questions`への
  回答を統合した新しい`result`を組み立てる。`cli/respond.py`の`respond_to_job`が、
  `WAITING_HUMAN → RUNNING`遷移後・`DONE`遷移前に呼び出す。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..orchestrator import UncertaintyJudgment, ask_judgments, assume_judgments
from .types import RequirementAnalysis


def build_issue_analysis_job_result(
    project: str,
    issue_iid: int,
    analysis: RequirementAnalysis,
    judgments: Sequence[UncertaintyJudgment],
) -> dict[str, Any]:
    """要求分析フェーズのJob `result`を組み立てる(`complete`/`wait_for_human`共通)。

    - `requirements`/`acceptance_criteria`/`assumptions`: `analysis`(Claude Codeの分析結果)から転記
    - `assumed_uncertainties`: `assume_judgments`(`MINOR`かつ`ASSUME`判定の不明点)の一覧。
      Claude Codeが分析時点で述べた`assumptions`(前提)とは別物で、「元は不明点だったが
      仮定を置いて処理を継続することにした項目」を表す(M4-9でMR本文の「○○と仮定して
      実装した」という記述の元として使う想定)
    - `questions`: `ask_judgments`(`CRITICAL`かつ`ASK`判定の不明点)の一覧。空でなければ
      呼び出し側(`cli/dispatcher.py`)がJobを`WAITING_HUMAN`へ遷移させる(ADR-0026)
    """
    return {
        "project": project,
        "issue_iid": issue_iid,
        "requirements": list(analysis.requirements),
        "acceptance_criteria": list(analysis.acceptance_criteria),
        "assumptions": list(analysis.assumptions),
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


def build_resolved_issue_analysis_job_result(
    result: Mapping[str, Any], answers: Sequence[str]
) -> dict[str, Any]:
    """`WAITING_HUMAN`の`result`に人間の回答を統合した新しい`result`を組み立てる(M4-5, ADR-0028)。

    `result["questions"]`(`build_issue_analysis_job_result`が組み立てたもの)と同じ順序・
    同じ件数の`answers`を受け取る想定。各質問を`resolved_questions`
    (`{"question", "severity", "answer"}`)へ変換して記録するとともに、`assumed_uncertainties`
    (ASSUME判定の不明点)と同じ形の項目として合流させる(`answer`は`assumption`キーに転記)。
    M4-9がMR本文に「前提とした情報」を記載する際、`assumed_uncertainties`だけを見れば
    ASSUME/ASK→回答のどちらも拾える(ADR-0028「決定」参照)。`questions`は解決済みのため
    常に空配列にする。

    `answers`の件数が`result["questions"]`と一致しない場合は`ValueError`を送出する
    (呼び出し側`respond_to_job`の実装ミスを早期に検知するための防御的チェック)。
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
    "build_issue_analysis_job_result",
    "build_resolved_issue_analysis_job_result",
]
