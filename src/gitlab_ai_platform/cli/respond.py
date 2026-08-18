"""`WAITING_HUMAN`状態のJobへ人間の回答を取り込み、再開・完了させる(M4-5、`respond`サブコマンド)。

方針(M4-5 [#111](https://github.com/AtsushiNi/gitlab-ai-platform/issues/111)、
`docs/adr/0028-waiting-human-answer-integration.md`):

- `review`/`watch`が使う非リース方式(`JobRepository.update_status`)の経路を再利用する。
  `WAITING_HUMAN`はそもそも`claim`(リース方式)の対象外の状態のため(ADR-0026)、
  `RunnerDispatcher`(`worker`)ではなくこちらの経路を使うのが自然
- `run_respond`(合成ルート)/`respond_to_job`(パイプライン本体)という、他のサブコマンド
  (`cli/single_run.py`等)と同じ分離パターンを踏襲する
- 質問提示・回答収集(`collect_answers`)はJobの状態を一切変更しない。全質問への回答が
  揃ってから`WAITING_HUMAN → RUNNING`へ遷移させ、`RUNNING → DONE`まで一気に進める。
  回答収集中(人間が考えている間)にCtrl+C等で中断されてもJobは`WAITING_HUMAN`のまま
  変化しないため、`respond`をそのまま再実行すればよい。`RUNNING`遷移後に例外
  (`KeyboardInterrupt`含む)が発生した場合は`FAILED`へ倒し、`RUNNING`のまま孤立させない
  (ADR-0028「決定」参照)

M4-6([#112](https://github.com/AtsushiNi/gitlab-ai-platform/issues/112)、ADR-0029)で、
`design`種別Jobも`WAITING_HUMAN`を使うようになったため、回答の統合先(`_RESULT_RESOLVERS`)を
`job_type`ごとに切り替えられるようにした(ADR-0028「影響」が予告していた拡張)。M4-7
([#113](https://github.com/AtsushiNi/gitlab-ai-platform/issues/113)、ADR-0030)で`plan`種別Jobも
同じ枠組みに追加した。M4-8([#114](https://github.com/AtsushiNi/gitlab-ai-platform/issues/114)、
ADR-0033)で`implement`種別Jobも追加し、ADR-0028が「今後の課題」としていたJobType予約4種
すべてが揃った。`_RESULT_RESOLVERS`に含まれない種別を指定すると`InvalidJobTransitionError`を
送出する。

M4-10([#116](https://github.com/AtsushiNi/gitlab-ai-platform/issues/116)、ADR-0035)で、
`respond_to_job`に`DONE`遷移成功後に呼ぶ任意のフック`on_job_completed`を追加した。
`run_respond`(合成ルート)が`orchestrator.pipeline.advance_pipeline`を束縛したコールバックを
渡すことで、`WAITING_HUMAN`を経由した場合も(`RunnerDispatcher`の`complete`経路と同様に)
次フェーズへの連鎖が起きる。`respond_to_job`自身はこのコールバックの中身(フェーズ順序)を
知らない。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..config import Config
from ..design.job import build_resolved_design_job_result
from ..implement.job import build_resolved_implement_job_result
from ..issue_analysis.job import build_resolved_issue_analysis_job_result
from ..job import Job, JobRepository, JobStatus, JobType, SqliteJobRepository
from ..job.errors import InvalidJobTransitionError, JobError, JobNotFoundError
from ..logging_ import get_logger
from ..orchestrator.pipeline import advance_pipeline_hook
from ..plan.job import build_resolved_plan_job_result

_logger = get_logger(__name__)

# `WAITING_HUMAN`後の回答統合関数(job_typeごとに`result`の構造が異なるため)。
_RESULT_RESOLVERS: dict[
    JobType, Callable[[Mapping[str, Any], Sequence[str]], dict[str, Any]]
] = {
    JobType.ISSUE_ANALYSIS: build_resolved_issue_analysis_job_result,
    JobType.DESIGN: build_resolved_design_job_result,
    JobType.PLAN: build_resolved_plan_job_result,
    JobType.IMPLEMENT: build_resolved_implement_job_result,
}


def collect_answers(
    questions: list[dict[str, Any]],
    *,
    ask: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> list[str]:
    """`questions`(`Job.result["questions"]`)を1件ずつ提示し、人間の回答を集める。

    Job Repositoryの状態は一切変更しない(呼び出し元`respond_to_job`が、この関数から
    正常に戻った後にだけ`WAITING_HUMAN → RUNNING`遷移を行う)。
    """
    answers: list[str] = []
    total = len(questions)
    for index, question in enumerate(questions, start=1):
        output(f"[{index}/{total}] ({question['severity']}) {question['question']}")
        answers.append(ask("回答: "))
    return answers


def list_waiting_human_jobs(
    job_repo: JobRepository, *, output: Callable[[str], None] = print
) -> list[Job]:
    """`WAITING_HUMAN`状態のJobを一覧表示する(`job_id`省略時の確認用途)。"""
    jobs = job_repo.list_by_status(JobStatus.WAITING_HUMAN)
    if not jobs:
        output("WAITING_HUMAN状態のJobはありません")
        return jobs

    output(f"WAITING_HUMAN状態のJob: {len(jobs)}件")
    for job in jobs:
        result = job.result or {}
        project = result.get("project", "?")
        issue_iid = result.get("issue_iid", "?")
        n_questions = len(result.get("questions", []))
        output(f"  {job.id}  {project} #{issue_iid}  質問{n_questions}件")
    output(
        "回答するには対象のjob_idを指定して再実行してください: "
        "gitlab-ai-platform respond <job_id>"
    )
    return jobs


def respond_to_job(
    job_repo: JobRepository,
    job: Job,
    *,
    ask: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    on_job_completed: Callable[[Job], None] | None = None,
) -> Job:
    """`WAITING_HUMAN`のJobに回答を取り込み、`RUNNING`を経て`DONE`へ遷移させる(ADR-0028)。

    質問提示・回答収集(`collect_answers`)はJobの状態を一切変更しない。`WAITING_HUMAN →
    RUNNING`遷移後に例外(`KeyboardInterrupt`含む)が発生した場合は`FAILED`へ更新してから
    元の例外を再送出し、`RUNNING`のまま放置しない。

    回答の統合先(`result`の組み立て方)は`job.job_type`に応じて`_RESULT_RESOLVERS`から
    選ぶ(M4-6、ADR-0029)。`run_respond`が事前に対応種別であることを確認済みの前提で呼ぶため、
    ここでの`KeyError`は呼び出し側の実装ミスとして扱う。

    `on_job_completed`(M4-10, ADR-0035)は`DONE`遷移が成功した後にだけ呼ぶ。フェーズ連鎖の
    失敗は、既に成功したこのJobの`DONE`確定を巻き戻す理由にはならないため、例外は`FAILED`
    フォールバックの対象にはせずログのみに留める。
    """
    questions = (job.result or {}).get("questions", [])
    answers = collect_answers(questions, ask=ask, output=output)
    resolve_result = _RESULT_RESOLVERS[job.job_type]

    job = job_repo.update_status(job.id, JobStatus.RUNNING)
    try:
        resolved_result = resolve_result(job.result or {}, answers)
        done_job = job_repo.update_status(
            job.id, JobStatus.DONE, result=resolved_result
        )
    except BaseException as exc:
        # KeyboardInterrupt(Ctrl+C)も含め、RUNNINGへ遷移させた後の失敗はJobを孤立させない
        # よう明示的にFAILEDへ倒す(ADR-0028「決定」)。FAILEDへの更新自体が失敗しても
        # (DB接続不良等)、元の例外を優先してそのまま再送出する
        try:
            job_repo.update_status(job.id, JobStatus.FAILED, error=str(exc))
        except JobError as update_exc:
            _logger.error(
                "respond.failed_status_update_failed",
                extra={"job_id": job.id, "error": str(update_exc)},
            )
        raise

    if on_job_completed is not None:
        try:
            on_job_completed(done_job)
        except Exception as exc:  # noqa: BLE001 - フック失敗で今回のDONE確定を巻き戻さない
            _logger.error(
                "respond.on_job_completed_failed",
                extra={"job_id": done_job.id, "error": str(exc)},
            )
    return done_job


def run_respond(
    config: Config,
    *,
    job_id: str | None = None,
    ask: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> Job | None:
    """合成ルート。`SqliteJobRepository`を組み立て、一覧表示または1件の回答取り込みを行う。

    `job_id`省略時は`list_waiting_human_jobs`で一覧表示するだけで`None`を返す(状態変更なし)。
    `job_id`指定時は対象Jobを取得し、`WAITING_HUMAN`状態であることを確認してから
    `respond_to_job`に委譲する。対象Jobが存在しない場合は`JobNotFoundError`、
    `WAITING_HUMAN`状態でない場合は`InvalidJobTransitionError`を送出する。
    """
    job_repo = SqliteJobRepository(config.job_db_path)
    try:
        if job_id is None:
            list_waiting_human_jobs(job_repo, output=output)
            return None

        job = job_repo.get(job_id)
        if job is None:
            raise JobNotFoundError(f"job_id={job_id!r} のJobが見つかりません")
        if job.status is not JobStatus.WAITING_HUMAN:
            raise InvalidJobTransitionError(
                f"job_id={job_id!r} はWAITING_HUMAN状態ではありません"
                f"(現在の状態: {job.status.value!r})"
            )
        if job.job_type not in _RESULT_RESOLVERS:
            # 対応済みJob種別は_RESULT_RESOLVERSに登録されているもののみ(M4-6時点で
            # issue-analysis/design、ADR-0029)。未対応種別を誤って指定した場合に
            # 分かりやすく失敗させる
            supported = ", ".join(sorted(t.value for t in _RESULT_RESOLVERS))
            raise InvalidJobTransitionError(
                f"job_id={job_id!r} のjob_type={job.job_type.value!r} は"
                f"respondがまだ対応していません({supported}のみ対応)"
            )

        return respond_to_job(
            job_repo,
            job,
            ask=ask,
            output=output,
            # issue-analysis→design→plan→implement→pushの連鎖(M4-10, ADR-0035)。
            # `job_repo`を束縛するだけで、`respond_to_job`自体にはフェーズ順序を渡さない
            on_job_completed=advance_pipeline_hook(job_repo),
        )
    finally:
        job_repo.close()


__all__ = [
    "collect_answers",
    "list_waiting_human_jobs",
    "respond_to_job",
    "run_respond",
]
