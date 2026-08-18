from __future__ import annotations

import dataclasses
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gitlab_ai_platform.cli.dispatcher import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    RunnerDispatcher,
    WaitingForHumanError,
    build_design_handler,
    build_implement_handler,
    build_issue_analysis_handler,
    build_job_handlers,
    build_plan_handler,
    build_push_handler,
    build_review_handler,
    default_worker_id,
    run_dispatcher,
)
from gitlab_ai_platform.config import Config
from gitlab_ai_platform.design.job import build_design_job_payload
from gitlab_ai_platform.gitlab_adapter.errors import GitLabApiError
from gitlab_ai_platform.gitlab_adapter.types import (
    Branch,
    CommitAction,
    CommitActionType,
    Discussion,
    Issue,
    MergeRequest,
    MergeRequestDiff,
)
from gitlab_ai_platform.implement.errors import ImplementationNotCommittedError
from gitlab_ai_platform.implement.git_ops import WorktreeState
from gitlab_ai_platform.implement.job import build_implement_job_payload
from gitlab_ai_platform.job import SqliteJobRepository
from gitlab_ai_platform.job.errors import LeaseLostError
from gitlab_ai_platform.job.protocol import Job, JobStatus, JobType
from gitlab_ai_platform.plan.job import build_plan_job_payload
from gitlab_ai_platform.poller.issue_poller import build_issue_analysis_job_payload
from gitlab_ai_platform.push.errors import NoFileChangesError
from gitlab_ai_platform.runner import RunResult
from gitlab_ai_platform.store import ReviewStatus, SqliteStateStore
from gitlab_ai_platform.workspace import IssueWorktreeHandle, WorktreeHandle

_PROJECT = "group/project"
_MR_IID = 1
_ISSUE_IID = 7
_SHA = "abcdef0123456789"
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
        job_db_path=":memory:",
        webhook_enabled=False,
        webhook_host="127.0.0.1",
        webhook_port=8088,
        webhook_path="/webhook",
        webhook_secret_token="",
        store_backend="sqlite",
        store_postgres_host="localhost",
        store_postgres_port=5432,
        store_postgres_dbname="gitlab_ai_platform",
        store_postgres_user="gitlab_ai_platform",
        store_postgres_password="",
        api_host="127.0.0.1",
        api_port=8090,
        api_token="",
    )
    kwargs.update(overrides)
    return Config.from_raw(**kwargs)


def _job(**overrides) -> Job:
    now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
    kwargs = dict(
        id="job-1",
        job_type=JobType.REVIEW,
        status=JobStatus.RUNNING,
        payload={"project": _PROJECT, "mr_iid": _MR_IID, "sha": _SHA},
        result=None,
        error=None,
        created_at=now,
        updated_at=now,
    )
    kwargs.update(overrides)
    return Job(**kwargs)


class _FakeGitLabReader:
    """`GitLabReader`(+ `implement`が使う`get_default_branch`/`create_branch`)を満たす
    テスト用フェイク(固定のMR/Issueを1件だけ返す)。"""

    def __init__(
        self,
        merge_request: MergeRequest | None = None,
        issue: Issue | None = None,
        *,
        default_branch: str = "main",
        create_branch_error: GitLabApiError | None = None,
    ) -> None:
        self._merge_request = merge_request
        self._issue = issue
        self._default_branch = default_branch
        self._create_branch_error = create_branch_error
        self.create_branch_calls: list[tuple[str, str, str]] = []

    def get_version(self) -> str:
        raise NotImplementedError

    def list_merge_requests(self, project: str, *, labels=(), state="opened"):
        raise NotImplementedError

    def get_merge_request(self, project: str, mr_iid: int) -> MergeRequest:
        assert self._merge_request is not None
        return self._merge_request

    def get_merge_request_diffs(self, project: str, mr_iid: int):
        return [
            MergeRequestDiff(
                old_path="a.py",
                new_path="a.py",
                diff="-old\n+new",
                new_file=False,
                renamed_file=False,
                deleted_file=False,
            )
        ]

    def list_merge_request_discussions(
        self, project: str, mr_iid: int
    ) -> list[Discussion]:
        return []

    def list_issues(self, project: str, *, labels=(), state="opened"):
        raise NotImplementedError

    def get_issue(self, project: str, issue_iid: int) -> Issue:
        assert self._issue is not None
        return self._issue

    def get_default_branch(self, project: str) -> str:
        return self._default_branch

    def create_branch(self, project: str, branch_name: str, ref: str) -> Branch:
        self.create_branch_calls.append((project, branch_name, ref))
        if self._create_branch_error is not None:
            raise self._create_branch_error
        return Branch(name=branch_name, commit_sha="branch-head-sha", protected=False)


class _FakeWorkspaceManager:
    def __init__(self, worktree_path: Path) -> None:
        self._worktree_path = worktree_path
        self.prepare_calls: list[tuple[str, int, str]] = []
        self.prepare_for_issue_calls: list[tuple[str, int, str]] = []
        self.discard_for_issue_calls: list[tuple[str, int]] = []

    def prepare(self, project: str, mr_iid: int, ref: str) -> WorktreeHandle:
        self.prepare_calls.append((project, mr_iid, ref))
        return WorktreeHandle(
            project=project,
            mr_iid=mr_iid,
            path=self._worktree_path,
            branch=f"mr-{mr_iid}",
            sha=ref,
        )

    def discard(self, project: str, mr_iid: int) -> None:
        pass

    def collect_garbage(self) -> list[WorktreeHandle]:
        return []

    def prepare_for_issue(
        self, project: str, issue_iid: int, ref: str
    ) -> IssueWorktreeHandle:
        self.prepare_for_issue_calls.append((project, issue_iid, ref))
        return IssueWorktreeHandle(
            project=project,
            issue_iid=issue_iid,
            path=self._worktree_path,
            branch=f"issue-{issue_iid}",
            sha="worktree-initial-sha",
        )

    def discard_for_issue(self, project: str, issue_iid: int) -> None:
        self.discard_for_issue_calls.append((project, issue_iid))


class _FakeWorktreeStateReader:
    """`read_worktree_state`の代わりに`build_implement_handler`へ注入するテスト用フェイク。

    実行前(1回目の呼び出し)・実行後(2回目の呼び出し)で異なる`WorktreeState`を返せるよう、
    あらかじめ用意したリストを順番に返す(実gitには繋がない)。
    """

    def __init__(self, states: list[WorktreeState]) -> None:
        self._states = list(states)
        self.calls: list[Path] = []

    def __call__(self, worktree_path: Path) -> WorktreeState:
        self.calls.append(worktree_path)
        return self._states.pop(0)


class _FakeClaudeCodeRunner:
    def __init__(
        self,
        tmp_path: Path,
        *,
        open_questions: list | None = None,
        result_kind: str = "issue-analysis",
    ) -> None:
        self._tmp_path = tmp_path
        # issue-analysis/design/planの結果に含める不足情報(既定は無し=WAITING_HUMANにならない)
        self._open_questions = open_questions if open_questions is not None else []
        # run_promptが返すペイロードの形("issue-analysis"/"design"/"plan")。
        # build_design_handler/build_plan_handlerのテストではそれぞれ"design"/"plan"を指定する
        self._result_kind = result_kind
        self.run_calls: list[tuple[str, int]] = []
        self.run_prompt_calls: list[tuple[Path, str, str]] = []
        self.run_prompt_kwargs: list[dict] = []

    def run(
        self,
        worktree_path,
        instructions,
        context,
        *,
        timeout_seconds,
        allowed_tools=(),
        disallowed_tools=(),
        permission_mode=None,
    ) -> RunResult:
        mr = context.merge_request
        self.run_calls.append((mr.project, mr.iid))
        log_path = self._tmp_path / f"run_log-{mr.iid}.json"
        log_path.write_text(json.dumps({"command": ["claude"]}), encoding="utf-8")
        payload = {"summary": "特に指摘なし", "findings": []}
        result_text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        return RunResult(
            is_error=False,
            result_text=result_text,
            session_id=f"session-{mr.iid}",
            terminal_reason="success",
            permission_denials=(),
            num_turns=1,
            total_cost_usd=0.01,
            timed_out=False,
            duration_seconds=1.0,
            log_path=log_path,
            raw={},
        )

    def run_prompt(
        self,
        worktree_path,
        prompt,
        *,
        log_key,
        timeout_seconds,
        allowed_tools=(),
        disallowed_tools=(),
        permission_mode=None,
    ) -> RunResult:
        self.run_prompt_calls.append((worktree_path, prompt, log_key))
        self.run_prompt_kwargs.append(
            {
                "allowed_tools": allowed_tools,
                "disallowed_tools": disallowed_tools,
                "permission_mode": permission_mode,
            }
        )
        if self._result_kind == "design":
            payload = {
                "design_document": "# 責務\n設計文書の本文です。",
                "open_questions": self._open_questions,
            }
        elif self._result_kind == "plan":
            payload = {
                "plan_document": "# 概要\n実装計画の本文です。",
                "tasks": [{"title": "タスク1", "description": "内容1"}],
                "open_questions": self._open_questions,
            }
        elif self._result_kind == "implement":
            payload = {
                "summary": "タスク1を実装しテストが通りました。",
                "commit_message": "タスク1を実装",
                "tests_passed": True,
                "open_questions": self._open_questions,
            }
        else:
            payload = {
                "requirements": ["要求1"],
                "acceptance_criteria": ["条件1"],
                "assumptions": ["前提1"],
                "open_questions": self._open_questions,
            }
        result_text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        log_path = self._tmp_path / "run_prompt_log.json"
        log_path.write_text(json.dumps({"command": ["claude"]}), encoding="utf-8")
        return RunResult(
            is_error=False,
            result_text=result_text,
            session_id="session-issue",
            terminal_reason="success",
            permission_denials=(),
            num_turns=1,
            total_cost_usd=0.01,
            timed_out=False,
            duration_seconds=1.0,
            log_path=log_path,
            raw={},
        )


class _FakeJobRepository:
    """`RunnerDispatcher`が使うメソッド(claim/heartbeat/complete/fail/wait_for_human)だけを
    満たす手書きフェイク。

    `claim`は`jobs_to_claim`を先頭から1件ずつ返し、尽きたら`None`を返し続ける。
    """

    def __init__(
        self,
        jobs_to_claim: list[Job | None],
        *,
        heartbeat_error: Exception | None = None,
    ) -> None:
        self._jobs_to_claim = list(jobs_to_claim)
        self._heartbeat_error = heartbeat_error
        # claimしたJobをid別に覚えておき、completeが返す更新後Jobの組み立てに使う
        # (M4-10: RunnerDispatcher._processがcompleteの戻り値をon_job_completedへ渡すため)
        self._claimed_jobs: dict[str, Job] = {}
        self.claim_calls: list[tuple[str, tuple[JobType, ...] | None, int]] = []
        self.heartbeat_calls: list[tuple[str, str, int]] = []
        self.complete_calls: list[tuple[str, str, dict | None]] = []
        self.fail_calls: list[tuple[str, str, str, bool]] = []
        self.wait_for_human_calls: list[tuple[str, str, dict | None]] = []

    def claim(self, worker_id, *, job_types=None, visibility_timeout_seconds=600):
        self.claim_calls.append((worker_id, job_types, visibility_timeout_seconds))
        if self._jobs_to_claim:
            job = self._jobs_to_claim.pop(0)
            if job is not None:
                self._claimed_jobs[job.id] = job
            return job
        return None

    def heartbeat(self, job_id, worker_id, *, visibility_timeout_seconds=600):
        self.heartbeat_calls.append((job_id, worker_id, visibility_timeout_seconds))
        if self._heartbeat_error is not None:
            raise self._heartbeat_error

    def complete(self, job_id, worker_id, result=None):
        self.complete_calls.append((job_id, worker_id, result))
        original = self._claimed_jobs.get(job_id)
        if original is None:
            return None
        return dataclasses.replace(original, status=JobStatus.DONE, result=result)

    def fail(self, job_id, worker_id, error, *, retry=True):
        self.fail_calls.append((job_id, worker_id, error, retry))

    def wait_for_human(self, job_id, worker_id, result=None):
        self.wait_for_human_calls.append((job_id, worker_id, result))


def _merge_request() -> MergeRequest:
    return MergeRequest(
        project=_PROJECT,
        iid=_MR_IID,
        title="Fix bug",
        description="",
        state="opened",
        source_branch="fix",
        target_branch="main",
        sha=_SHA,
        author="alice",
    )


def _issue() -> Issue:
    return Issue(
        project=_PROJECT,
        iid=_ISSUE_IID,
        title="Add feature Y",
        description="We need feature Y.",
        state="opened",
        author="carol",
        labels=("要求分析待ち",),
    )


# --- build_review_handler / build_job_handlers ---


def test_build_review_handler_runs_execute_review_and_returns_result_dict(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")

    try:
        handler = build_review_handler(adapter, workspace, runner, store, config)
        job = _job()

        result = handler(job)

        assert result["project"] == _PROJECT
        assert result["mr_iid"] == _MR_IID
        assert result["sha"] == _SHA
        assert Path(result["result_path"]).is_dir()
        assert workspace.prepare_calls == [(_PROJECT, _MR_IID, _SHA)]
        assert runner.run_calls == [(_PROJECT, _MR_IID)]
        record = store.find(_PROJECT, _MR_IID, _SHA)
        assert record.status == ReviewStatus.DONE
    finally:
        store.close()


def test_build_job_handlers_registers_all_six_job_types(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request(), _issue())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")

    try:
        handlers = build_job_handlers(adapter, workspace, runner, store, config)
        # M4-9時点で実際にRunnerが処理できるのはreview/issue-analysis/design/plan/implement/push
        # (ADR-0016で予約済みの4種+plan+push、ADR-0030/0034)
        assert set(handlers) == {
            JobType.REVIEW,
            JobType.ISSUE_ANALYSIS,
            JobType.DESIGN,
            JobType.PLAN,
            JobType.IMPLEMENT,
            JobType.PUSH,
        }
    finally:
        store.close()


# --- build_issue_analysis_handler ---


def _issue_analysis_job(**overrides) -> Job:
    now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
    kwargs = dict(
        id="job-analysis-1",
        job_type=JobType.ISSUE_ANALYSIS,
        status=JobStatus.RUNNING,
        payload=build_issue_analysis_job_payload(_PROJECT, _ISSUE_IID),
        result=None,
        error=None,
        created_at=now,
        updated_at=now,
    )
    kwargs.update(overrides)
    return Job(**kwargs)


def test_build_issue_analysis_handler_returns_result_dict_when_no_open_questions(
    tmp_path,
):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    runner = _FakeClaudeCodeRunner(tmp_path, open_questions=[])

    handler = build_issue_analysis_handler(adapter, runner, config)
    job = _issue_analysis_job()

    result = handler(job)

    assert result["project"] == _PROJECT
    assert result["issue_iid"] == _ISSUE_IID
    assert result["requirements"] == ["要求1"]
    assert result["acceptance_criteria"] == ["条件1"]
    assert result["assumptions"] == ["前提1"]
    assert result["assumed_uncertainties"] == []
    assert result["questions"] == []
    assert len(runner.run_prompt_calls) == 1
    _, prompt, log_key = runner.run_prompt_calls[0]
    assert "Add feature Y" in prompt
    assert log_key == f"group%2Fproject/issue-{_ISSUE_IID}"


def test_build_issue_analysis_handler_raises_waiting_for_human_when_critical_question(
    tmp_path,
):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    runner = _FakeClaudeCodeRunner(
        tmp_path,
        open_questions=[
            {"question": "対象範囲は?", "severity": "critical"},
        ],
    )

    handler = build_issue_analysis_handler(adapter, runner, config)
    job = _issue_analysis_job()

    with pytest.raises(WaitingForHumanError) as excinfo:
        handler(job)

    assert excinfo.value.result["questions"] == [
        {"question": "対象範囲は?", "severity": "critical"}
    ]


def test_build_issue_analysis_handler_completes_when_only_minor_question(tmp_path):
    # MINORな不明点はASSUME判定になり、WAITING_HUMANにはならない(orchestrator.judge_uncertainty)
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    runner = _FakeClaudeCodeRunner(
        tmp_path,
        open_questions=[
            {
                "question": "文言は?",
                "severity": "minor",
                "assumption": "一般的な文言を使う",
            },
        ],
    )

    handler = build_issue_analysis_handler(adapter, runner, config)
    job = _issue_analysis_job()

    result = handler(job)

    assert result["questions"] == []
    assert result["assumed_uncertainties"] == [
        {"question": "文言は?", "severity": "minor", "assumption": "一般的な文言を使う"}
    ]


# --- build_design_handler ---


def _design_job(**overrides) -> Job:
    now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
    kwargs = dict(
        id="job-design-1",
        job_type=JobType.DESIGN,
        status=JobStatus.RUNNING,
        payload=build_design_job_payload(
            _PROJECT,
            _ISSUE_IID,
            {
                "requirements": ["要求1"],
                "acceptance_criteria": ["条件1"],
                "assumptions": ["前提1"],
                "assumed_uncertainties": [],
            },
        ),
        result=None,
        error=None,
        created_at=now,
        updated_at=now,
    )
    kwargs.update(overrides)
    return Job(**kwargs)


def test_build_design_handler_returns_result_dict_when_no_open_questions(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    runner = _FakeClaudeCodeRunner(tmp_path, open_questions=[], result_kind="design")

    handler = build_design_handler(adapter, runner, config)
    job = _design_job()

    result = handler(job)

    assert result["project"] == _PROJECT
    assert result["issue_iid"] == _ISSUE_IID
    assert result["design_document"] == "# 責務\n設計文書の本文です。"
    assert result["assumed_uncertainties"] == []
    assert result["questions"] == []
    assert len(runner.run_prompt_calls) == 1
    _, prompt, log_key = runner.run_prompt_calls[0]
    assert "Add feature Y" in prompt
    assert "要求1" in prompt
    assert log_key == f"group%2Fproject/issue-{_ISSUE_IID}/design"


def test_build_design_handler_raises_waiting_for_human_when_critical_question(
    tmp_path,
):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    runner = _FakeClaudeCodeRunner(
        tmp_path,
        open_questions=[
            {"question": "既存の認証基盤を再利用しますか?", "severity": "critical"},
        ],
        result_kind="design",
    )

    handler = build_design_handler(adapter, runner, config)
    job = _design_job()

    with pytest.raises(WaitingForHumanError) as excinfo:
        handler(job)

    assert excinfo.value.result["questions"] == [
        {"question": "既存の認証基盤を再利用しますか?", "severity": "critical"}
    ]


def test_build_design_handler_completes_when_only_minor_question(tmp_path):
    # MINORな不明点はASSUME判定になり、WAITING_HUMANにはならない(orchestrator.judge_uncertainty)
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    runner = _FakeClaudeCodeRunner(
        tmp_path,
        open_questions=[
            {
                "question": "キャッシュ戦略は?",
                "severity": "minor",
                "assumption": "単純なTTLキャッシュを使う",
            },
        ],
        result_kind="design",
    )

    handler = build_design_handler(adapter, runner, config)
    job = _design_job()

    result = handler(job)

    assert result["questions"] == []
    assert result["assumed_uncertainties"] == [
        {
            "question": "キャッシュ戦略は?",
            "severity": "minor",
            "assumption": "単純なTTLキャッシュを使う",
        }
    ]


# --- build_plan_handler ---


def _plan_job(**overrides) -> Job:
    now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
    kwargs = dict(
        id="job-plan-1",
        job_type=JobType.PLAN,
        status=JobStatus.RUNNING,
        payload=build_plan_job_payload(
            _PROJECT,
            _ISSUE_IID,
            {
                "design_document": "# 責務\n設計文書の本文です。",
                "assumed_uncertainties": [],
            },
        ),
        result=None,
        error=None,
        created_at=now,
        updated_at=now,
    )
    kwargs.update(overrides)
    return Job(**kwargs)


def test_build_plan_handler_returns_result_dict_when_no_open_questions(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    runner = _FakeClaudeCodeRunner(tmp_path, open_questions=[], result_kind="plan")

    handler = build_plan_handler(adapter, runner, config)
    job = _plan_job()

    result = handler(job)

    assert result["project"] == _PROJECT
    assert result["issue_iid"] == _ISSUE_IID
    assert result["plan_document"] == "# 概要\n実装計画の本文です。"
    assert result["tasks"] == [{"title": "タスク1", "description": "内容1"}]
    assert result["assumed_uncertainties"] == []
    assert result["questions"] == []
    assert len(runner.run_prompt_calls) == 1
    _, prompt, log_key = runner.run_prompt_calls[0]
    assert "Add feature Y" in prompt
    assert "設計文書の本文です。" in prompt
    assert log_key == f"group%2Fproject/issue-{_ISSUE_IID}/plan"


def test_build_plan_handler_raises_waiting_for_human_when_critical_question(
    tmp_path,
):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    runner = _FakeClaudeCodeRunner(
        tmp_path,
        open_questions=[
            {"question": "既存の移行ツールを再利用しますか?", "severity": "critical"},
        ],
        result_kind="plan",
    )

    handler = build_plan_handler(adapter, runner, config)
    job = _plan_job()

    with pytest.raises(WaitingForHumanError) as excinfo:
        handler(job)

    assert excinfo.value.result["questions"] == [
        {"question": "既存の移行ツールを再利用しますか?", "severity": "critical"}
    ]


def test_build_plan_handler_completes_when_only_minor_question(tmp_path):
    # MINORな不明点はASSUME判定になり、WAITING_HUMANにはならない(orchestrator.judge_uncertainty)
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    runner = _FakeClaudeCodeRunner(
        tmp_path,
        open_questions=[
            {
                "question": "タスク粒度はこれでよいか?",
                "severity": "minor",
                "assumption": "1コミット相当の粒度とした",
            },
        ],
        result_kind="plan",
    )

    handler = build_plan_handler(adapter, runner, config)
    job = _plan_job()

    result = handler(job)

    assert result["questions"] == []
    assert result["assumed_uncertainties"] == [
        {
            "question": "タスク粒度はこれでよいか?",
            "severity": "minor",
            "assumption": "1コミット相当の粒度とした",
        }
    ]


# --- build_implement_handler ---


def _implement_job(**overrides) -> Job:
    now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
    kwargs = dict(
        id="job-implement-1",
        job_type=JobType.IMPLEMENT,
        status=JobStatus.RUNNING,
        payload=build_implement_job_payload(
            _PROJECT,
            _ISSUE_IID,
            {
                "plan_document": "# 概要\n実装計画の本文です。",
                "tasks": [{"title": "タスク1", "description": "内容1"}],
                "assumed_uncertainties": [],
            },
        ),
        result=None,
        error=None,
        created_at=now,
        updated_at=now,
    )
    kwargs.update(overrides)
    return Job(**kwargs)


def _committed_states() -> list[WorktreeState]:
    # 実行前後でHEAD shaが変化する(=commitされた)状態を模擬する
    return [
        WorktreeState(head_sha="before-sha", is_clean=True),
        WorktreeState(head_sha="after-sha", is_clean=True),
    ]


def test_build_implement_handler_returns_result_dict_when_commit_detected(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path, open_questions=[], result_kind="implement")
    state_reader = _FakeWorktreeStateReader(_committed_states())

    handler = build_implement_handler(
        adapter, workspace, runner, config, read_worktree_state=state_reader
    )
    job = _implement_job()

    result = handler(job)

    assert result["project"] == _PROJECT
    assert result["issue_iid"] == _ISSUE_IID
    assert result["summary"] == "タスク1を実装しテストが通りました。"
    assert result["commit_message"] == "タスク1を実装"
    assert result["commit_sha"] == "after-sha"
    assert result["remote_branch"] == f"ai/issue-{_ISSUE_IID}"
    assert result["local_branch"] == f"issue-{_ISSUE_IID}"
    assert result["assumed_uncertainties"] == []
    assert result["questions"] == []


def test_build_implement_handler_creates_remote_branch_from_default_branch(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue(), default_branch="develop")
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path, open_questions=[], result_kind="implement")
    state_reader = _FakeWorktreeStateReader(_committed_states())

    handler = build_implement_handler(
        adapter, workspace, runner, config, read_worktree_state=state_reader
    )
    handler(_implement_job())

    assert adapter.create_branch_calls == [
        (_PROJECT, f"ai/issue-{_ISSUE_IID}", "develop")
    ]
    assert workspace.prepare_for_issue_calls == [
        (_PROJECT, _ISSUE_IID, f"ai/issue-{_ISSUE_IID}")
    ]


def test_build_implement_handler_tolerates_branch_already_existing(tmp_path):
    # retry等でbranchが既に存在する場合(GitLab APIの400)は続行する
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(
        issue=_issue(),
        create_branch_error=GitLabApiError("already exists", status_code=400),
    )
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path, open_questions=[], result_kind="implement")
    state_reader = _FakeWorktreeStateReader(_committed_states())

    handler = build_implement_handler(
        adapter, workspace, runner, config, read_worktree_state=state_reader
    )

    result = handler(_implement_job())

    assert result["commit_sha"] == "after-sha"


def test_build_implement_handler_reraises_non_conflict_gitlab_errors(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(
        issue=_issue(),
        create_branch_error=GitLabApiError("server error", status_code=500),
    )
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path, open_questions=[], result_kind="implement")

    handler = build_implement_handler(adapter, workspace, runner, config)

    with pytest.raises(GitLabApiError):
        handler(_implement_job())


def test_build_implement_handler_raises_when_head_sha_unchanged(tmp_path):
    # テストが通らない/commitされなかった場合の扱い(ADR-0033): HEAD shaが変化しないまま
    # 終了した場合はImplementationNotCommittedErrorを送出し、Job Queueの既存retry機構に乗せる
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(
        tmp_path,
        open_questions=[],
        result_kind="implement",
    )
    state_reader = _FakeWorktreeStateReader(
        [
            WorktreeState(head_sha="same-sha", is_clean=True),
            WorktreeState(head_sha="same-sha", is_clean=True),
        ]
    )

    handler = build_implement_handler(
        adapter, workspace, runner, config, read_worktree_state=state_reader
    )

    with pytest.raises(ImplementationNotCommittedError):
        handler(_implement_job())


def test_build_implement_handler_raises_when_worktree_left_dirty(tmp_path):
    # HEADが進んでいても、未commitの差分が残っている(=commit漏れがある)場合も失敗として扱う
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path, open_questions=[], result_kind="implement")
    state_reader = _FakeWorktreeStateReader(
        [
            WorktreeState(head_sha="before-sha", is_clean=True),
            WorktreeState(head_sha="after-sha", is_clean=False),
        ]
    )

    handler = build_implement_handler(
        adapter, workspace, runner, config, read_worktree_state=state_reader
    )

    with pytest.raises(ImplementationNotCommittedError):
        handler(_implement_job())


def test_build_implement_handler_raises_waiting_for_human_when_critical_question(
    tmp_path,
):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(
        tmp_path,
        open_questions=[
            {"question": "既存の命名規則に従いますか?", "severity": "critical"},
        ],
        result_kind="implement",
    )
    state_reader = _FakeWorktreeStateReader(_committed_states())

    handler = build_implement_handler(
        adapter, workspace, runner, config, read_worktree_state=state_reader
    )

    with pytest.raises(WaitingForHumanError) as excinfo:
        handler(_implement_job())

    assert excinfo.value.result["questions"] == [
        {"question": "既存の命名規則に従いますか?", "severity": "critical"}
    ]
    # 不明点があっても、commit自体は既にされている前提でcommit_shaを結果に残す
    assert excinfo.value.result["commit_sha"] == "after-sha"


def test_build_implement_handler_completes_when_only_minor_question(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(
        tmp_path,
        open_questions=[
            {
                "question": "エラーハンドリングの粒度は?",
                "severity": "minor",
                "assumption": "既存の例外型をそのまま再送出した",
            },
        ],
        result_kind="implement",
    )
    state_reader = _FakeWorktreeStateReader(_committed_states())

    handler = build_implement_handler(
        adapter, workspace, runner, config, read_worktree_state=state_reader
    )

    result = handler(_implement_job())

    assert result["questions"] == []
    assert result["assumed_uncertainties"] == [
        {
            "question": "エラーハンドリングの粒度は?",
            "severity": "minor",
            "assumption": "既存の例外型をそのまま再送出した",
        }
    ]


def test_build_implement_handler_grants_edit_write_bash_and_blocks_git_push(tmp_path):
    # M4-8: このリポジトリで初めてEdit/Write/Bashを許可する。git pushは明示的に禁止する
    # (docs/operations/security.md参照)
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path, open_questions=[], result_kind="implement")
    state_reader = _FakeWorktreeStateReader(_committed_states())

    handler = build_implement_handler(
        adapter, workspace, runner, config, read_worktree_state=state_reader
    )
    handler(_implement_job())

    assert len(runner.run_prompt_kwargs) == 1
    kwargs = runner.run_prompt_kwargs[0]
    assert set(kwargs["allowed_tools"]) == {"Edit", "Write", "Bash"}
    assert any("git push" in t for t in kwargs["disallowed_tools"])
    # --dangerously-skip-permissions相当の全許可モードは使わない(ADR-0005)
    assert kwargs["permission_mode"] != "bypassPermissions"


def test_build_implement_handler_does_not_discard_worktree_on_success(tmp_path):
    # 実装成功時はローカルcommitをM4-9(push)が参照できるようdiscard_for_issueを呼ばない
    # (ADR-0033)
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(issue=_issue())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path, open_questions=[], result_kind="implement")
    state_reader = _FakeWorktreeStateReader(_committed_states())

    handler = build_implement_handler(
        adapter, workspace, runner, config, read_worktree_state=state_reader
    )
    handler(_implement_job())

    assert workspace.discard_for_issue_calls == []


# --- build_push_handler ---


class _FakeGitLabPushWriter:
    """`GitLabReader.get_default_branch`と`GitLabWriter`の`push`が使う書き込みメソッド
    (`push_file_changes`/`create_merge_request`)だけを満たすテスト用フェイク。"""

    def __init__(
        self,
        *,
        default_branch: str = "main",
        merge_request: MergeRequest | None = None,
        push_error: Exception | None = None,
        create_merge_request_error: Exception | None = None,
    ) -> None:
        self._default_branch = default_branch
        self._merge_request = merge_request or MergeRequest(
            project=_PROJECT,
            iid=42,
            title="[Issue #7] タスク1を実装した",
            description="...",
            state="opened",
            source_branch=f"ai/issue-{_ISSUE_IID}",
            target_branch=default_branch,
            sha="pushed-sha",
            author="ai-bot",
            web_url="https://gitlab.example.com/group/project/-/merge_requests/42",
        )
        self._push_error = push_error
        self._create_merge_request_error = create_merge_request_error
        self.push_file_changes_calls: list[tuple] = []
        self.create_merge_request_calls: list[tuple] = []

    def get_default_branch(self, project: str) -> str:
        return self._default_branch

    def push_file_changes(self, project, branch, commit_message, actions) -> str:
        self.push_file_changes_calls.append((project, branch, commit_message, actions))
        if self._push_error is not None:
            raise self._push_error
        return "pushed-sha"

    def create_merge_request(
        self, project, source_branch, target_branch, title, description=""
    ) -> MergeRequest:
        self.create_merge_request_calls.append(
            (project, source_branch, target_branch, title, description)
        )
        if self._create_merge_request_error is not None:
            raise self._create_merge_request_error
        return self._merge_request


class _FakeComputeCommitActions:
    """`build_push_handler`へ注入する`compute_commit_actions`のテスト用フェイク。実gitに繋がない。"""

    def __init__(self, actions: list | None = None) -> None:
        self._actions = (
            actions
            if actions is not None
            else [
                CommitAction(
                    action=CommitActionType.CREATE,
                    file_path="new_file.py",
                    content="print('new')\n",
                )
            ]
        )
        self.calls: list[tuple[Path, str, str]] = []

    def __call__(self, worktree_path: Path, default_branch: str, commit_sha: str):
        self.calls.append((worktree_path, default_branch, commit_sha))
        return list(self._actions)


def _push_job(**overrides) -> Job:
    now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
    kwargs = dict(
        id="job-push-1",
        job_type=JobType.PUSH,
        status=JobStatus.RUNNING,
        payload={
            "project": _PROJECT,
            "issue_iid": _ISSUE_IID,
            "plan_document": "# 概要\n実装計画の本文です。",
            "summary": "タスク1を実装した",
            "commit_message": "タスク1を実装",
            "commit_sha": "after-sha",
            "remote_branch": f"ai/issue-{_ISSUE_IID}",
            "local_branch": f"issue-{_ISSUE_IID}",
            "worktree_path": "/tmp/workspace/issue-7",
            "assumed_uncertainties": [
                {
                    "question": "エラーメッセージの文言は?",
                    "severity": "minor",
                    "assumption": "一般的な文言を使う",
                }
            ],
        },
        result=None,
        error=None,
        created_at=now,
        updated_at=now,
    )
    kwargs.update(overrides)
    return Job(**kwargs)


def test_build_push_handler_returns_result_dict_on_success(tmp_path):
    adapter = _FakeGitLabPushWriter()
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    compute_actions = _FakeComputeCommitActions()

    handler = build_push_handler(
        adapter, workspace, compute_commit_actions=compute_actions
    )
    result = handler(_push_job())

    assert result == {
        "project": _PROJECT,
        "issue_iid": _ISSUE_IID,
        "pushed_commit_sha": "pushed-sha",
        "remote_branch": f"ai/issue-{_ISSUE_IID}",
        "merge_request_iid": 42,
        "merge_request_web_url": "https://gitlab.example.com/group/project/-/merge_requests/42",
    }


def test_build_push_handler_computes_actions_against_default_branch(tmp_path):
    adapter = _FakeGitLabPushWriter(default_branch="develop")
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    compute_actions = _FakeComputeCommitActions()

    handler = build_push_handler(
        adapter, workspace, compute_commit_actions=compute_actions
    )
    handler(_push_job())

    assert compute_actions.calls == [
        (Path("/tmp/workspace/issue-7"), "develop", "after-sha")
    ]


def test_build_push_handler_pushes_actions_to_remote_branch(tmp_path):
    adapter = _FakeGitLabPushWriter()
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    compute_actions = _FakeComputeCommitActions()

    handler = build_push_handler(
        adapter, workspace, compute_commit_actions=compute_actions
    )
    handler(_push_job())

    assert len(adapter.push_file_changes_calls) == 1
    project, branch, commit_message, actions = adapter.push_file_changes_calls[0]
    assert project == _PROJECT
    assert branch == f"ai/issue-{_ISSUE_IID}"
    assert commit_message == "タスク1を実装"
    assert actions == compute_actions._actions


def test_build_push_handler_falls_back_to_summary_when_commit_message_is_none(
    tmp_path,
):
    adapter = _FakeGitLabPushWriter()
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    compute_actions = _FakeComputeCommitActions()

    handler = build_push_handler(
        adapter, workspace, compute_commit_actions=compute_actions
    )
    job = _push_job(payload={**_push_job().payload, "commit_message": None})
    handler(job)

    assert adapter.push_file_changes_calls[0][2] == "タスク1を実装した"


def test_build_push_handler_creates_merge_request_with_required_sections(tmp_path):
    adapter = _FakeGitLabPushWriter()
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    compute_actions = _FakeComputeCommitActions()

    handler = build_push_handler(
        adapter, workspace, compute_commit_actions=compute_actions
    )
    handler(_push_job())

    assert len(adapter.create_merge_request_calls) == 1
    project, source_branch, target_branch, title, description = (
        adapter.create_merge_request_calls[0]
    )
    assert project == _PROJECT
    assert source_branch == f"ai/issue-{_ISSUE_IID}"
    assert target_branch == "main"
    assert f"Closes #{_ISSUE_IID}" in description
    assert "実装計画の本文です。" in description
    assert "と仮定して実装した" in description


def test_build_push_handler_discards_worktree_after_success(tmp_path):
    # push・MR作成の両方が成功した後にworktreeを破棄する(ADR-0034「論点4」)
    adapter = _FakeGitLabPushWriter()
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    compute_actions = _FakeComputeCommitActions()

    handler = build_push_handler(
        adapter, workspace, compute_commit_actions=compute_actions
    )
    handler(_push_job())

    assert workspace.discard_for_issue_calls == [(_PROJECT, _ISSUE_IID)]


def test_build_push_handler_raises_no_file_changes_error_when_actions_empty(tmp_path):
    adapter = _FakeGitLabPushWriter()
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    compute_actions = _FakeComputeCommitActions(actions=[])

    handler = build_push_handler(
        adapter, workspace, compute_commit_actions=compute_actions
    )

    with pytest.raises(NoFileChangesError):
        handler(_push_job())

    assert adapter.push_file_changes_calls == []
    assert adapter.create_merge_request_calls == []
    assert workspace.discard_for_issue_calls == []


def test_build_push_handler_does_not_discard_worktree_when_push_fails(tmp_path):
    adapter = _FakeGitLabPushWriter(push_error=GitLabApiError("boom", status_code=500))
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    compute_actions = _FakeComputeCommitActions()

    handler = build_push_handler(
        adapter, workspace, compute_commit_actions=compute_actions
    )

    with pytest.raises(GitLabApiError):
        handler(_push_job())

    assert adapter.create_merge_request_calls == []
    assert workspace.discard_for_issue_calls == []


def test_build_push_handler_does_not_discard_worktree_when_merge_request_creation_fails(
    tmp_path,
):
    adapter = _FakeGitLabPushWriter(
        create_merge_request_error=GitLabApiError("boom", status_code=500)
    )
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    compute_actions = _FakeComputeCommitActions()

    handler = build_push_handler(
        adapter, workspace, compute_commit_actions=compute_actions
    )

    with pytest.raises(GitLabApiError):
        handler(_push_job())

    assert workspace.discard_for_issue_calls == []


# --- RunnerDispatcher.run_once ---


def test_run_once_returns_false_when_no_job_available():
    repo = _FakeJobRepository([None])
    dispatcher = RunnerDispatcher(repo, {}, worker_id=_WORKER_ID)

    assert dispatcher.run_once() is False
    assert repo.complete_calls == []
    assert repo.fail_calls == []


def test_run_once_completes_job_on_handler_success():
    job = _job()
    repo = _FakeJobRepository([job])
    handler_calls = []

    def _handler(received_job: Job) -> dict:
        handler_calls.append(received_job)
        return {"ok": True}

    dispatcher = RunnerDispatcher(
        repo, {JobType.REVIEW: _handler}, worker_id=_WORKER_ID
    )

    processed = dispatcher.run_once()

    assert processed is True
    assert handler_calls == [job]
    assert repo.complete_calls == [(job.id, _WORKER_ID, {"ok": True})]
    assert repo.fail_calls == []


# --- on_job_completed(M4-10, ADR-0035)---


def test_run_once_calls_on_job_completed_with_the_completed_job_after_success():
    job = _job()
    repo = _FakeJobRepository([job])
    completed_jobs: list[Job] = []

    dispatcher = RunnerDispatcher(
        repo,
        {JobType.REVIEW: lambda received_job: {"ok": True}},
        worker_id=_WORKER_ID,
        on_job_completed=completed_jobs.append,
    )

    dispatcher.run_once()

    assert len(completed_jobs) == 1
    assert completed_jobs[0].id == job.id
    assert completed_jobs[0].status == JobStatus.DONE
    assert completed_jobs[0].result == {"ok": True}


def test_run_once_does_not_call_on_job_completed_when_handler_fails():
    job = _job()
    repo = _FakeJobRepository([job])
    completed_jobs: list[Job] = []

    def _handler(received_job: Job) -> dict:
        raise RuntimeError("boom")

    dispatcher = RunnerDispatcher(
        repo,
        {JobType.REVIEW: _handler},
        worker_id=_WORKER_ID,
        on_job_completed=completed_jobs.append,
    )

    dispatcher.run_once()

    assert completed_jobs == []


def test_run_once_does_not_call_on_job_completed_when_waiting_for_human():
    job = _job()
    repo = _FakeJobRepository([job])
    completed_jobs: list[Job] = []

    def _handler(received_job: Job) -> dict:
        raise WaitingForHumanError(
            {"questions": [{"question": "q?", "severity": "critical"}]}
        )

    dispatcher = RunnerDispatcher(
        repo,
        {JobType.REVIEW: _handler},
        worker_id=_WORKER_ID,
        on_job_completed=completed_jobs.append,
    )

    dispatcher.run_once()

    assert completed_jobs == []
    assert repo.wait_for_human_calls != []


def test_run_once_does_not_call_on_job_completed_when_job_type_not_implemented():
    job = _job(job_type=JobType.ISSUE_ANALYSIS, payload={})
    repo = _FakeJobRepository([job])
    completed_jobs: list[Job] = []

    dispatcher = RunnerDispatcher(
        repo,
        {},
        worker_id=_WORKER_ID,
        job_types=(JobType.ISSUE_ANALYSIS,),
        on_job_completed=completed_jobs.append,
    )

    dispatcher.run_once()

    assert completed_jobs == []


def test_run_once_on_job_completed_exception_does_not_crash_dispatcher(caplog):
    # フェーズ連鎖(M4-10)の失敗は、既に成功した今回のJobの完了確定を巻き戻す理由にはならない
    job = _job()
    repo = _FakeJobRepository([job])

    def _failing_hook(completed_job: Job) -> None:
        raise RuntimeError("advance_pipeline boom")

    dispatcher = RunnerDispatcher(
        repo,
        {JobType.REVIEW: lambda received_job: {"ok": True}},
        worker_id=_WORKER_ID,
        on_job_completed=_failing_hook,
    )

    processed = dispatcher.run_once()

    assert processed is True
    # complete自体は既に成功している(フックの失敗で巻き戻らない)
    assert repo.complete_calls == [(job.id, _WORKER_ID, {"ok": True})]
    assert repo.fail_calls == []


def test_run_once_without_on_job_completed_does_not_require_the_hook():
    # on_job_completed省略時(既定None)でも従来通り動作する(後方互換)
    job = _job()
    repo = _FakeJobRepository([job])

    dispatcher = RunnerDispatcher(
        repo, {JobType.REVIEW: lambda received_job: {"ok": True}}, worker_id=_WORKER_ID
    )

    processed = dispatcher.run_once()

    assert processed is True
    assert repo.complete_calls == [(job.id, _WORKER_ID, {"ok": True})]


def test_run_once_fails_with_retry_true_when_handler_raises(caplog):
    job = _job()
    repo = _FakeJobRepository([job])

    def _handler(received_job: Job) -> dict:
        raise RuntimeError("boom")

    dispatcher = RunnerDispatcher(
        repo, {JobType.REVIEW: _handler}, worker_id=_WORKER_ID
    )

    processed = dispatcher.run_once()

    assert processed is True
    assert repo.complete_calls == []
    assert repo.fail_calls == [(job.id, _WORKER_ID, "boom", True)]


def test_run_once_waits_for_human_when_handler_raises_waiting_for_human_error():
    # ADR-0026: WaitingForHumanErrorはfail/completeどちらでもなくwait_for_humanを呼ぶ
    job = _job()
    repo = _FakeJobRepository([job])
    questions_result = {"questions": [{"question": "q?", "severity": "critical"}]}

    def _handler(received_job: Job) -> dict:
        raise WaitingForHumanError(questions_result)

    dispatcher = RunnerDispatcher(
        repo, {JobType.REVIEW: _handler}, worker_id=_WORKER_ID
    )

    processed = dispatcher.run_once()

    assert processed is True
    assert repo.complete_calls == []
    assert repo.fail_calls == []
    assert repo.wait_for_human_calls == [(job.id, _WORKER_ID, questions_result)]


def test_run_once_fails_with_retry_false_when_job_type_not_implemented():
    # handlersにJobTypeが登録されていない(ADR-0016: 未実装種別はNotImplementedErrorの契約)。
    # job_typesを明示指定してclaim対象に含めることで、この経路を検証できる
    job = _job(job_type=JobType.ISSUE_ANALYSIS, payload={})
    repo = _FakeJobRepository([job])
    dispatcher = RunnerDispatcher(
        repo,
        {},
        worker_id=_WORKER_ID,
        job_types=(JobType.ISSUE_ANALYSIS,),
    )

    processed = dispatcher.run_once()

    assert processed is True
    assert repo.complete_calls == []
    assert len(repo.fail_calls) == 1
    fail_job_id, fail_worker_id, error, retry = repo.fail_calls[0]
    assert fail_job_id == job.id
    assert fail_worker_id == _WORKER_ID
    assert "issue-analysis" in error
    assert retry is False


def test_default_job_types_claim_only_registered_handler_types():
    repo = _FakeJobRepository([None])
    dispatcher = RunnerDispatcher(
        repo, {JobType.REVIEW: lambda job: None}, worker_id=_WORKER_ID
    )

    dispatcher.run_once()

    assert repo.claim_calls[0][1] == (JobType.REVIEW,)


def test_explicit_job_types_override_the_default():
    repo = _FakeJobRepository([None])
    dispatcher = RunnerDispatcher(
        repo,
        {JobType.REVIEW: lambda job: None},
        worker_id=_WORKER_ID,
        job_types=(JobType.REVIEW, JobType.ISSUE_ANALYSIS),
    )

    dispatcher.run_once()

    assert repo.claim_calls[0][1] == (JobType.REVIEW, JobType.ISSUE_ANALYSIS)


# --- heartbeat ---


def test_heartbeat_is_called_periodically_while_handler_runs():
    job = _job()
    repo = _FakeJobRepository([job])

    def _slow_handler(received_job: Job) -> dict:
        time.sleep(0.15)
        return {}

    dispatcher = RunnerDispatcher(
        repo,
        {JobType.REVIEW: _slow_handler},
        worker_id=_WORKER_ID,
        heartbeat_interval_seconds=0.02,
    )

    dispatcher.run_once()

    assert len(repo.heartbeat_calls) >= 2
    assert repo.complete_calls == [(job.id, _WORKER_ID, {})]


def test_heartbeat_lease_lost_does_not_interrupt_handler():
    job = _job()
    repo = _FakeJobRepository([job], heartbeat_error=LeaseLostError("lost"))

    def _slow_handler(received_job: Job) -> dict:
        time.sleep(0.05)
        return {"done": True}

    dispatcher = RunnerDispatcher(
        repo,
        {JobType.REVIEW: _slow_handler},
        worker_id=_WORKER_ID,
        heartbeat_interval_seconds=0.02,
    )

    dispatcher.run_once()

    # heartbeatがLeaseLostErrorを送出しても、handler本体は最後まで実行され完了する
    assert repo.complete_calls == [(job.id, _WORKER_ID, {"done": True})]
    assert len(repo.heartbeat_calls) >= 1


# --- run_forever ---


def test_run_forever_returns_immediately_when_stop_event_already_set():
    stop_event = threading.Event()
    stop_event.set()
    repo = _FakeJobRepository([])
    dispatcher = RunnerDispatcher(repo, {}, worker_id=_WORKER_ID)

    dispatcher.run_forever(stop_event)

    assert repo.claim_calls == []


def test_run_forever_processes_jobs_and_polls_until_stopped():
    stop_event = threading.Event()
    processed_job_ids: list[str] = []

    def _handler(received_job: Job) -> dict:
        processed_job_ids.append(received_job.id)
        if len(processed_job_ids) >= 2:
            stop_event.set()
        return {}

    jobs = [_job(id="job-1"), _job(id="job-2"), None, None]
    repo = _FakeJobRepository(jobs)
    dispatcher = RunnerDispatcher(
        repo,
        {JobType.REVIEW: _handler},
        worker_id=_WORKER_ID,
        poll_interval_seconds=0.01,
    )

    # 万一stop_eventが想定通りセットされなかった場合にテストがハングしないよう保険をかける
    threading.Timer(2.0, stop_event.set).start()
    dispatcher.run_forever(stop_event)

    assert processed_job_ids == ["job-1", "job-2"]


# --- module-level defaults / composition root ---


def test_default_worker_id_contains_hostname_and_pid():
    worker_id = default_worker_id()

    assert ":" in worker_id
    assert str(__import__("os").getpid()) in worker_id


def test_default_intervals_are_positive():
    assert DEFAULT_POLL_INTERVAL_SECONDS > 0
    assert DEFAULT_HEARTBEAT_INTERVAL_SECONDS > 0


def test_run_dispatcher_run_once_with_no_jobs_does_not_crash(tmp_path):
    # 実サービス(GitLab/git/Claude Code)には繋がない。claimがNoneを返すだけの経路のため
    # GitLabRestAdapter等の具象実装は構築されるが実際には呼ばれない
    config = _config(tmp_path, job_db_path=str(tmp_path / "job.db"))

    run_dispatcher(config, run_once=True)


def test_run_dispatcher_returns_immediately_when_stop_event_preset(tmp_path):
    # `watch`のProcessLockに相当する多重起動防止はworkerには無い(ADR-0022)。
    # stop_eventを起動前にセットしておけばclaimループ本体に一切入らない
    config = _config(tmp_path, job_db_path=str(tmp_path / "job.db"))
    stop_event = threading.Event()
    stop_event.set()

    run_dispatcher(config, stop_event=stop_event)


def test_run_dispatcher_does_not_prevent_a_second_concurrent_invocation(tmp_path):
    # `worker`は同一job_db_pathへの複数プロセス同時起動を前提とする設計であり、`watch`と
    # 異なりProcessLockを取得しない(ADR-0022「決定」)。2回連続で呼んでもAlreadyRunningError
    # 等の多重起動エラーにならないことを確認する
    config = _config(tmp_path, job_db_path=str(tmp_path / "job.db"))

    run_dispatcher(config, run_once=True)
    run_dispatcher(config, run_once=True)


def test_run_dispatcher_wires_advance_pipeline_hook_as_on_job_completed(
    tmp_path, monkeypatch
):
    # run_dispatcher(合成ルート)がadvance_pipeline_hook(job_repo)をon_job_completedとして
    # 正しく束縛していることを検証する(M4-10, ADR-0035)。実サービス(GitLab/Claude Code)には
    # 繋がないよう、build_job_handlersとadvance_pipeline_hook自体をモンキーパッチする
    hook_bound_repos: list[object] = []
    completed_jobs: list[Job] = []

    def _fake_advance_pipeline_hook(job_repo):
        hook_bound_repos.append(job_repo)

        def _hook(completed_job: Job) -> None:
            completed_jobs.append(completed_job)

        return _hook

    monkeypatch.setattr(
        "gitlab_ai_platform.cli.dispatcher.advance_pipeline_hook",
        _fake_advance_pipeline_hook,
    )
    monkeypatch.setattr(
        "gitlab_ai_platform.cli.dispatcher.build_job_handlers",
        lambda adapter, workspace, runner, store, config: {
            JobType.REVIEW: lambda job: {"ok": True}
        },
    )

    config = _config(tmp_path, job_db_path=str(tmp_path / "job.db"))
    job_repo = SqliteJobRepository(config.job_db_path)
    enqueued = job_repo.enqueue(
        JobType.REVIEW, {"project": _PROJECT, "mr_iid": _MR_IID}
    )
    job_repo.close()

    run_dispatcher(config, run_once=True)

    assert len(hook_bound_repos) == 1
    assert len(completed_jobs) == 1
    assert completed_jobs[0].id == enqueued.id
    assert completed_jobs[0].status == JobStatus.DONE
    assert completed_jobs[0].result == {"ok": True}
