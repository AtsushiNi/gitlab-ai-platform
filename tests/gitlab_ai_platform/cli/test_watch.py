from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from gitlab_ai_platform.cli import watch as watch_module
from gitlab_ai_platform.cli.lock import AlreadyRunningError, ProcessLock
from gitlab_ai_platform.cli.watch import build_on_detected, run_watch, run_watch_loop
from gitlab_ai_platform.config import Config
from gitlab_ai_platform.gitlab_adapter.types import (
    Discussion,
    MergeRequest,
    MergeRequestDiff,
)
from gitlab_ai_platform.poller import DetectedReview
from gitlab_ai_platform.runner import RunResult
from gitlab_ai_platform.store import ReviewStatus, SqliteStateStore
from gitlab_ai_platform.workspace import WorktreeHandle
from gitlab_ai_platform.workspace.errors import GitCommandError

_LABEL = "レビュー待ち"
_PROJECT = "group/project"


def _config(tmp_path: Path, **overrides) -> Config:
    kwargs = dict(
        gitlab_url="https://gitlab.example.com",
        gitlab_token="secret-token",
        projects=[_PROJECT],
        poll_interval_seconds=1,
        max_parallel=5,
        review_label=_LABEL,
        workspace_root=str(tmp_path / "workspace"),
        workspace_max_disk_mb=1000,
        runner_log_dir=str(tmp_path / "logs"),
        runner_timeout_seconds=1800,
        reviews_root=str(tmp_path / "reviews"),
        state_db_path=":memory:",
    )
    kwargs.update(overrides)
    return Config.from_raw(**kwargs)


def _mr(iid: int, sha: str) -> MergeRequest:
    return MergeRequest(
        project=_PROJECT,
        iid=iid,
        title=f"MR {iid}",
        description="",
        state="opened",
        source_branch=f"feature-{iid}",
        target_branch="main",
        sha=sha,
        author="alice",
        labels=(_LABEL,),
    )


class _FakeGitLabReader:
    """`GitLabReader`を満たすテスト用フェイク。`list_merge_requests`は固定のMR一覧を返す。"""

    def __init__(self, merge_requests: list[MergeRequest]) -> None:
        self._merge_requests = merge_requests
        self._by_iid = {mr.iid: mr for mr in merge_requests}

    def get_version(self) -> str:
        raise NotImplementedError

    def list_merge_requests(self, project: str, *, labels=(), state="opened"):
        return list(self._merge_requests)

    def get_merge_request(self, project: str, mr_iid: int) -> MergeRequest:
        return self._by_iid[mr_iid]

    def get_merge_request_diffs(self, project: str, mr_iid: int):
        return [
            MergeRequestDiff(
                old_path="a.py", new_path="a.py", diff="-old\n+new", new_file=False,
                renamed_file=False, deleted_file=False,
            )
        ]

    def list_merge_request_discussions(self, project: str, mr_iid: int) -> list[Discussion]:
        return []


class _FakeWorkspaceManager:
    """MR IIDごとに例外を差し込める`WorkspaceManager`フェイク。"""

    def __init__(self, worktree_path: Path, *, fail_for: dict[int, Exception] | None = None) -> None:
        self._worktree_path = worktree_path
        self._fail_for = fail_for or {}
        self.prepare_calls: list[tuple[str, int, str]] = []

    def prepare(self, project: str, mr_iid: int, ref: str) -> WorktreeHandle:
        self.prepare_calls.append((project, mr_iid, ref))
        if mr_iid in self._fail_for:
            raise self._fail_for[mr_iid]
        return WorktreeHandle(
            project=project, mr_iid=mr_iid, path=self._worktree_path, branch=f"mr-{mr_iid}", sha=ref
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


def test_build_on_detected_runs_execute_review_and_marks_done(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader([_mr(1, "sha-1")])
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")

    try:
        on_detected = build_on_detected(adapter, workspace, runner, store, config)
        on_detected(DetectedReview(project=_PROJECT, mr_iid=1, commit_sha="sha-1"))

        record = store.find(_PROJECT, 1, "sha-1")
        assert record.status == ReviewStatus.DONE
        assert workspace.prepare_calls == [(_PROJECT, 1, "sha-1")]
        assert runner.run_calls == [(_PROJECT, 1)]
    finally:
        store.close()


def test_build_on_detected_logs_and_continues_on_known_pipeline_error(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader([_mr(1, "sha-1")])
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(
        worktree_path,
        fail_for={1: GitCommandError("boom", command=["git"], returncode=1, stderr="")},
    )
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")

    try:
        on_detected = build_on_detected(adapter, workspace, runner, store, config)
        # 例外を送出せず、ログに記録して静かに終わる(呼び出し元のループを止めない)
        on_detected(DetectedReview(project=_PROJECT, mr_iid=1, commit_sha="sha-1"))

        record = store.find(_PROJECT, 1, "sha-1")
        assert record.status == ReviewStatus.FAILED
        assert runner.run_calls == []
    finally:
        store.close()


def test_build_on_detected_propagates_unexpected_exception(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader([_mr(1, "sha-1")])
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path, fail_for={1: RuntimeError("bug")})
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")

    try:
        on_detected = build_on_detected(adapter, workspace, runner, store, config)
        with pytest.raises(RuntimeError, match="bug"):
            on_detected(DetectedReview(project=_PROJECT, mr_iid=1, commit_sha="sha-1"))
    finally:
        store.close()


def test_run_watch_loop_processes_all_detected_reviews_then_stops(tmp_path, monkeypatch):
    # run_watch_loopはMrPoller.runにon_detectedを渡すだけの薄い結線であることを検証する。
    # build_on_detectedをラップし、そのサイクルで検出された全MRの処理が終わった時点で
    # stop_eventをセットすることで、poll_interval_seconds=0でも無限ループにならないようにする
    config = _config(tmp_path)
    mrs = [_mr(1, "sha-1"), _mr(2, "sha-2")]
    adapter = _FakeGitLabReader(mrs)
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")
    stop_event = threading.Event()

    original_build_on_detected = watch_module.build_on_detected

    def _stopping_build_on_detected(adapter_, workspace_, runner_, store_, config_):
        inner = original_build_on_detected(adapter_, workspace_, runner_, store_, config_)
        seen: list[DetectedReview] = []

        def _wrapped(review: DetectedReview) -> None:
            inner(review)
            seen.append(review)
            if len(seen) == len(mrs):
                stop_event.set()

        return _wrapped

    monkeypatch.setattr(watch_module, "build_on_detected", _stopping_build_on_detected)

    try:
        run_watch_loop(adapter, workspace, runner, store, config, stop_event=stop_event)

        assert runner.run_calls == [(_PROJECT, 1), (_PROJECT, 2)]
        assert store.find(_PROJECT, 1, "sha-1").status == ReviewStatus.DONE
        assert store.find(_PROJECT, 2, "sha-2").status == ReviewStatus.DONE
    finally:
        store.close()


def test_run_watch_acquires_lock_and_releases_on_return(tmp_path):
    config = _config(tmp_path, state_db_path=str(tmp_path / "state.db"))
    stop_event = threading.Event()
    stop_event.set()  # 即座に停止させ、実際のGitLab/git/Claude Code呼び出しを避ける

    run_watch(config, stop_event=stop_event)

    lock_path = tmp_path / "state.lock"
    assert lock_path.is_file()
    # run_watch終了後はロックが解放され、再取得できる
    reacquired = ProcessLock(lock_path)
    reacquired.acquire()
    reacquired.release()


def test_run_watch_raises_already_running_when_lock_is_held(tmp_path):
    config = _config(tmp_path, state_db_path=str(tmp_path / "state.db"))
    lock_path = tmp_path / "state.lock"
    holder = ProcessLock(lock_path)
    holder.acquire()

    try:
        stop_event = threading.Event()
        stop_event.set()
        with pytest.raises(AlreadyRunningError):
            run_watch(config, stop_event=stop_event)
    finally:
        holder.release()
