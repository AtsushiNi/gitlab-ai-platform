from __future__ import annotations

import dataclasses
import http.client
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from gitlab_ai_platform.cli import watch as watch_module
from gitlab_ai_platform.cli.lock import AlreadyRunningError, ProcessLock
from gitlab_ai_platform.cli.watch import (
    _lock_path_for,
    build_on_detected,
    run_watch,
    run_watch_loop,
)
from gitlab_ai_platform.config import Config
from gitlab_ai_platform.gitlab_adapter.types import (
    Discussion,
    MergeRequest,
    MergeRequestDiff,
)
from gitlab_ai_platform.job import JobStatus, JobType, SqliteJobRepository
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
        store_backend="sqlite",
        store_postgres_host="localhost",
        store_postgres_port=5432,
        store_postgres_dbname="gitlab_ai_platform",
        store_postgres_user="gitlab_ai_platform",
        store_postgres_password="",
        job_db_path=":memory:",
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


def _free_port() -> int:
    # OSに空きポートを割り当てさせてから即座に閉じる(テスト用のWebhookサーバーの待受先)。
    # 割り当てから実際のbindまでの間に他プロセスに奪われる可能性はゼロではないが、
    # テスト実行環境でのポート衝突を避けるための簡便な方法として許容する
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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


class _FakeGitLabReaderDriftingSha(_FakeGitLabReader):
    """検出(`list_merge_requests`)後、実行時点(`get_merge_request`)には別のcommitに
    進んでしまっているケースを再現するフェイク(常駐モードでの検出〜実行の間のレース用)。"""

    def __init__(self, mr: MergeRequest, drifted_sha: str) -> None:
        super().__init__([mr])
        self._drifted_sha = drifted_sha

    def get_merge_request(self, project: str, mr_iid: int) -> MergeRequest:
        mr = super().get_merge_request(project, mr_iid)
        return dataclasses.replace(mr, sha=self._drifted_sha)


class _FakeGitLabReaderWebhookOnly(_FakeGitLabReader):
    """`list_merge_requests`は常に空を返す(MR PollerからはMRが見えない)が、
    `get_merge_request`等は通常通り応答するフェイク(M3-6のWebhook経由検出テスト用。
    `execute_review`はWebhookが検出したcommitに対して`get_merge_request`等を直接呼ぶため、
    Pollerに検出させずにWebhook経由の検出だけを再現できる)。"""

    def list_merge_requests(
        self, project: str, *, labels: tuple[str, ...] = (), state: str = "opened"
    ) -> list[MergeRequest]:
        return []


class _FakeWorkspaceManager:
    """MR IIDごとに例外を差し込める`WorkspaceManager`フェイク。"""

    def __init__(
        self, worktree_path: Path, *, fail_for: dict[int, Exception] | None = None
    ) -> None:
        self._worktree_path = worktree_path
        self._fail_for = fail_for or {}
        self.prepare_calls: list[tuple[str, int, str]] = []

    def prepare(self, project: str, mr_iid: int, ref: str) -> WorktreeHandle:
        self.prepare_calls.append((project, mr_iid, ref))
        if mr_iid in self._fail_for:
            raise self._fail_for[mr_iid]
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


class _ConcurrencyTrackingClaudeCodeRunner(_FakeClaudeCodeRunner):
    """`run`の同時実行数を計測するフェイク(M2-1のワーカープール検証用)。

    `hold_seconds`だけ`run`の中で待つことで、複数MRの`run`呼び出しが実際に重なる
    (真に並行実行されている)ことを観測できる時間の余白を作る。
    """

    def __init__(self, tmp_path: Path, *, hold_seconds: float = 0.05) -> None:
        super().__init__(tmp_path)
        self._hold_seconds = hold_seconds
        self._active_lock = threading.Lock()
        self._active = 0
        self.max_active = 0

    def run(self, *args, **kwargs) -> RunResult:
        with self._active_lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            time.sleep(self._hold_seconds)
            return super().run(*args, **kwargs)
        finally:
            with self._active_lock:
                self._active -= 1


def _run_until_all_detected_reviews_processed(
    adapter,
    workspace,
    runner,
    store,
    job_repo,
    config,
    *,
    expected_count: int,
    monkeypatch,
) -> None:
    """`run_watch_loop`を、対象の全MRが処理し終わった時点で`stop_event`をセットして止める。

    複数のテストで使う共通ヘルパー(M2-1で並行実行になったため、`build_on_detected`の
    呼び出し完了を待つだけでは「そのサイクルの全MR処理完了」を表せない。処理件数を
    数えて判定する)。
    """
    stop_event = threading.Event()
    original_build_on_detected = watch_module.build_on_detected
    seen_lock = threading.Lock()
    seen: list[DetectedReview] = []

    def _counting_build_on_detected(
        adapter_, workspace_, runner_, store_, job_repo_, config_
    ):
        inner = original_build_on_detected(
            adapter_, workspace_, runner_, store_, job_repo_, config_
        )

        def _wrapped(review: DetectedReview) -> None:
            try:
                inner(review)
            finally:
                with seen_lock:
                    seen.append(review)
                    if len(seen) == expected_count:
                        stop_event.set()

        return _wrapped

    monkeypatch.setattr(watch_module, "build_on_detected", _counting_build_on_detected)
    run_watch_loop(
        adapter, workspace, runner, store, job_repo, config, stop_event=stop_event
    )


def test_build_on_detected_runs_execute_review_and_marks_done(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader([_mr(1, "sha-1")])
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")
    job_repo = SqliteJobRepository(":memory:")

    try:
        on_detected = build_on_detected(
            adapter, workspace, runner, store, job_repo, config
        )
        on_detected(DetectedReview(project=_PROJECT, mr_iid=1, commit_sha="sha-1"))

        record = store.find(_PROJECT, 1, "sha-1")
        assert record.status == ReviewStatus.DONE
        assert workspace.prepare_calls == [(_PROJECT, 1, "sha-1")]
        assert runner.run_calls == [(_PROJECT, 1)]
        # M3-1(#91): 検出されたレビューがreview Jobとして起票・完了していること
        done_jobs = job_repo.list_by_status(JobStatus.DONE)
        assert len(done_jobs) == 1
        assert done_jobs[0].job_type == JobType.REVIEW
    finally:
        job_repo.close()
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
    job_repo = SqliteJobRepository(":memory:")

    try:
        on_detected = build_on_detected(
            adapter, workspace, runner, store, job_repo, config
        )
        # 例外を送出せず、ログに記録して静かに終わる(呼び出し元のループを止めない)
        on_detected(DetectedReview(project=_PROJECT, mr_iid=1, commit_sha="sha-1"))

        record = store.find(_PROJECT, 1, "sha-1")
        assert record.status == ReviewStatus.FAILED
        assert runner.run_calls == []
        assert len(job_repo.list_by_status(JobStatus.FAILED)) == 1
    finally:
        job_repo.close()
        store.close()


def test_build_on_detected_uses_detected_sha_even_if_mr_has_moved_on(tmp_path):
    # 検出(起票)後、実行までの間に別のcommitがpushされていても、起票済みのshaに対して
    # レビューを行う(実行時点の最新shaを使うと、起票済み(project, mr_iid, 検出時sha)の
    # State Storeレコードが二度と遷移しない孤立レコードになってしまうため)
    config = _config(tmp_path)
    adapter = _FakeGitLabReaderDriftingSha(_mr(1, "sha-1"), drifted_sha="sha-2")
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")
    job_repo = SqliteJobRepository(":memory:")

    try:
        on_detected = build_on_detected(
            adapter, workspace, runner, store, job_repo, config
        )
        on_detected(DetectedReview(project=_PROJECT, mr_iid=1, commit_sha="sha-1"))

        assert store.find(_PROJECT, 1, "sha-1").status == ReviewStatus.DONE
        assert workspace.prepare_calls == [(_PROJECT, 1, "sha-1")]
        # 新しいsha側は今回の検出サイクルでは起票されていないので、レコードは作られない
        # (Pollerが次回の走査で新規に検出・起票する)
        assert store.find(_PROJECT, 1, "sha-2") is None
    finally:
        job_repo.close()
        store.close()


def test_build_on_detected_propagates_unexpected_exception(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader([_mr(1, "sha-1")])
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path, fail_for={1: RuntimeError("bug")})
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")
    job_repo = SqliteJobRepository(":memory:")

    try:
        on_detected = build_on_detected(
            adapter, workspace, runner, store, job_repo, config
        )
        with pytest.raises(RuntimeError, match="bug"):
            on_detected(DetectedReview(project=_PROJECT, mr_iid=1, commit_sha="sha-1"))
    finally:
        job_repo.close()
        store.close()


def test_run_watch_loop_processes_all_detected_reviews_then_stops(
    tmp_path, monkeypatch
):
    # run_watch_loopはMrPoller.runの検出結果をワーカープール(M2-1)へ投入する薄い結線で
    # あることを検証する。全MRの処理が終わった時点でstop_eventをセットすることで、
    # poll_interval_seconds=0でも無限ループにならないようにする。
    # M2-1以降は複数MRを並行実行するため、実行完了の順序は保証されない
    # (runner.run_callsの並び順ではなく集合として比較する)
    config = _config(tmp_path)
    mrs = [_mr(1, "sha-1"), _mr(2, "sha-2")]
    adapter = _FakeGitLabReader(mrs)
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")
    job_repo = SqliteJobRepository(":memory:")

    try:
        _run_until_all_detected_reviews_processed(
            adapter,
            workspace,
            runner,
            store,
            job_repo,
            config,
            expected_count=len(mrs),
            monkeypatch=monkeypatch,
        )

        assert set(runner.run_calls) == {(_PROJECT, 1), (_PROJECT, 2)}
        assert store.find(_PROJECT, 1, "sha-1").status == ReviewStatus.DONE
        assert store.find(_PROJECT, 2, "sha-2").status == ReviewStatus.DONE
    finally:
        job_repo.close()
        store.close()


def test_run_watch_loop_processes_reviews_concurrently_up_to_max_parallel(
    tmp_path, monkeypatch
):
    # Issue #80「ワーカープール、同時実行数の設定」の中核: 複数MRのレビューが実際に
    # 並行実行されること、かつ同時実行数がconfig.max_parallelを超えないことを検証する
    max_parallel = 2
    config = _config(tmp_path, max_parallel=max_parallel)
    mrs = [_mr(i, f"sha-{i}") for i in range(1, 6)]
    adapter = _FakeGitLabReader(mrs)
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _ConcurrencyTrackingClaudeCodeRunner(tmp_path, hold_seconds=0.1)
    store = SqliteStateStore(":memory:")
    job_repo = SqliteJobRepository(":memory:")

    try:
        _run_until_all_detected_reviews_processed(
            adapter,
            workspace,
            runner,
            store,
            job_repo,
            config,
            expected_count=len(mrs),
            monkeypatch=monkeypatch,
        )

        assert len(runner.run_calls) == len(mrs)
        # 同時実行数がmax_parallelを超えないこと
        assert runner.max_active <= max_parallel
        # 実際に複数MRが同時に実行された(単なる逐次実行ではない)こと
        assert runner.max_active > 1
        for mr in mrs:
            assert store.find(_PROJECT, mr.iid, mr.sha).status == ReviewStatus.DONE
    finally:
        job_repo.close()
        store.close()


def test_run_watch_loop_isolates_worker_failure_from_other_reviews(tmp_path):
    # Issue #80「失敗時の隔離」: 1件のMRの処理で想定外の例外が起きても、並行実行中の
    # 他のMRの処理は完了する(State StoreがDONEへ更新される)ことを検証する。想定外の
    # 例外自体は`docs/adr/0009`の方針通りrun_watch_loopの外へ伝播する
    config = _config(tmp_path)
    mrs = [_mr(1, "sha-1"), _mr(2, "sha-2"), _mr(3, "sha-3")]
    adapter = _FakeGitLabReader(mrs)
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path, fail_for={2: RuntimeError("bug")})
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")
    job_repo = SqliteJobRepository(":memory:")
    stop_event = threading.Event()

    try:
        with pytest.raises(RuntimeError, match="bug"):
            run_watch_loop(
                adapter,
                workspace,
                runner,
                store,
                job_repo,
                config,
                stop_event=stop_event,
            )

        # MR2は失敗(FAILED)だが、MR1/MR3は影響を受けず完了している
        assert store.find(_PROJECT, 1, "sha-1").status == ReviewStatus.DONE
        assert store.find(_PROJECT, 2, "sha-2").status == ReviewStatus.FAILED
        assert store.find(_PROJECT, 3, "sha-3").status == ReviewStatus.DONE
    finally:
        job_repo.close()
        store.close()


def test_run_watch_loop_does_not_start_webhook_server_when_disabled(tmp_path):
    # webhook.enabled=false(既定)なら、run_watch_loopはWebhookサーバーを一切起動しない
    # (「片方だけでも動く」、docs/adr/0017-webhook-receiver.md)。ポートを予約だけしておき、
    # そのポートへの接続が拒否される(誰も待ち受けていない)ことで確認する
    port = _free_port()
    config = _config(tmp_path, webhook_enabled=False, webhook_port=port)
    adapter = _FakeGitLabReader([])
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")
    job_repo = SqliteJobRepository(":memory:")
    stop_event = threading.Event()
    stop_event.set()

    try:
        run_watch_loop(
            adapter, workspace, runner, store, job_repo, config, stop_event=stop_event
        )

        with pytest.raises(ConnectionRefusedError):
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).connect()
    finally:
        job_repo.close()
        store.close()


def test_run_watch_loop_processes_webhook_detected_review_via_same_pipeline(tmp_path):
    # webhook.enabled=trueの場合、Webhook経由で検出されたMRもPollerと同じ実行経路
    # (ReviewWorkerPool→execute_review_job)で処理され、State Storeが更新されることを検証する
    # (docs/adr/0017-webhook-receiver.md「検出後の実行経路を完全に共有する」)。
    port = _free_port()
    secret = "whsec-test-secret"
    config = _config(
        tmp_path,
        poll_interval_seconds=1,
        webhook_enabled=True,
        webhook_host="127.0.0.1",
        webhook_port=port,
        webhook_secret_token=secret,
    )
    # Pollerには見せず(list_merge_requestsは常に空)、Webhook経由でのみ検出させる。
    # `execute_review`が検出後に呼ぶget_merge_request等はMR1に応答できる必要がある
    adapter = _FakeGitLabReaderWebhookOnly([_mr(1, "sha-1")])
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(tmp_path)
    store = SqliteStateStore(":memory:")
    job_repo = SqliteJobRepository(":memory:")
    stop_event = threading.Event()

    thread = threading.Thread(
        target=run_watch_loop,
        args=(adapter, workspace, runner, store, job_repo, config),
        kwargs={"stop_event": stop_event},
    )
    thread.start()
    try:
        payload = json.dumps(
            {
                "object_kind": "merge_request",
                "object_attributes": {
                    "iid": 1,
                    "state": "opened",
                    "last_commit": {"id": "sha-1"},
                },
                "project": {"path_with_namespace": _PROJECT},
                "labels": [{"title": _LABEL}],
            }
        ).encode("utf-8")

        status = None
        for _ in range(50):  # サーバー起動を待つ(最大約2.5秒)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                conn.request(
                    "POST",
                    "/webhook",
                    body=payload,
                    headers={
                        "X-Gitlab-Event": "Merge Request Hook",
                        "X-Gitlab-Token": secret,
                    },
                )
                response = conn.getresponse()
                status = response.status
                response.read()
                conn.close()
                break
            except ConnectionRefusedError:
                time.sleep(0.05)
        assert status == 202

        for _ in range(50):  # ワーカースレッドでの処理完了を待つ(最大約2.5秒)
            record = store.find(_PROJECT, 1, "sha-1")
            if record is not None and record.status == ReviewStatus.DONE:
                break
            time.sleep(0.05)
        else:
            pytest.fail("Webhook経由のレビューが時間内に完了しなかった")

        assert runner.run_calls == [(_PROJECT, 1)]
    finally:
        stop_event.set()
        thread.join(timeout=5)
        job_repo.close()
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


def test_lock_path_for_memory_db_avoids_invalid_filename():
    # ":memory:"をそのまま`with_suffix`すると`:`を含む不正なファイル名になる
    # (Windowsでは`:`はドライブレター区切り用の予約文字)
    lock_path = _lock_path_for(":memory:")

    assert ":" not in lock_path.name
    assert lock_path.suffix == ".lock"


def test_run_watch_with_memory_state_db_does_not_crash_on_lock_acquisition(
    tmp_path, monkeypatch
):
    # ":memory:"はconfig.tomlの`[store] db_path`として設定可能な値であり、watchモードの
    # 起動処理(ロック取得含む)がそれで壊れないことを確認する
    monkeypatch.chdir(tmp_path)
    config = _config(tmp_path, state_db_path=":memory:")
    stop_event = threading.Event()
    stop_event.set()

    run_watch(config, stop_event=stop_event)
