from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from gitlab_ai_platform.cli.dispatcher import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    RunnerDispatcher,
    build_job_handlers,
    build_review_handler,
    default_worker_id,
    run_dispatcher,
)
from gitlab_ai_platform.config import Config
from gitlab_ai_platform.gitlab_adapter.types import (
    Discussion,
    MergeRequest,
    MergeRequestDiff,
)
from gitlab_ai_platform.job.errors import LeaseLostError
from gitlab_ai_platform.job.protocol import Job, JobStatus, JobType
from gitlab_ai_platform.runner import RunResult
from gitlab_ai_platform.store import ReviewStatus, SqliteStateStore
from gitlab_ai_platform.workspace import WorktreeHandle

_PROJECT = "group/project"
_MR_IID = 1
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
    """`GitLabReader`を満たすテスト用フェイク(固定のMRを1件だけ返す)。"""

    def __init__(self, merge_request: MergeRequest) -> None:
        self._merge_request = merge_request

    def get_version(self) -> str:
        raise NotImplementedError

    def list_merge_requests(self, project: str, *, labels=(), state="opened"):
        raise NotImplementedError

    def get_merge_request(self, project: str, mr_iid: int) -> MergeRequest:
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


class _FakeWorkspaceManager:
    def __init__(self, worktree_path: Path) -> None:
        self._worktree_path = worktree_path
        self.prepare_calls: list[tuple[str, int, str]] = []

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


class _FakeClaudeCodeRunner:
    def __init__(self, tmp_path: Path) -> None:
        self._tmp_path = tmp_path
        self.run_calls: list[tuple[str, int]] = []

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


class _FakeJobRepository:
    """`RunnerDispatcher`が使うメソッド(claim/heartbeat/complete/fail)だけを満たす手書きフェイク。

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
        self.claim_calls: list[tuple[str, tuple[JobType, ...] | None, int]] = []
        self.heartbeat_calls: list[tuple[str, str, int]] = []
        self.complete_calls: list[tuple[str, str, dict | None]] = []
        self.fail_calls: list[tuple[str, str, str, bool]] = []

    def claim(self, worker_id, *, job_types=None, visibility_timeout_seconds=600):
        self.claim_calls.append((worker_id, job_types, visibility_timeout_seconds))
        if self._jobs_to_claim:
            return self._jobs_to_claim.pop(0)
        return None

    def heartbeat(self, job_id, worker_id, *, visibility_timeout_seconds=600):
        self.heartbeat_calls.append((job_id, worker_id, visibility_timeout_seconds))
        if self._heartbeat_error is not None:
            raise self._heartbeat_error

    def complete(self, job_id, worker_id, result=None):
        self.complete_calls.append((job_id, worker_id, result))

    def fail(self, job_id, worker_id, error, *, retry=True):
        self.fail_calls.append((job_id, worker_id, error, retry))


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


def test_build_job_handlers_registers_only_review_type(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")

    try:
        handlers = build_job_handlers(adapter, workspace, runner, store, config)
        # M3-3時点で実際にRunnerが処理できるのはreviewのみ(ADR-0016)
        assert set(handlers) == {JobType.REVIEW}
    finally:
        store.close()


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
