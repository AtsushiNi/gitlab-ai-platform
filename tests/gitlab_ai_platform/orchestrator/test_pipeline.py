from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gitlab_ai_platform.job.errors import JobError
from gitlab_ai_platform.job.protocol import Job, JobStatus, JobType
from gitlab_ai_platform.orchestrator.pipeline import (
    advance_pipeline,
    advance_pipeline_hook,
)

_PROJECT = "group/project"
_ISSUE_IID = 7


def _job(
    job_type: JobType,
    *,
    status: JobStatus = JobStatus.DONE,
    payload: dict | None = None,
    result: dict | None = None,
) -> Job:
    now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)
    return Job(
        id="job-1",
        job_type=job_type,
        status=status,
        payload=payload if payload is not None else {},
        result=result,
        error=None,
        created_at=now,
        updated_at=now,
    )


class _FakeJobRepository:
    """`advance_pipeline`が使う`enqueue`のみを満たす手書きフェイク。"""

    def __init__(self, *, enqueue_error: Exception | None = None) -> None:
        self._enqueue_error = enqueue_error
        self.enqueue_calls: list[tuple[JobType, dict]] = []
        self._next_id = 0

    def enqueue(
        self, job_type: JobType, payload: dict, *, max_attempts: int = 3
    ) -> Job:
        self.enqueue_calls.append((job_type, payload))
        if self._enqueue_error is not None:
            raise self._enqueue_error
        self._next_id += 1
        now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)
        return Job(
            id=f"next-job-{self._next_id}",
            job_type=job_type,
            status=JobStatus.PENDING,
            payload=payload,
            result=None,
            error=None,
            created_at=now,
            updated_at=now,
        )


# --- 連鎖させない境界(status/job_type) ---


@pytest.mark.parametrize(
    "status",
    [JobStatus.PENDING, JobStatus.RUNNING, JobStatus.WAITING_HUMAN, JobStatus.FAILED],
)
def test_returns_none_when_job_is_not_done(status):
    repo = _FakeJobRepository()
    job = _job(
        JobType.ISSUE_ANALYSIS,
        status=status,
        result={"project": _PROJECT, "issue_iid": _ISSUE_IID},
    )

    assert advance_pipeline(repo, job) is None
    assert repo.enqueue_calls == []


def test_returns_none_for_review_job_type():
    # reviewは無人実行パイプラインとは別トラックで、次フェーズを持たない
    repo = _FakeJobRepository()
    job = _job(
        JobType.REVIEW,
        result={
            "project": _PROJECT,
            "mr_iid": 1,
            "sha": "abc",
            "result_path": "/tmp/x",
        },
    )

    assert advance_pipeline(repo, job) is None
    assert repo.enqueue_calls == []


def test_returns_none_when_push_result_missing_review_required_fields():
    # merge_request_iid等がresultに無い(壊れたJob等)場合はKeyErrorをNoneに変換する
    repo = _FakeJobRepository()
    job = _job(
        JobType.PUSH,
        result={"project": _PROJECT, "issue_iid": _ISSUE_IID},
    )

    assert advance_pipeline(repo, job) is None
    assert repo.enqueue_calls == []


# --- issue-analysis → design ---


def test_issue_analysis_done_enqueues_design_job():
    repo = _FakeJobRepository()
    job = _job(
        JobType.ISSUE_ANALYSIS,
        result={
            "project": _PROJECT,
            "issue_iid": _ISSUE_IID,
            "requirements": ["要求1"],
            "acceptance_criteria": ["条件1"],
            "assumptions": ["前提1"],
            "assumed_uncertainties": [],
            "questions": [],
        },
    )

    next_job = advance_pipeline(repo, job)

    assert next_job is not None
    assert next_job.job_type is JobType.DESIGN
    assert len(repo.enqueue_calls) == 1
    job_type, payload = repo.enqueue_calls[0]
    assert job_type is JobType.DESIGN
    assert payload == {
        "project": _PROJECT,
        "issue_iid": _ISSUE_IID,
        "requirements": ["要求1"],
        "acceptance_criteria": ["条件1"],
        "assumptions": ["前提1"],
        "assumed_uncertainties": [],
    }


# --- design → plan ---


def test_design_done_enqueues_plan_job():
    repo = _FakeJobRepository()
    job = _job(
        JobType.DESIGN,
        result={
            "project": _PROJECT,
            "issue_iid": _ISSUE_IID,
            "design_document": "# 責務\n設計文書の本文です。",
            "assumed_uncertainties": [],
            "questions": [],
        },
    )

    next_job = advance_pipeline(repo, job)

    assert next_job is not None
    assert next_job.job_type is JobType.PLAN
    job_type, payload = repo.enqueue_calls[0]
    assert job_type is JobType.PLAN
    assert payload["design_document"] == "# 責務\n設計文書の本文です。"
    assert payload["project"] == _PROJECT
    assert payload["issue_iid"] == _ISSUE_IID


# --- plan → implement ---


def test_plan_done_enqueues_implement_job():
    repo = _FakeJobRepository()
    job = _job(
        JobType.PLAN,
        result={
            "project": _PROJECT,
            "issue_iid": _ISSUE_IID,
            "plan_document": "# 概要\n実装計画の本文です。",
            "tasks": [{"title": "タスク1", "description": "内容1"}],
            "assumed_uncertainties": [],
            "questions": [],
        },
    )

    next_job = advance_pipeline(repo, job)

    assert next_job is not None
    assert next_job.job_type is JobType.IMPLEMENT
    job_type, payload = repo.enqueue_calls[0]
    assert job_type is JobType.IMPLEMENT
    assert payload["plan_document"] == "# 概要\n実装計画の本文です。"
    assert payload["tasks"] == [{"title": "タスク1", "description": "内容1"}]


# --- implement → push ---


def test_implement_done_enqueues_push_job_using_payload_and_result():
    # pushのみ、直前フェーズ(implement)のJob payload/result両方から組み立てる(ADR-0034「論点2」)
    repo = _FakeJobRepository()
    job = _job(
        JobType.IMPLEMENT,
        payload={
            "project": _PROJECT,
            "issue_iid": _ISSUE_IID,
            "plan_document": "# 概要\n実装計画の本文です。",
            "tasks": [{"title": "タスク1", "description": "内容1"}],
            "assumed_uncertainties": [],
        },
        result={
            "project": _PROJECT,
            "issue_iid": _ISSUE_IID,
            "summary": "タスク1を実装した",
            "commit_message": "タスク1を実装",
            "commit_sha": "after-sha",
            "remote_branch": f"ai/issue-{_ISSUE_IID}",
            "local_branch": f"issue-{_ISSUE_IID}",
            "worktree_path": "/tmp/workspace/issue-7",
            "assumed_uncertainties": [],
            "questions": [],
        },
    )

    next_job = advance_pipeline(repo, job)

    assert next_job is not None
    assert next_job.job_type is JobType.PUSH
    job_type, payload = repo.enqueue_calls[0]
    assert job_type is JobType.PUSH
    assert payload["plan_document"] == "# 概要\n実装計画の本文です。"
    assert payload["commit_sha"] == "after-sha"
    assert payload["remote_branch"] == f"ai/issue-{_ISSUE_IID}"
    assert payload["worktree_path"] == "/tmp/workspace/issue-7"


def test_implement_done_returns_none_when_result_missing_push_required_fields():
    # commit_sha等がresultに無い(壊れたJob等)場合はKeyErrorをNoneに変換する
    repo = _FakeJobRepository()
    job = _job(
        JobType.IMPLEMENT,
        payload={"project": _PROJECT, "issue_iid": _ISSUE_IID, "plan_document": ""},
        result={"project": _PROJECT, "issue_iid": _ISSUE_IID},
    )

    assert advance_pipeline(repo, job) is None
    assert repo.enqueue_calls == []


# --- push → review(M4-11, ADR-0036) ---


def test_push_done_enqueues_review_job_using_mr_iid_and_pushed_commit_sha():
    # MR IIDは`push`完了時のresultで初めて手に入るため、`implement`ではなく`push`完了を
    # 起点にする(ADR-0036「論点1」)
    repo = _FakeJobRepository()
    job = _job(
        JobType.PUSH,
        result={
            "project": _PROJECT,
            "issue_iid": _ISSUE_IID,
            "pushed_commit_sha": "pushed-sha",
            "remote_branch": f"ai/issue-{_ISSUE_IID}",
            "merge_request_iid": 42,
            "merge_request_web_url": "https://gitlab.example.com/group/project/-/merge_requests/42",
        },
    )

    next_job = advance_pipeline(repo, job)

    assert next_job is not None
    assert next_job.job_type is JobType.REVIEW
    assert len(repo.enqueue_calls) == 1
    job_type, payload = repo.enqueue_calls[0]
    assert job_type is JobType.REVIEW
    assert payload == {
        "project": _PROJECT,
        "mr_iid": 42,
        "sha": "pushed-sha",
    }


# --- 欠損フィールド ---


def test_returns_none_when_result_is_none():
    repo = _FakeJobRepository()
    job = _job(JobType.ISSUE_ANALYSIS, result=None)

    assert advance_pipeline(repo, job) is None
    assert repo.enqueue_calls == []


def test_returns_none_when_project_missing_from_result():
    repo = _FakeJobRepository()
    job = _job(JobType.ISSUE_ANALYSIS, result={"issue_iid": _ISSUE_IID})

    assert advance_pipeline(repo, job) is None
    assert repo.enqueue_calls == []


def test_returns_none_when_issue_iid_missing_from_result():
    repo = _FakeJobRepository()
    job = _job(JobType.ISSUE_ANALYSIS, result={"project": _PROJECT})

    assert advance_pipeline(repo, job) is None
    assert repo.enqueue_calls == []


# --- enqueue失敗 ---


def test_returns_none_when_enqueue_raises_job_error():
    repo = _FakeJobRepository(enqueue_error=JobError("db down"))
    job = _job(
        JobType.ISSUE_ANALYSIS,
        result={
            "project": _PROJECT,
            "issue_iid": _ISSUE_IID,
            "requirements": [],
            "acceptance_criteria": [],
            "assumptions": [],
            "assumed_uncertainties": [],
            "questions": [],
        },
    )

    assert advance_pipeline(repo, job) is None
    # enqueue自体は試みられている(失敗が握りつぶされる前に呼ばれたこと)
    assert len(repo.enqueue_calls) == 1


# --- advance_pipeline_hook ---


def test_advance_pipeline_hook_binds_job_repo_and_returns_none():
    # RunnerDispatcher/respond_to_jobの`on_job_completed: Callable[[Job], None]`契約に
    # 合わせて、戻り値を持たないcallableを返すことを確認する
    repo = _FakeJobRepository()
    job = _job(
        JobType.ISSUE_ANALYSIS,
        result={
            "project": _PROJECT,
            "issue_iid": _ISSUE_IID,
            "requirements": ["要求1"],
            "acceptance_criteria": [],
            "assumptions": [],
            "assumed_uncertainties": [],
            "questions": [],
        },
    )
    hook = advance_pipeline_hook(repo)

    result = hook(job)

    assert result is None
    assert len(repo.enqueue_calls) == 1
    assert repo.enqueue_calls[0][0] is JobType.DESIGN


def test_advance_pipeline_hook_is_a_noop_for_terminal_job_types():
    # reviewはパイプラインの最終フェーズ(次が無い)
    repo = _FakeJobRepository()
    job = _job(
        JobType.REVIEW,
        result={
            "project": _PROJECT,
            "mr_iid": 1,
            "sha": "abc",
            "result_path": "/tmp/x",
        },
    )
    hook = advance_pipeline_hook(repo)

    assert hook(job) is None
    assert repo.enqueue_calls == []
