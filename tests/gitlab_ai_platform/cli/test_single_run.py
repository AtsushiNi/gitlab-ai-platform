from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gitlab_ai_platform.cli.single_run import (
    _clone_url_for,
    _credential_helper,
    _find_previous_review_result,
    build_workspace_manager,
    execute_review,
)
from gitlab_ai_platform.config import GITLAB_TOKEN_ENV_KEY, Config
from gitlab_ai_platform.gitlab_adapter import GitLabApiError
from gitlab_ai_platform.gitlab_adapter.types import (
    Discussion,
    MergeRequest,
    MergeRequestDiff,
    Note,
)
from gitlab_ai_platform.review import ReviewResult, save_review
from gitlab_ai_platform.review.errors import ReviewOutputParseError
from gitlab_ai_platform.runner import RunResult
from gitlab_ai_platform.runner.errors import ClaudeCodeTimeoutError
from gitlab_ai_platform.store import ReviewStatus, SqliteStateStore
from gitlab_ai_platform.store.errors import StateStoreError
from gitlab_ai_platform.workspace import WorktreeHandle
from gitlab_ai_platform.workspace.errors import GitCommandError

_PROJECT = "group/project"
_MR_IID = 1
_SHA = "abcdef0123456789"


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
    )
    kwargs.update(overrides)
    return Config.from_raw(**kwargs)


def _merge_request() -> MergeRequest:
    return MergeRequest(
        project=_PROJECT,
        iid=_MR_IID,
        title="Fix bug",
        description="Fixes a bug.",
        state="opened",
        source_branch="fix",
        target_branch="main",
        sha=_SHA,
        author="alice",
    )


class _FakeGitLabReader:
    def __init__(
        self, merge_request: MergeRequest, *, fail: Exception | None = None
    ) -> None:
        self._merge_request = merge_request
        self._fail = fail
        self.get_merge_request_calls: list[tuple[str, int]] = []

    def get_version(self) -> str:
        raise NotImplementedError

    def list_merge_requests(self, project, *, labels=(), state="opened"):
        raise NotImplementedError

    def get_merge_request(self, project: str, mr_iid: int) -> MergeRequest:
        self.get_merge_request_calls.append((project, mr_iid))
        if self._fail is not None:
            raise self._fail
        return self._merge_request

    def get_merge_request_diffs(
        self, project: str, mr_iid: int
    ) -> list[MergeRequestDiff]:
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
        return [
            Discussion(
                id="1",
                notes=(
                    Note(
                        id=1,
                        body="LGTM",
                        author="bob",
                        created_at="2024-01-01T00:00:00Z",
                    ),
                ),
            )
        ]


class _FakeWorkspaceManager:
    def __init__(self, worktree_path: Path, *, fail: Exception | None = None) -> None:
        self._worktree_path = worktree_path
        self._fail = fail
        self.prepare_calls: list[tuple[str, int, str]] = []

    def prepare(self, project: str, mr_iid: int, ref: str) -> WorktreeHandle:
        self.prepare_calls.append((project, mr_iid, ref))
        if self._fail is not None:
            raise self._fail
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
    def __init__(
        self, run_result: RunResult | None = None, *, fail: Exception | None = None
    ) -> None:
        self._run_result = run_result
        self._fail = fail
        self.run_calls: list[dict] = []

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
        self.run_calls.append(
            {
                "worktree_path": worktree_path,
                "instructions": instructions,
                "context": context,
                "timeout_seconds": timeout_seconds,
                "allowed_tools": tuple(allowed_tools),
                "disallowed_tools": tuple(disallowed_tools),
                "permission_mode": permission_mode,
            }
        )
        if self._fail is not None:
            raise self._fail
        assert self._run_result is not None
        return self._run_result


def _run_result(
    tmp_path: Path, *, is_error: bool = False, findings: list | None = None
) -> RunResult:
    log_path = tmp_path / "run_log.json"
    log_path.write_text(json.dumps({"command": ["claude"]}), encoding="utf-8")

    payload = {
        "summary": "特に指摘なし" if not findings else "指摘あり",
        "findings": findings or [],
    }
    result_text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

    return RunResult(
        is_error=is_error,
        result_text=result_text,
        session_id="session-1",
        terminal_reason="success" if not is_error else "aborted_error",
        permission_denials=(),
        num_turns=1,
        total_cost_usd=0.01,
        timed_out=False,
        duration_seconds=1.0,
        log_path=log_path,
        raw={},
    )


def test_execute_review_happy_path_saves_review_and_marks_done(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(_run_result(tmp_path))
    store = SqliteStateStore(":memory:")

    try:
        result = execute_review(
            adapter, workspace, runner, store, config, _PROJECT, _MR_IID
        )

        assert result.project == _PROJECT
        assert result.mr_iid == _MR_IID
        assert result.sha == _SHA
        assert result.worktree_path == worktree_path
        assert result.review_result.summary == "特に指摘なし"
        assert result.review_paths.result_json.is_file()
        assert result.review_paths.result_md.is_file()
        assert result.review_paths.input_path.is_file()
        # input.md には instructions とMR固有情報(diff等)の両方が含まれる
        assert "Fix bug" in result.review_paths.input_path.read_text(encoding="utf-8")

        record = store.find(_PROJECT, _MR_IID, _SHA)
        assert record.status == ReviewStatus.DONE
        assert record.reviewed_at is not None
        assert record.result_path == str(result.review_paths.dir)

        assert workspace.prepare_calls == [(_PROJECT, _MR_IID, _SHA)]
        assert runner.run_calls[0]["timeout_seconds"] == config.runner_timeout_seconds
    finally:
        store.close()


def test_execute_review_overrides_timeout_and_tool_options(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(_run_result(tmp_path))
    store = SqliteStateStore(":memory:")

    try:
        execute_review(
            adapter,
            workspace,
            runner,
            store,
            config,
            _PROJECT,
            _MR_IID,
            timeout_seconds=42,
            allowed_tools=("Read",),
            disallowed_tools=("Bash",),
            permission_mode="plan",
        )

        call = runner.run_calls[0]
        assert call["timeout_seconds"] == 42
        assert call["allowed_tools"] == ("Read",)
        assert call["disallowed_tools"] == ("Bash",)
        assert call["permission_mode"] == "plan"
    finally:
        store.close()


def test_execute_review_does_not_ticket_on_gitlab_adapter_error(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request(), fail=GitLabApiError("boom"))
    workspace = _FakeWorkspaceManager(tmp_path / "worktree")
    runner = _FakeClaudeCodeRunner()
    store = SqliteStateStore(":memory:")

    try:
        with pytest.raises(GitLabApiError):
            execute_review(adapter, workspace, runner, store, config, _PROJECT, _MR_IID)

        assert store.find(_PROJECT, _MR_IID, _SHA) is None
        assert workspace.prepare_calls == []
    finally:
        store.close()


def test_execute_review_marks_failed_on_workspace_error(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request())
    workspace = _FakeWorkspaceManager(
        tmp_path / "worktree",
        fail=GitCommandError("boom", command=["git"], returncode=1, stderr=""),
    )
    runner = _FakeClaudeCodeRunner()
    store = SqliteStateStore(":memory:")

    try:
        with pytest.raises(GitCommandError):
            execute_review(adapter, workspace, runner, store, config, _PROJECT, _MR_IID)

        record = store.find(_PROJECT, _MR_IID, _SHA)
        assert record.status == ReviewStatus.FAILED
    finally:
        store.close()


def test_execute_review_marks_failed_on_runner_error(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(
        fail=ClaudeCodeTimeoutError(
            "timed out", timeout_seconds=1, log_path=tmp_path / "log.json", stderr=""
        )
    )
    store = SqliteStateStore(":memory:")

    try:
        with pytest.raises(ClaudeCodeTimeoutError):
            execute_review(adapter, workspace, runner, store, config, _PROJECT, _MR_IID)

        record = store.find(_PROJECT, _MR_IID, _SHA)
        assert record.status == ReviewStatus.FAILED
    finally:
        store.close()


def test_execute_review_marks_failed_on_review_parse_error(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(_run_result(tmp_path, is_error=True))
    store = SqliteStateStore(":memory:")

    try:
        with pytest.raises(ReviewOutputParseError):
            execute_review(adapter, workspace, runner, store, config, _PROJECT, _MR_IID)

        record = store.find(_PROJECT, _MR_IID, _SHA)
        assert record.status == ReviewStatus.FAILED
    finally:
        store.close()


def test_execute_review_rerun_on_same_commit_does_not_raise_duplicate_error(tmp_path):
    """デバッグ用途では同一commitへの再実行を繰り返す想定(MR Pollerと異なり無視しない)。"""
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    store = SqliteStateStore(":memory:")

    try:
        for _ in range(2):
            runner = _FakeClaudeCodeRunner(_run_result(tmp_path))
            result = execute_review(
                adapter, workspace, runner, store, config, _PROJECT, _MR_IID
            )
            assert result.sha == _SHA

        record = store.find(_PROJECT, _MR_IID, _SHA)
        assert record.status == ReviewStatus.DONE
    finally:
        store.close()


def test_execute_review_computes_comparison_against_previous_sha(tmp_path):
    """再レビュー(M2-2, #81): 前回とcommitが異なる2回目の実行で、修正済み/未対応/新規が
    正しく分類され、result.jsonのcomparisonフィールドに反映されることの回帰テスト。"""
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    store = SqliteStateStore(":memory:")

    carried_over_finding = {
        "severity": "major",
        "file": "src/app.py",
        "line": 10,
        "rationale": "Nullチェックが不足しているため例外が発生する可能性があります",
        "suggestion": "Nullチェックを追加してください",
    }
    resolved_only_finding = {
        "severity": "minor",
        "file": "src/util.py",
        "line": 5,
        "rationale": "変数名がわかりにくいです",
        "suggestion": "より説明的な名前にしてください",
    }
    new_finding = {
        "severity": "critical",
        "file": "src/other.py",
        "line": 1,
        "rationale": "APIキーがログに平文で出力されています",
        "suggestion": "ログ出力からAPIキーを除去してください",
    }

    try:
        runner1 = _FakeClaudeCodeRunner(
            _run_result(
                tmp_path, findings=[carried_over_finding, resolved_only_finding]
            )
        )
        execute_review(
            adapter, workspace, runner1, store, config, _PROJECT, _MR_IID, sha="sha1"
        )

        runner2 = _FakeClaudeCodeRunner(
            _run_result(tmp_path, findings=[carried_over_finding, new_finding])
        )
        result2 = execute_review(
            adapter, workspace, runner2, store, config, _PROJECT, _MR_IID, sha="sha2"
        )

        data = json.loads(result2.review_paths.result_json.read_text(encoding="utf-8"))
        comparison = data["comparison"]
        assert comparison is not None
        assert [f["file"] for f in comparison["unresolved"]] == ["src/app.py"]
        assert [f["file"] for f in comparison["new"]] == ["src/other.py"]
        assert [f["file"] for f in comparison["resolved"]] == ["src/util.py"]
    finally:
        store.close()


def test_execute_review_first_review_has_no_comparison(tmp_path):
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(_run_result(tmp_path))
    store = SqliteStateStore(":memory:")

    try:
        result = execute_review(
            adapter, workspace, runner, store, config, _PROJECT, _MR_IID
        )

        data = json.loads(result.review_paths.result_json.read_text(encoding="utf-8"))
        assert data["comparison"] is None
    finally:
        store.close()


def test_find_previous_review_result_returns_none_without_prior_reviews(tmp_path):
    root = tmp_path / "reviews"

    result = _find_previous_review_result(
        str(root), _PROJECT, _MR_IID, exclude_sha="sha2"
    )

    assert result is None


def test_find_previous_review_result_excludes_current_sha(tmp_path):
    root = tmp_path / "reviews"
    log_source = tmp_path / "log.json"
    log_source.write_text("{}", encoding="utf-8")
    save_review(
        root,
        _PROJECT,
        _MR_IID,
        "sha1",
        ReviewResult(summary="s", findings=()),
        input_prompt="p",
        run_log_path=log_source,
    )

    # 索引に唯一存在するレビューが、今まさに保存しようとしているcommit自身と同じ場合は
    # 「前回」として扱わない(単発実行の同一commit再実行と混同しないため)
    result = _find_previous_review_result(
        str(root), _PROJECT, _MR_IID, exclude_sha="sha1"
    )

    assert result is None


def test_find_previous_review_result_returns_latest_excluding_current_sha(tmp_path):
    root = tmp_path / "reviews"
    log_source = tmp_path / "log.json"
    log_source.write_text("{}", encoding="utf-8")
    older = ReviewResult(summary="old", findings=())
    newer = ReviewResult(summary="new", findings=())

    save_review(
        root,
        _PROJECT,
        _MR_IID,
        "sha1",
        older,
        input_prompt="p",
        run_log_path=log_source,
        reviewed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    save_review(
        root,
        _PROJECT,
        _MR_IID,
        "sha2",
        newer,
        input_prompt="p",
        run_log_path=log_source,
        reviewed_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    result = _find_previous_review_result(
        str(root), _PROJECT, _MR_IID, exclude_sha="sha3"
    )

    assert result == newer


def test_find_previous_review_result_returns_none_when_result_file_missing(tmp_path):
    """索引にはあるが結果ファイルが後から消えている(壊れている)場合、比較機能だけを
    諦め、例外を送出せずNoneを返すことの回帰テスト(single_run.pyのモジュールdocstring参照)。
    """
    root = tmp_path / "reviews"
    log_source = tmp_path / "log.json"
    log_source.write_text("{}", encoding="utf-8")
    paths = save_review(
        root,
        _PROJECT,
        _MR_IID,
        "sha1",
        ReviewResult(summary="s", findings=()),
        input_prompt="p",
        run_log_path=log_source,
    )
    paths.result_json.unlink()

    result = _find_previous_review_result(
        str(root), _PROJECT, _MR_IID, exclude_sha="sha2"
    )

    assert result is None


def test_clone_url_for_builds_https_git_url():
    build = _clone_url_for("https://gitlab.example.com")

    assert (
        build("group/subgroup/project")
        == "https://gitlab.example.com/group/subgroup/project.git"
    )


def test_credential_helper_references_token_env_var_without_leaking_value():
    helper = _credential_helper()

    assert GITLAB_TOKEN_ENV_KEY in helper
    assert helper.startswith("!")
    assert "secret-token" not in helper


class _FlakyStore:
    """SqliteStateStoreをラップし、update_statusの呼び出しを指定回数だけ失敗させるフェイク。"""

    def __init__(
        self, inner: SqliteStateStore, *, fail_update_status_times: int = 0
    ) -> None:
        self._inner = inner
        self._fail_update_status_times = fail_update_status_times
        self.update_status_calls: list[ReviewStatus] = []

    def find(self, *args, **kwargs):
        return self._inner.find(*args, **kwargs)

    def create(self, *args, **kwargs):
        return self._inner.create(*args, **kwargs)

    def update_status(self, project, mr_iid, sha, status, **kwargs):
        self.update_status_calls.append(status)
        if self._fail_update_status_times > 0:
            self._fail_update_status_times -= 1
            raise StateStoreError("boom")
        return self._inner.update_status(project, mr_iid, sha, status, **kwargs)

    def close(self) -> None:
        self._inner.close()


def test_execute_review_preserves_original_exception_when_failed_status_update_also_fails(
    tmp_path,
):
    # FAILEDへの更新自体が失敗しても、元の例外(ここではClaudeCodeTimeoutError)が
    # StateStoreErrorにすり替わらず、そのまま伝播することの回帰テスト
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(
        fail=ClaudeCodeTimeoutError(
            "timed out", timeout_seconds=1, log_path=tmp_path / "log.json", stderr=""
        )
    )
    inner_store = SqliteStateStore(":memory:")
    store = _FlakyStore(inner_store, fail_update_status_times=1)

    try:
        with pytest.raises(ClaudeCodeTimeoutError):
            execute_review(adapter, workspace, runner, store, config, _PROJECT, _MR_IID)

        assert store.update_status_calls == [ReviewStatus.FAILED]
    finally:
        inner_store.close()


def test_execute_review_marks_failed_when_done_update_itself_fails(tmp_path):
    # 全段階が成功してもDONEへの更新自体が失敗した場合、RUNNINGのまま放置せず
    # FAILEDへの更新を試みることの回帰テスト
    config = _config(tmp_path)
    adapter = _FakeGitLabReader(_merge_request())
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    workspace = _FakeWorkspaceManager(worktree_path)
    runner = _FakeClaudeCodeRunner(_run_result(tmp_path))
    inner_store = SqliteStateStore(":memory:")
    store = _FlakyStore(inner_store, fail_update_status_times=1)

    try:
        with pytest.raises(StateStoreError):
            execute_review(adapter, workspace, runner, store, config, _PROJECT, _MR_IID)

        assert store.update_status_calls == [ReviewStatus.DONE, ReviewStatus.FAILED]
        record = inner_store.find(_PROJECT, _MR_IID, _SHA)
        assert record.status == ReviewStatus.FAILED
    finally:
        inner_store.close()


def test_build_workspace_manager_clears_existing_credential_helper_before_setting_own(
    tmp_path,
):
    # gitはcredential.helperを複数個「追加」していく仕組みのため、既存の設定
    # (実行環境の~/.gitconfig等)を空値でクリアしてから独自のものを設定する必要がある
    config = _config(tmp_path)

    manager = build_workspace_manager(config)

    config_args = manager._config_args()
    helper_values = [
        config_args[i + 1].removeprefix("credential.helper=")
        for i in range(len(config_args) - 1)
        if config_args[i] == "-c"
        and config_args[i + 1].startswith("credential.helper=")
    ]
    assert helper_values[0] == ""
    assert helper_values[1] != ""
