from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gitlab_ai_platform.cli.respond import (
    collect_answers,
    list_waiting_human_jobs,
    respond_to_job,
    run_respond,
)
from gitlab_ai_platform.config import Config
from gitlab_ai_platform.job import Job, JobStatus, JobType, SqliteJobRepository
from gitlab_ai_platform.job.errors import InvalidJobTransitionError, JobNotFoundError

_PROJECT = "group/project"
_ISSUE_IID = 7
_WORKER_ID = "worker-1"


def _config(tmp_path: Path, **overrides) -> Config:
    kwargs = dict(
        gitlab_url="https://gitlab.example.com",
        gitlab_token="secret-token",
        projects=[_PROJECT],
        poll_interval_seconds=60,
        max_parallel=5,
        review_label="レビュー待ち",
        issue_label="AI実装",
        issue_ticket_db_path=":memory:",
        workspace_root=str(tmp_path / "workspace"),
        workspace_max_disk_mb=1000,
        runner_log_dir=str(tmp_path / "logs"),
        runner_timeout_seconds=1800,
        reviews_root=str(tmp_path / "reviews"),
        state_db_path=":memory:",
        store_backend="sqlite",
        store_postgres_host="localhost",
        store_postgres_port=5432,
        store_postgres_dbname="gitlab_ai_platform",
        store_postgres_user="gitlab_ai_platform",
        store_postgres_password="",
        job_db_path=str(tmp_path / "job.db"),
        webhook_enabled=False,
        webhook_host="127.0.0.1",
        webhook_port=8088,
        webhook_path="/webhook",
        webhook_secret_token="",
        api_host="127.0.0.1",
        api_port=8090,
        api_token="",
    )
    kwargs.update(overrides)
    return Config.from_raw(**kwargs)


_QUESTIONS = [
    {"question": "パスワード再設定フローも対象ですか?", "severity": "critical"},
    {"question": "対象ロールは管理者のみですか?", "severity": "critical"},
]


def _result(**overrides) -> dict:
    kwargs = dict(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        requirements=["要求1"],
        acceptance_criteria=["条件1"],
        assumptions=[],
        assumed_uncertainties=[],
        questions=list(_QUESTIONS),
    )
    kwargs.update(overrides)
    return kwargs


def _design_result(**overrides) -> dict:
    kwargs = dict(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        design_document="# 責務\n...",
        assumed_uncertainties=[],
        questions=list(_QUESTIONS),
    )
    kwargs.update(overrides)
    return kwargs


def _plan_result(**overrides) -> dict:
    kwargs = dict(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        plan_document="# 概要\n...",
        tasks=[{"title": "タスク1", "description": "内容1"}],
        assumed_uncertainties=[],
        questions=list(_QUESTIONS),
    )
    kwargs.update(overrides)
    return kwargs


def _implement_result(**overrides) -> dict:
    kwargs = dict(
        project=_PROJECT,
        issue_iid=_ISSUE_IID,
        summary="タスク1を実装した",
        commit_message="タスク1を実装",
        commit_sha="after-sha",
        remote_branch=f"ai/issue-{_ISSUE_IID}",
        local_branch=f"issue-{_ISSUE_IID}",
        worktree_path="/tmp/workspace/issue-7",
        assumed_uncertainties=[],
        questions=list(_QUESTIONS),
    )
    kwargs.update(overrides)
    return kwargs


def _make_waiting_human_job(
    job_db_path: str,
    *,
    result: dict | None = None,
    job_type: JobType = JobType.ISSUE_ANALYSIS,
) -> Job:
    """`SqliteJobRepository`経由でWAITING_HUMAN状態のJobを1件作る(テスト用セットアップ)。

    `wait_for_human`はリース方式のため、`enqueue`→`claim`→`wait_for_human`という
    実際の`RunnerDispatcher`と同じ手順を踏む。
    """
    repo = SqliteJobRepository(job_db_path)
    try:
        job = repo.enqueue(job_type, {"project": _PROJECT, "issue_iid": _ISSUE_IID})
        job = repo.claim(_WORKER_ID)
        assert job is not None
        return repo.wait_for_human(
            job.id, _WORKER_ID, result=result if result is not None else _result()
        )
    finally:
        repo.close()


# --- collect_answers ---


def test_collect_answers_prompts_each_question_and_returns_answers_in_order():
    printed: list[str] = []
    asked: list[str] = []

    def _ask(prompt: str) -> str:
        asked.append(prompt)
        return f"回答{len(asked)}"

    answers = collect_answers(_QUESTIONS, ask=_ask, output=printed.append)

    assert answers == ["回答1", "回答2"]
    assert len(asked) == 2
    assert "パスワード再設定フローも対象ですか?" in printed[0]
    assert "critical" in printed[0]


def test_collect_answers_with_no_questions_returns_empty_list():
    assert collect_answers([], ask=lambda _: "unused", output=lambda _: None) == []


# --- list_waiting_human_jobs ---


def test_list_waiting_human_jobs_reports_none_when_empty():
    repo = SqliteJobRepository(":memory:")
    printed: list[str] = []
    try:
        jobs = list_waiting_human_jobs(repo, output=printed.append)

        assert jobs == []
        assert any("ありません" in line for line in printed)
    finally:
        repo.close()


def test_list_waiting_human_jobs_lists_project_and_question_count(tmp_path):
    job_db_path = str(tmp_path / "job.db")
    job = _make_waiting_human_job(job_db_path)

    repo = SqliteJobRepository(job_db_path)
    printed: list[str] = []
    try:
        jobs = list_waiting_human_jobs(repo, output=printed.append)

        assert [j.id for j in jobs] == [job.id]
        assert any(job.id in line and "質問2件" in line for line in printed)
    finally:
        repo.close()


# --- respond_to_job ---


def test_respond_to_job_transitions_waiting_human_to_done_with_merged_result(
    tmp_path,
):
    job_db_path = str(tmp_path / "job.db")
    job = _make_waiting_human_job(job_db_path)

    repo = SqliteJobRepository(job_db_path)
    answers = iter(["回答1", "回答2"])
    try:
        done_job = respond_to_job(
            repo, job, ask=lambda _: next(answers), output=lambda _: None
        )

        assert done_job.status == JobStatus.DONE
        assert done_job.result["questions"] == []
        assert done_job.result["resolved_questions"] == [
            {**_QUESTIONS[0], "answer": "回答1"},
            {**_QUESTIONS[1], "answer": "回答2"},
        ]
        assert done_job.result["assumed_uncertainties"] == [
            {**_QUESTIONS[0], "assumption": "回答1"},
            {**_QUESTIONS[1], "assumption": "回答2"},
        ]
        # DBに永続化されていることも確認する
        assert repo.get(job.id).status == JobStatus.DONE
    finally:
        repo.close()


def test_respond_to_job_calls_on_job_completed_with_the_done_job(tmp_path):
    # M4-10, ADR-0035: DONE遷移成功後にon_job_completedへ更新後のJobを渡す
    job_db_path = str(tmp_path / "job.db")
    job = _make_waiting_human_job(job_db_path)

    repo = SqliteJobRepository(job_db_path)
    answers = iter(["回答1", "回答2"])
    completed_jobs: list[Job] = []
    try:
        done_job = respond_to_job(
            repo,
            job,
            ask=lambda _: next(answers),
            output=lambda _: None,
            on_job_completed=completed_jobs.append,
        )

        assert len(completed_jobs) == 1
        assert completed_jobs[0].id == done_job.id
        assert completed_jobs[0].status == JobStatus.DONE
    finally:
        repo.close()


def test_respond_to_job_does_not_call_on_job_completed_when_interrupted(tmp_path):
    # RUNNING遷移後に中断された場合(FAILEDへ倒す経路)はon_job_completedを呼ばない
    job_db_path = str(tmp_path / "job.db")
    job = _make_waiting_human_job(job_db_path)

    repo = SqliteJobRepository(job_db_path)
    completed_jobs: list[Job] = []

    def _ask(_: str) -> str:
        raise KeyboardInterrupt

    try:
        with pytest.raises(KeyboardInterrupt):
            respond_to_job(
                repo,
                job,
                ask=_ask,
                output=lambda _: None,
                on_job_completed=completed_jobs.append,
            )

        assert completed_jobs == []
    finally:
        repo.close()


def test_respond_to_job_on_job_completed_exception_does_not_fail_the_response(
    tmp_path,
):
    # フェーズ連鎖(M4-10)の失敗は、既に成功したこのJobのDONE確定を巻き戻す理由にはならない
    job_db_path = str(tmp_path / "job.db")
    job = _make_waiting_human_job(job_db_path)

    repo = SqliteJobRepository(job_db_path)
    answers = iter(["回答1", "回答2"])

    def _failing_hook(completed_job: Job) -> None:
        raise RuntimeError("advance_pipeline boom")

    try:
        done_job = respond_to_job(
            repo,
            job,
            ask=lambda _: next(answers),
            output=lambda _: None,
            on_job_completed=_failing_hook,
        )

        assert done_job.status == JobStatus.DONE
        # DBにもDONEとして永続化されたまま(フックの失敗で巻き戻らない)
        assert repo.get(job.id).status == JobStatus.DONE
    finally:
        repo.close()


def test_respond_to_job_interrupted_during_answer_collection_leaves_job_untouched(
    tmp_path,
):
    # 回答収集中(input()待ち)の中断はJobの状態を一切変更してはいけない
    # (WAITING_HUMANのまま残り、respondを再実行できる)
    job_db_path = str(tmp_path / "job.db")
    job = _make_waiting_human_job(job_db_path)

    repo = SqliteJobRepository(job_db_path)

    def _ask(_: str) -> str:
        raise KeyboardInterrupt

    try:
        with pytest.raises(KeyboardInterrupt):
            respond_to_job(repo, job, ask=_ask, output=lambda _: None)

        assert repo.get(job.id).status == JobStatus.WAITING_HUMAN
    finally:
        repo.close()


class _FailingOnDoneJobRepository:
    """`update_status(DONE)`だけを失敗させる手書きフェイク(RUNNING遷移後の障害を再現する)。

    `respond_to_job`が使うメソッド(`update_status`)のみを満たす。
    """

    def __init__(self, job: Job, *, fail_exc: Exception) -> None:
        self._job = job
        self._fail_exc = fail_exc
        self.update_status_calls: list[
            tuple[str, JobStatus, dict | None, str | None]
        ] = []

    def update_status(self, job_id, status, result=None, error=None):
        self.update_status_calls.append((job_id, status, result, error))
        if status is JobStatus.DONE:
            raise self._fail_exc
        self._job = dataclasses.replace(self._job, status=status)
        return self._job


def test_respond_to_job_falls_back_to_failed_when_completion_raises_after_running():
    job = Job(
        id="job-1",
        job_type=JobType.ISSUE_ANALYSIS,
        status=JobStatus.WAITING_HUMAN,
        payload={"project": _PROJECT, "issue_iid": _ISSUE_IID},
        result=_result(questions=[_QUESTIONS[0]]),
        error=None,
        created_at=datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC),
    )
    boom = RuntimeError("boom")
    repo = _FailingOnDoneJobRepository(job, fail_exc=boom)

    with pytest.raises(RuntimeError, match="boom"):
        respond_to_job(repo, job, ask=lambda _: "回答1", output=lambda _: None)

    statuses = [call[1] for call in repo.update_status_calls]
    assert statuses == [JobStatus.RUNNING, JobStatus.DONE, JobStatus.FAILED]
    failed_call = repo.update_status_calls[-1]
    assert failed_call[3] == "boom"


# --- run_respond ---


def test_run_respond_without_job_id_lists_jobs_and_returns_none(tmp_path):
    config = _config(tmp_path)
    _make_waiting_human_job(config.job_db_path)
    printed: list[str] = []

    result = run_respond(config, output=printed.append)

    assert result is None
    assert any("WAITING_HUMAN" in line for line in printed)


def test_run_respond_with_unknown_job_id_raises_job_not_found(tmp_path):
    config = _config(tmp_path)
    SqliteJobRepository(config.job_db_path).close()  # DBファイルを作るだけ

    with pytest.raises(JobNotFoundError):
        run_respond(config, job_id="does-not-exist")


def test_run_respond_with_non_waiting_human_job_raises_invalid_transition(tmp_path):
    config = _config(tmp_path)
    repo = SqliteJobRepository(config.job_db_path)
    job = repo.enqueue(
        JobType.ISSUE_ANALYSIS, {"project": _PROJECT, "issue_iid": _ISSUE_IID}
    )
    repo.close()

    with pytest.raises(InvalidJobTransitionError):
        run_respond(config, job_id=job.id)


def test_run_respond_with_unsupported_job_type_raises_invalid_transition(tmp_path):
    config = _config(tmp_path)
    job = _make_waiting_human_job(
        config.job_db_path, job_type=JobType.REVIEW, result=_result()
    )

    with pytest.raises(InvalidJobTransitionError):
        run_respond(config, job_id=job.id)


def test_run_respond_with_valid_job_id_completes_the_job(tmp_path):
    config = _config(tmp_path)
    job = _make_waiting_human_job(config.job_db_path)
    answers = iter(["回答1", "回答2"])

    done_job = run_respond(
        config, job_id=job.id, ask=lambda _: next(answers), output=lambda _: None
    )

    assert done_job is not None
    assert done_job.status == JobStatus.DONE
    assert done_job.result["resolved_questions"][0]["answer"] == "回答1"


def test_run_respond_advances_pipeline_to_design_after_issue_analysis_done(tmp_path):
    # M4-10, ADR-0035: run_respond(合成ルート)がadvance_pipelineを実際に配線していることを
    # 検証する。enqueueはDB書き込みのみで外部サービス(GitLab/Claude Code)には繋がらないため、
    # モンキーパッチなしで実際の連鎖を確認できる
    config = _config(tmp_path)
    job = _make_waiting_human_job(config.job_db_path)  # 既定はissue-analysis種別
    answers = iter(["回答1", "回答2"])

    done_job = run_respond(
        config, job_id=job.id, ask=lambda _: next(answers), output=lambda _: None
    )
    assert done_job is not None
    assert done_job.status == JobStatus.DONE

    repo = SqliteJobRepository(config.job_db_path)
    try:
        pending_jobs = repo.list_by_status(JobStatus.PENDING)
        assert len(pending_jobs) == 1
        next_job = pending_jobs[0]
        assert next_job.job_type == JobType.DESIGN
        assert next_job.payload["project"] == _PROJECT
        assert next_job.payload["issue_iid"] == _ISSUE_IID
        assert next_job.payload["requirements"] == ["要求1"]
    finally:
        repo.close()


# --- design種別Job(M4-6, ADR-0029)---


def test_respond_to_job_supports_design_job_type(tmp_path):
    # design種別のWAITING_HUMAN Jobも、issue-analysisと同じ手順でDONEまで遷移できること
    # (_RESULT_RESOLVERSの一般化、ADR-0029)
    job_db_path = str(tmp_path / "job.db")
    job = _make_waiting_human_job(
        job_db_path, job_type=JobType.DESIGN, result=_design_result()
    )

    repo = SqliteJobRepository(job_db_path)
    answers = iter(["回答1", "回答2"])
    try:
        done_job = respond_to_job(
            repo, job, ask=lambda _: next(answers), output=lambda _: None
        )

        assert done_job.status == JobStatus.DONE
        assert done_job.result["design_document"] == "# 責務\n..."
        assert done_job.result["questions"] == []
        assert done_job.result["resolved_questions"] == [
            {**_QUESTIONS[0], "answer": "回答1"},
            {**_QUESTIONS[1], "answer": "回答2"},
        ]
    finally:
        repo.close()


def test_run_respond_with_design_job_type_completes_the_job(tmp_path):
    config = _config(tmp_path)
    job = _make_waiting_human_job(
        config.job_db_path, job_type=JobType.DESIGN, result=_design_result()
    )
    answers = iter(["回答1", "回答2"])

    done_job = run_respond(
        config, job_id=job.id, ask=lambda _: next(answers), output=lambda _: None
    )

    assert done_job is not None
    assert done_job.status == JobStatus.DONE
    assert done_job.result["resolved_questions"][0]["answer"] == "回答1"


# --- plan種別Job(M4-7, ADR-0030)---


def test_respond_to_job_supports_plan_job_type(tmp_path):
    # plan種別のWAITING_HUMAN Jobも、issue-analysis/designと同じ手順でDONEまで遷移できること
    # (_RESULT_RESOLVERSの一般化、ADR-0030)
    job_db_path = str(tmp_path / "job.db")
    job = _make_waiting_human_job(
        job_db_path, job_type=JobType.PLAN, result=_plan_result()
    )

    repo = SqliteJobRepository(job_db_path)
    answers = iter(["回答1", "回答2"])
    try:
        done_job = respond_to_job(
            repo, job, ask=lambda _: next(answers), output=lambda _: None
        )

        assert done_job.status == JobStatus.DONE
        assert done_job.result["plan_document"] == "# 概要\n..."
        assert done_job.result["tasks"] == [
            {"title": "タスク1", "description": "内容1"}
        ]
        assert done_job.result["questions"] == []
        assert done_job.result["resolved_questions"] == [
            {**_QUESTIONS[0], "answer": "回答1"},
            {**_QUESTIONS[1], "answer": "回答2"},
        ]
    finally:
        repo.close()


def test_run_respond_with_plan_job_type_completes_the_job(tmp_path):
    config = _config(tmp_path)
    job = _make_waiting_human_job(
        config.job_db_path, job_type=JobType.PLAN, result=_plan_result()
    )
    answers = iter(["回答1", "回答2"])

    done_job = run_respond(
        config, job_id=job.id, ask=lambda _: next(answers), output=lambda _: None
    )

    assert done_job is not None
    assert done_job.status == JobStatus.DONE
    assert done_job.result["resolved_questions"][0]["answer"] == "回答1"


# --- implement種別Job(M4-8, ADR-0033)---


def test_respond_to_job_supports_implement_job_type(tmp_path):
    # implement種別のWAITING_HUMAN Jobも、issue-analysis/design/planと同じ手順で
    # DONEまで遷移できること(_RESULT_RESOLVERSの一般化、ADR-0033)
    job_db_path = str(tmp_path / "job.db")
    job = _make_waiting_human_job(
        job_db_path, job_type=JobType.IMPLEMENT, result=_implement_result()
    )

    repo = SqliteJobRepository(job_db_path)
    answers = iter(["回答1", "回答2"])
    try:
        done_job = respond_to_job(
            repo, job, ask=lambda _: next(answers), output=lambda _: None
        )

        assert done_job.status == JobStatus.DONE
        # commit_sha等、実装フェーズ固有のフィールドがそのまま引き継がれること
        assert done_job.result["commit_sha"] == "after-sha"
        assert done_job.result["remote_branch"] == f"ai/issue-{_ISSUE_IID}"
        assert done_job.result["questions"] == []
        assert done_job.result["resolved_questions"] == [
            {**_QUESTIONS[0], "answer": "回答1"},
            {**_QUESTIONS[1], "answer": "回答2"},
        ]
    finally:
        repo.close()


def test_run_respond_with_implement_job_type_completes_the_job(tmp_path):
    config = _config(tmp_path)
    job = _make_waiting_human_job(
        config.job_db_path, job_type=JobType.IMPLEMENT, result=_implement_result()
    )
    answers = iter(["回答1", "回答2"])

    done_job = run_respond(
        config, job_id=job.id, ask=lambda _: next(answers), output=lambda _: None
    )

    assert done_job is not None
    assert done_job.status == JobStatus.DONE
    assert done_job.result["resolved_questions"][0]["answer"] == "回答1"
