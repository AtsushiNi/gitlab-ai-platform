"""Issue→MRパイプラインのフェーズ間状態遷移(M4-10 [#116](https://github.com/AtsushiNi/gitlab-ai-platform/issues/116)、
[ADR-0035](../../../docs/adr/0035-pipeline-orchestration.md))。

方針:

- `issue-analysis → design → plan → implement → push`という5フェーズの連鎖を、`advance_pipeline`
  という1つの純粋寄りの関数(`JobRepository`への`enqueue`という副作用のみ持つ)で表現する。
  `JobType`ごとの「次フェーズ」対応表(`_NEXT_JOB_TYPE`)を見て、次フェーズが無ければ(`review`/
  `push`)`None`を返すだけの単純な仕組みにした
- 各フェーズの`build_<次フェーズ>_job_payload`(`design`/`plan`/`implement`/`push`各パッケージに
  既存)をそのまま呼び出す。新しい変換ロジックはここには書かない(Issue #116本文の想定通り)
- `RunnerDispatcher._process`(`cli/dispatcher.py`)がJobを`complete`した経路、`respond_to_job`
  (`cli/respond.py`)がJobを`WAITING_HUMAN`から`DONE`にした経路の両方から、同じこの関数を呼ぶ
  (ADR-0035「決定」参照)。どちらも「連鎖させてよいのはJobが`DONE`になった場合のみ」という
  境界を、呼び出しタイミング自体(`complete`成功後/`DONE`遷移成功後にのみ呼ぶ)で表現しており、
  本関数内の状態チェックは呼び出し側の実装ミスに対する防御にすぎない
- **意図的に本パッケージの他モジュール(`orchestrator/__init__.py`)からは再エクスポートしない。**
  本モジュールは`design`/`plan`/`implement`/`push`各パッケージ(の`job.py`)をimportするが、
  それらのパッケージは`design/job.py`等が`from ..orchestrator import UncertaintyJudgment, ...`と
  いう形で`orchestrator`パッケージ本体に依存している。`orchestrator/__init__.py`が本モジュールを
  importしてしまうと、`design`等のどのモジュールが最初にimportされるかという実行時の順序に
  依存して循環importが解決できたりできなかったりする脆い状態になる(ADR-0035「却下した選択肢」
  参照)。呼び出し側は`from gitlab_ai_platform.orchestrator.pipeline import advance_pipeline`と
  明示的にサブモジュールをimportする
- Issue単位で「どのフェーズまで進んだか」を横断的に追跡する専用の索引は追加しない。各フェーズの
  Job `payload`/`result`が既に`project`/`issue_iid`を持っており、`JobRepository`の既存機能
  (`get`/`list_by_status`等)で辿れるため(ADR-0035「決定」、過剰な作り込みを避ける方針)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..design import build_design_job_payload
from ..implement import build_implement_job_payload
from ..job.errors import JobError
from ..job.protocol import Job, JobRepository, JobStatus, JobType
from ..logging_ import get_logger
from ..plan import build_plan_job_payload
from ..push import build_push_job_payload

_logger = get_logger(__name__)

# JobType→次フェーズのJobTypeの対応表。無人実行トラック5フェーズの連鎖順序そのもの。
# `review`(MRレビュー、別トラック)と`push`(最終フェーズ)は次が無いため含めない
_NEXT_JOB_TYPE: dict[JobType, JobType] = {
    JobType.ISSUE_ANALYSIS: JobType.DESIGN,
    JobType.DESIGN: JobType.PLAN,
    JobType.PLAN: JobType.IMPLEMENT,
    JobType.IMPLEMENT: JobType.PUSH,
}


def advance_pipeline(job_repo: JobRepository, completed_job: Job) -> Job | None:
    """完了したJobを受け取り、パイプラインの次フェーズのJobを投入する。

    以下のいずれかに該当する場合は何もせず`None`を返す(例外は送出しない):

    - `completed_job.status`が`DONE`でない場合(`WAITING_HUMAN`・`FAILED`を連鎖させない境界。
      呼び出し側は`complete`成功後/`WAITING_HUMAN → DONE`成功後にのみ本関数を呼ぶべきだが、
      本チェックは呼び出し側の実装ミスに対する防御)
    - `completed_job.job_type`に次フェーズが無い場合(`review`/`push`)
    - `completed_job.result`に次フェーズのpayload組み立てに必要なフィールドが欠けている場合
      (`project`/`issue_iid`が無い、または`push`投入に必要な`commit_sha`等が無い)
    - 次フェーズJobの`enqueue`が`JobError`で失敗した場合(Job Queue自体の障害。呼び出し元の
      Job自体は既に`DONE`として確定済みのため、この失敗でその確定を巻き戻す必要はない)
    """
    if completed_job.status is not JobStatus.DONE:
        return None

    next_job_type = _NEXT_JOB_TYPE.get(completed_job.job_type)
    if next_job_type is None:
        return None

    payload = _build_next_job_payload(next_job_type, completed_job)
    if payload is None:
        _logger.warning(
            "orchestrator.advance_pipeline_skipped_missing_fields",
            extra={
                "job_id": completed_job.id,
                "job_type": completed_job.job_type.value,
                "next_job_type": next_job_type.value,
            },
        )
        return None

    try:
        next_job = job_repo.enqueue(next_job_type, payload)
    except JobError as exc:
        _logger.error(
            "orchestrator.advance_pipeline_enqueue_failed",
            extra={
                "completed_job_id": completed_job.id,
                "completed_job_type": completed_job.job_type.value,
                "next_job_type": next_job_type.value,
                "error": str(exc),
            },
        )
        return None

    _logger.info(
        "orchestrator.advance_pipeline_enqueued",
        extra={
            "completed_job_id": completed_job.id,
            "completed_job_type": completed_job.job_type.value,
            "next_job_id": next_job.id,
            "next_job_type": next_job_type.value,
        },
    )
    return next_job


def advance_pipeline_hook(job_repo: JobRepository) -> Callable[[Job], None]:
    """`job_repo`を束縛した`advance_pipeline`を、`RunnerDispatcher`/`respond_to_job`の
    `on_job_completed: Callable[[Job], None]`契約に合わせて返す。

    `advance_pipeline`自体は`Job | None`を返す(呼び出し元が次フェーズJobを参照できるように)。
    `on_job_completed`は戻り値を使わない`Callable[[Job], None]`契約のため、単に
    `lambda completed: advance_pipeline(job_repo, completed)`と書くと戻り値の型が
    合わずmypyエラーになる。本関数はその変換のためだけの薄いヘルパー。
    """

    def _hook(completed_job: Job) -> None:
        advance_pipeline(job_repo, completed_job)

    return _hook


def _build_next_job_payload(
    next_job_type: JobType, completed_job: Job
) -> dict[str, Any] | None:
    """`completed_job`(直前フェーズ)から次フェーズJobの`payload`を組み立てる。

    各フェーズが既に用意している`build_<次フェーズ>_job_payload`をそのまま呼び出すだけで、
    ここで新しい変換ロジックは書かない(Issue #116本文の想定通り)。`project`/`issue_iid`が
    `completed_job.result`に無い、または`push`投入に必要なフィールドが`result`に無い場合は
    `KeyError`を`None`に変換して返す(呼び出し元の`advance_pipeline`が握りつぶす)。
    """
    result = completed_job.result or {}
    project = result.get("project")
    issue_iid = result.get("issue_iid")
    if project is None or issue_iid is None:
        return None

    try:
        if next_job_type is JobType.DESIGN:
            return build_design_job_payload(project, issue_iid, result)
        if next_job_type is JobType.PLAN:
            return build_plan_job_payload(project, issue_iid, result)
        if next_job_type is JobType.IMPLEMENT:
            return build_implement_job_payload(project, issue_iid, result)
        if next_job_type is JobType.PUSH:
            # `push`のみ、直前フェーズ(implement)のJob payload/result両方を必要とする
            # (`plan_document`はpayload側、`commit_sha`等はresult側。ADR-0034「論点2」)
            return build_push_job_payload(
                project,
                issue_iid,
                implement_payload=completed_job.payload,
                implement_result=result,
            )
    except KeyError:
        return None

    return None


__all__ = ["advance_pipeline", "advance_pipeline_hook"]
