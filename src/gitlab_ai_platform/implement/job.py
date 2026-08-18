"""implement種別のJob(`job/`)のpayload/result構造。

方針(M4-8 [#114](https://github.com/AtsushiNi/gitlab-ai-platform/issues/114)、ADR-0033):

- `plan`と同じく、実装フェーズを投入する自動化された検出器(Poller相当)はまだ存在しない
  (オーケストレーション本体はM4-10のスコープ)。そのため`build_implement_job_payload`/
  `implement_job_payload_to_args`は`poller/`ではなく本パッケージに置く。M4-10実装時に
  「plan完了 → implement投入」の橋渡しをするコード(呼び出し側)から利用される想定
- payloadは実装計画フェーズ完了時の`Job.result`(`plan.build_plan_job_result`/
  `build_resolved_plan_job_result`が組み立てたもの)から`plan_document`/`tasks`/
  `assumed_uncertainties`のみを転記する(`plan/job.py`の`build_plan_job_payload`が
  設計フェーズの`Job.result`から必要なフィールドだけを転記するのと同じパターン)。
- `design`/`plan`と異なり、実装フェーズは実際にGitLabへのbranch作成・ローカルcommitを
  行う(ADR-0033)ため、`build_implement_job_result`は`plan`/`design`が持たなかった
  `commit_sha`/`remote_branch`/`local_branch`/`worktree_path`を追加で持つ。
  `commit_sha`はM4-9(push フェーズ)が「何を push するか」を特定するために必須の情報。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..orchestrator import UncertaintyJudgment, ask_judgments, assume_judgments
from ..plan.types import PlanTask
from .types import ImplementInput, ImplementResult


def build_implement_job_payload(
    project: str, issue_iid: int, plan_result: Mapping[str, Any]
) -> dict[str, Any]:
    """`implement`種別Jobの`payload`を組み立てる。

    `plan_result`は実装計画フェーズ完了時の`Job.result`(`plan.build_plan_job_result`/
    `build_resolved_plan_job_result`が組み立てたもの)。`plan_document`/`tasks`/
    `assumed_uncertainties`のみを転記し、`questions`/`resolved_questions`のようなimplementには
    不要なフィールドは含めない(`plan.build_plan_job_payload`と同じ絞り込み方針)。
    """
    return {
        "project": project,
        "issue_iid": issue_iid,
        "plan_document": plan_result.get("plan_document", ""),
        "tasks": list(plan_result.get("tasks", ())),
        "assumed_uncertainties": list(plan_result.get("assumed_uncertainties", ())),
    }


def implement_job_payload_to_args(
    payload: Mapping[str, Any],
) -> tuple[str, int, ImplementInput]:
    """`implement`種別Jobの`payload`から`(project, issue_iid, ImplementInput)`を取り出す。

    `cli/dispatcher.py`の`build_implement_handler`が利用する。
    """
    assumed_uncertainties = tuple(
        _format_assumed_uncertainty(item)
        for item in payload.get("assumed_uncertainties", ())
    )
    tasks = tuple(_task_from_dict(item) for item in payload.get("tasks", ()))
    implement_input = ImplementInput(
        plan_document=payload.get("plan_document", ""),
        tasks=tasks,
        assumed_uncertainties=assumed_uncertainties,
    )
    return payload["project"], payload["issue_iid"], implement_input


def _format_assumed_uncertainty(item: Mapping[str, Any]) -> str:
    """`{"question", "severity", "assumption"}`形式の辞書を、プロンプトに埋め込みやすい
    1行の文字列(`"question → assumption"`)に整形する(`design/plan`の`job.py`と同じロジック)。"""
    return f"{item['question']} → {item['assumption']}"


def _task_from_dict(item: Mapping[str, Any]) -> PlanTask:
    return PlanTask(title=item["title"], description=item["description"])


def build_implement_job_result(
    project: str,
    issue_iid: int,
    *,
    implement_result: ImplementResult,
    commit_sha: str,
    remote_branch: str,
    local_branch: str,
    worktree_path: str,
    judgments: Sequence[UncertaintyJudgment],
) -> dict[str, Any]:
    """実装フェーズのJob `result`を組み立てる(`complete`/`wait_for_human`共通)。

    - `summary`/`commit_message`: `implement_result`(Claude Codeの実装結果)から転記
    - `commit_sha`: 実行後に確認したworktreeの実際のHEAD commit sha(構造的に確認済みの値。
      `implement_result.commit_message`と異なりClaude Codeの自己申告ではない)
    - `remote_branch`: GitLab上に作成した実装用branch名(M4-9のpush対象)
    - `local_branch`/`worktree_path`: worktreeの識別情報(デバッグ・追跡用)
    - `assumed_uncertainties`: `assume_judgments`(`MINOR`かつ`ASSUME`判定の不明点)の一覧
    - `questions`: `ask_judgments`(`CRITICAL`かつ`ASK`判定の不明点)の一覧。空でなければ
      呼び出し側(`cli/dispatcher.py`)がJobを`WAITING_HUMAN`へ遷移させる(ADR-0026)
    """
    return {
        "project": project,
        "issue_iid": issue_iid,
        "summary": implement_result.summary,
        "commit_message": implement_result.commit_message,
        "commit_sha": commit_sha,
        "remote_branch": remote_branch,
        "local_branch": local_branch,
        "worktree_path": worktree_path,
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


def build_resolved_implement_job_result(
    result: Mapping[str, Any], answers: Sequence[str]
) -> dict[str, Any]:
    """`WAITING_HUMAN`の`result`に人間の回答を統合した新しい`result`を組み立てる。

    `design.build_resolved_design_job_result`/`plan.build_resolved_plan_job_result`と
    全く同じロジック(`result["questions"]`と同じ順序・件数の`answers`を受け取り、
    `resolved_questions`(`{"question", "severity", "answer"}`)へ変換するとともに
    `assumed_uncertainties`へ合流させる)。`commit_sha`/`remote_branch`等の他フィールドは
    `**result`でそのまま引き継ぐため、implement固有の実装は不要。両フェーズで別々の関数として
    持つ理由はADR-0029/0030「却下した選択肢」を参照(3つ目の重複時点で共通化を検討する方針を
    予告しており、本Issueが実質その3つ目にあたるが、既存2つの公開契約を変更する理由が
    無いため引き続き見送る)。

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
    "build_implement_job_payload",
    "build_implement_job_result",
    "build_resolved_implement_job_result",
    "implement_job_payload_to_args",
]
