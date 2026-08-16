from __future__ import annotations

import os
import signal
import threading
from pathlib import Path

import pytest

from gitlab_ai_platform.cli import exit_codes
from gitlab_ai_platform.cli.decompose import ClaudeCommandNotFoundError
from gitlab_ai_platform.cli.lock import AlreadyRunningError
from gitlab_ai_platform.cli.main import main
from gitlab_ai_platform.cli.single_run import SingleRunResult
from gitlab_ai_platform.config import GITLAB_TOKEN_ENV_KEY
from gitlab_ai_platform.gitlab_adapter import GitLabApiError
from gitlab_ai_platform.review.errors import ReviewOutputParseError
from gitlab_ai_platform.review.types import Finding, ReviewPaths, ReviewResult, Severity
from gitlab_ai_platform.runner import RunResult
from gitlab_ai_platform.runner.errors import ClaudeCodeTimeoutError
from gitlab_ai_platform.store.errors import StateStoreError
from gitlab_ai_platform.workspace.errors import GitCommandError

_CONFIG_TOML = """
[gitlab]
url = "https://gitlab.example.com"
projects = ["group/project"]
"""


def _write_config(tmp_path: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "config.toml"
    config_path.write_text(_CONFIG_TOML, encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text(f"{GITLAB_TOKEN_ENV_KEY}=secret-token\n", encoding="utf-8")
    return config_path, env_path


def _argv(tmp_path: Path, *extra: str) -> list[str]:
    config_path, env_path = _write_config(tmp_path)
    return [
        "--config",
        str(config_path),
        "--env",
        str(env_path),
        "review",
        "group/project",
        "1",
        *extra,
    ]


def _single_run_result(tmp_path: Path) -> SingleRunResult:
    result_dir = tmp_path / "reviews" / "group/project" / "1" / "abc123"
    result_dir.mkdir(parents=True)
    result_json = result_dir / "result.json"
    result_json.write_text("{}", encoding="utf-8")
    result_md = result_dir / "result.md"
    result_md.write_text("# result", encoding="utf-8")
    log_path = result_dir / "run_log.json"
    log_path.write_text("{}", encoding="utf-8")

    return SingleRunResult(
        project="group/project",
        mr_iid=1,
        sha="abc123",
        worktree_path=tmp_path / "worktree",
        review_result=ReviewResult(
            summary="重大な問題あり",
            findings=(
                Finding(
                    severity=Severity.CRITICAL,
                    file="a.py",
                    line=1,
                    rationale="r",
                    suggestion="s",
                ),
            ),
        ),
        review_paths=ReviewPaths(
            dir=result_dir,
            result_json=result_json,
            result_md=result_md,
            input_path=result_dir / "input.md",
            log_path=log_path,
        ),
        run_result=RunResult(
            is_error=False,
            result_text="",
            session_id="s",
            terminal_reason="success",
            permission_denials=(),
            num_turns=1,
            total_cost_usd=0.0,
            timed_out=False,
            duration_seconds=1.0,
            log_path=log_path,
            raw={},
        ),
    )


def test_review_command_returns_ok_and_prints_summary(tmp_path, monkeypatch, capsys):
    result = _single_run_result(tmp_path)
    monkeypatch.setattr(
        "gitlab_ai_platform.cli.main.run_single_review", lambda *a, **k: result
    )

    exit_code = main(_argv(tmp_path))

    assert exit_code == exit_codes.EXIT_OK
    out = capsys.readouterr().out
    assert "group/project" in out
    assert "critical=1" in out
    assert str(result.review_paths.result_md) in out


def test_review_command_passes_cli_options_through(tmp_path, monkeypatch):
    captured = {}

    def _fake_run_single_review(config, project, mr_iid, **kwargs):
        captured["project"] = project
        captured["mr_iid"] = mr_iid
        captured.update(kwargs)
        return _single_run_result(tmp_path)

    monkeypatch.setattr(
        "gitlab_ai_platform.cli.main.run_single_review", _fake_run_single_review
    )

    exit_code = main(
        _argv(
            tmp_path,
            "--timeout",
            "42",
            "--allowed-tools",
            "Read",
            "Grep",
            "--disallowed-tools",
            "Bash",
            "--permission-mode",
            "plan",
        )
    )

    assert exit_code == exit_codes.EXIT_OK
    assert captured["project"] == "group/project"
    assert captured["mr_iid"] == 1
    assert captured["timeout_seconds"] == 42
    assert captured["allowed_tools"] == ("Read", "Grep")
    assert captured["disallowed_tools"] == ("Bash",)
    assert captured["permission_mode"] == "plan"


def test_review_command_returns_config_error_exit_code(tmp_path, capsys):
    # projectsが空 → ConfigError
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[gitlab]\nurl = "https://gitlab.example.com"\nprojects = []\n',
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(f"{GITLAB_TOKEN_ENV_KEY}=secret-token\n", encoding="utf-8")

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--env",
            str(env_path),
            "review",
            "group/project",
            "1",
        ]
    )

    assert exit_code == exit_codes.EXIT_CONFIG_ERROR
    assert "設定エラー" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("exception", "expected_exit_code"),
    [
        (GitLabApiError("boom"), exit_codes.EXIT_GITLAB_ADAPTER_ERROR),
        (
            GitCommandError("boom", command=["git"], returncode=1, stderr=""),
            exit_codes.EXIT_WORKSPACE_ERROR,
        ),
        (
            ClaudeCodeTimeoutError(
                "boom", timeout_seconds=1, log_path=Path("/tmp/x"), stderr=""
            ),
            exit_codes.EXIT_RUNNER_ERROR,
        ),
        (ReviewOutputParseError("boom", raw_text=""), exit_codes.EXIT_REVIEW_ERROR),
        (StateStoreError("boom"), exit_codes.EXIT_STATE_STORE_ERROR),
    ],
)
def test_review_command_maps_pipeline_errors_to_exit_codes(
    tmp_path, monkeypatch, capsys, exception, expected_exit_code
):
    def _raise(*args, **kwargs):
        raise exception

    monkeypatch.setattr("gitlab_ai_platform.cli.main.run_single_review", _raise)

    exit_code = main(_argv(tmp_path))

    assert exit_code == expected_exit_code
    assert capsys.readouterr().err.strip() != ""


def test_review_command_handles_keyboard_interrupt(tmp_path, monkeypatch, capsys):
    def _raise(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("gitlab_ai_platform.cli.main.run_single_review", _raise)

    exit_code = main(_argv(tmp_path))

    assert exit_code == exit_codes.EXIT_INTERRUPTED


def test_main_requires_a_subcommand(tmp_path):
    config_path, env_path = _write_config(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        main(["--config", str(config_path), "--env", str(env_path)])

    assert excinfo.value.code == 2


def test_main_handles_keyboard_interrupt_during_config_loading(
    tmp_path, monkeypatch, capsys
):
    # 以前はrun_single_review呼び出し中のみKeyboardInterruptをEXIT_INTERRUPTEDへ変換しており、
    # load_config中の中断は未加工のtracebackになっていた回帰テスト
    def _raise(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("gitlab_ai_platform.cli.main.load_config", _raise)

    exit_code = main(_argv(tmp_path))

    assert exit_code == exit_codes.EXIT_INTERRUPTED
    assert "中断されました" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "-1"])
def test_review_command_rejects_non_positive_timeout(tmp_path, value):
    with pytest.raises(SystemExit) as excinfo:
        main(_argv(tmp_path, "--timeout", value))

    assert excinfo.value.code == 2


def _watch_argv(tmp_path: Path) -> list[str]:
    config_path, env_path = _write_config(tmp_path)
    return ["--config", str(config_path), "--env", str(env_path), "watch"]


def test_watch_command_returns_ok_when_run_watch_returns_normally(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("gitlab_ai_platform.cli.main.run_watch", lambda *a, **k: None)

    exit_code = main(_watch_argv(tmp_path))

    assert exit_code == exit_codes.EXIT_OK


def test_watch_command_passes_a_stop_event_to_run_watch(tmp_path, monkeypatch):
    captured = {}

    def _fake_run_watch(config, *, stop_event=None):
        captured["stop_event"] = stop_event

    monkeypatch.setattr("gitlab_ai_platform.cli.main.run_watch", _fake_run_watch)

    main(_watch_argv(tmp_path))

    assert isinstance(captured["stop_event"], threading.Event)
    assert not captured["stop_event"].is_set()


def test_watch_command_returns_already_running_exit_code(tmp_path, monkeypatch, capsys):
    def _raise(*args, **kwargs):
        raise AlreadyRunningError("別プロセスが実行中です")

    monkeypatch.setattr("gitlab_ai_platform.cli.main.run_watch", _raise)

    exit_code = main(_watch_argv(tmp_path))

    assert exit_code == exit_codes.EXIT_ALREADY_RUNNING
    assert "多重起動エラー" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("exception", "expected_exit_code"),
    [
        (GitLabApiError("boom"), exit_codes.EXIT_GITLAB_ADAPTER_ERROR),
        (
            GitCommandError("boom", command=["git"], returncode=1, stderr=""),
            exit_codes.EXIT_WORKSPACE_ERROR,
        ),
        (
            ClaudeCodeTimeoutError(
                "boom", timeout_seconds=1, log_path=Path("/tmp/x"), stderr=""
            ),
            exit_codes.EXIT_RUNNER_ERROR,
        ),
        (ReviewOutputParseError("boom", raw_text=""), exit_codes.EXIT_REVIEW_ERROR),
        (StateStoreError("boom"), exit_codes.EXIT_STATE_STORE_ERROR),
    ],
)
def test_watch_command_maps_pipeline_errors_to_exit_codes(
    tmp_path, monkeypatch, capsys, exception, expected_exit_code
):
    # run_watch_loop内(1MR分の失敗)はwatch.build_on_detectedが既に握りつぶすため、
    # ここに届くのは具象実装の組み立て(構成)段階の失敗のみ。reviewコマンドと同じ
    # 変換になっていることを確認する
    def _raise(*args, **kwargs):
        raise exception

    monkeypatch.setattr("gitlab_ai_platform.cli.main.run_watch", _raise)

    exit_code = main(_watch_argv(tmp_path))

    assert exit_code == expected_exit_code
    assert capsys.readouterr().err.strip() != ""


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_watch_command_sets_stop_event_on_signal_and_restores_handler(
    tmp_path, monkeypatch, sig
):
    # run_watchを、自プロセスにシグナルを送ってからstop_eventの状態を確認するフェイクに差し替え、
    # SIGINT/SIGTERM受信時にstop_eventがセットされることを検証する
    original_handler = signal.getsignal(sig)
    observed = {}

    def _fake_run_watch(config, *, stop_event=None):
        os.kill(os.getpid(), sig)
        observed["was_set_after_signal"] = stop_event.is_set()

    monkeypatch.setattr("gitlab_ai_platform.cli.main.run_watch", _fake_run_watch)

    exit_code = main(_watch_argv(tmp_path))

    assert exit_code == exit_codes.EXIT_OK
    assert observed["was_set_after_signal"] is True
    # ハンドラがmain終了後に元へ戻されていること(他のテスト・プロセス全体への影響防止)
    assert signal.getsignal(sig) == original_handler


def _decompose_argv(tmp_path: Path, *extra: str) -> list[str]:
    config_path, env_path = _write_config(tmp_path)
    return [
        "--config",
        str(config_path),
        "--env",
        str(env_path),
        "decompose",
        "group/project",
        *extra,
    ]


def test_decompose_command_returns_run_decompose_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr("gitlab_ai_platform.cli.main.run_decompose", lambda *a, **k: 0)

    exit_code = main(_decompose_argv(tmp_path))

    assert exit_code == exit_codes.EXIT_OK


def test_decompose_command_propagates_claude_session_exit_code(tmp_path, monkeypatch):
    # 対話セッション自体はheadlessな成否判定を持たないため、claudeプロセスの終了コードを
    # そのままCLIの終了コードとして返す
    monkeypatch.setattr("gitlab_ai_platform.cli.main.run_decompose", lambda *a, **k: 7)

    exit_code = main(_decompose_argv(tmp_path))

    assert exit_code == 7


def test_decompose_command_passes_project_and_options_through(tmp_path, monkeypatch):
    captured = {}

    def _fake_run_decompose(project, **kwargs):
        captured["project"] = project
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(
        "gitlab_ai_platform.cli.main.run_decompose", _fake_run_decompose
    )

    exit_code = main(_decompose_argv(tmp_path, "--permission-mode", "plan"))

    assert exit_code == exit_codes.EXIT_OK
    assert captured["project"] == "group/project"
    assert captured["permission_mode"] == "plan"
    assert captured["config_path"].name == "config.toml"
    assert captured["env_path"].name == ".env"


def test_decompose_command_returns_claude_not_found_exit_code(
    tmp_path, monkeypatch, capsys
):
    def _raise(*args, **kwargs):
        raise ClaudeCommandNotFoundError("claudeコマンドが見つかりません")

    monkeypatch.setattr("gitlab_ai_platform.cli.main.run_decompose", _raise)

    exit_code = main(_decompose_argv(tmp_path))

    assert exit_code == exit_codes.EXIT_CLAUDE_NOT_FOUND
    assert "Claude Code起動エラー" in capsys.readouterr().err


def test_decompose_command_returns_config_error_exit_code(tmp_path, capsys):
    # projectsが空 → ConfigError(decomposeもreview/watchと同じconfig検証を通る)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[gitlab]\nurl = "https://gitlab.example.com"\nprojects = []\n',
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(f"{GITLAB_TOKEN_ENV_KEY}=secret-token\n", encoding="utf-8")

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--env",
            str(env_path),
            "decompose",
            "group/project",
        ]
    )

    assert exit_code == exit_codes.EXIT_CONFIG_ERROR
    assert "設定エラー" in capsys.readouterr().err
