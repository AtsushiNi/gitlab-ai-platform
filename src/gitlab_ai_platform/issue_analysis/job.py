"""issue-analysis種別のJob(`job/`)のresult構造。

方針(M4-3 [#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109)、ADR-0026):

- payloadの組み立て・分解(`build_issue_analysis_job_payload`/`issue_analysis_job_payload_to_args`)
  は`poller/issue_poller.py`(M4-1, [#107](https://github.com/AtsushiNi/gitlab-ai-platform/issues/107))
  に既に実装済みのため、ここでは再利用し重複実装しない。ここで定義するのは`result`の組み立てのみ。
- `review/job.py`の`build_review_job_result`と同じ役割だが、issue-analysisは`complete`(通常の
  完了)・`wait_for_human`(人間の確認待ち)のどちらの結果にも同じ構造の辞書を使う(ADR-0026)。
  `questions`(`ask_judgments`の結果)は`wait_for_human`のときのみ非空になり、`complete`のときは
  必ず空配列になる(`requires_human`がFalseの場合のみ`complete`を呼ぶため)。
"""

from __future__ import annotations

from collections.abc import Sequence
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


__all__ = ["build_issue_analysis_job_result"]
