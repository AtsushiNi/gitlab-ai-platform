from __future__ import annotations

import json
from pathlib import Path

import pytest

from gitlab_ai_platform.cli.decompose import (
    MCP_SERVER_NAME,
    ClaudeCommandNotFoundError,
    build_claude_command,
    build_initial_prompt,
    build_mcp_config,
    build_system_prompt,
    run_decompose,
)


def test_build_mcp_config_registers_adapter_mcp_server_with_config_and_env_paths():
    config = build_mcp_config(
        Path("/tmp/config.toml"),
        Path("/tmp/.env"),
        python_executable="/usr/bin/python3",
    )

    server = config["mcpServers"][MCP_SERVER_NAME]
    assert server["command"] == "/usr/bin/python3"
    assert server["args"] == [
        "-m",
        "gitlab_ai_platform.adapter_mcp_server",
        "--config",
        "/tmp/config.toml",
        "--env",
        "/tmp/.env",
    ]
    # トークンの値そのものは一切含まれない(パスのみ)
    assert "token" not in json.dumps(config).lower()


def test_build_mcp_config_appends_log_dir_when_given():
    config = build_mcp_config(
        Path("/tmp/config.toml"),
        Path("/tmp/.env"),
        python_executable="/usr/bin/python3",
        log_dir=Path("/tmp/logs"),
    )

    args = config["mcpServers"][MCP_SERVER_NAME]["args"]
    assert args[-2:] == ["--log-dir", "/tmp/logs"]


def test_build_mcp_config_omits_log_dir_when_not_given():
    config = build_mcp_config(
        Path("/tmp/config.toml"),
        Path("/tmp/.env"),
        python_executable="/usr/bin/python3",
    )

    args = config["mcpServers"][MCP_SERVER_NAME]["args"]
    assert "--log-dir" not in args


def test_build_system_prompt_mentions_project_and_requires_human_judgement():
    prompt = build_system_prompt("group/project")

    assert "group/project" in prompt
    assert "create_issue" in prompt
    assert "人間" in prompt


def test_build_initial_prompt_mentions_project():
    prompt = build_initial_prompt("group/project")

    assert "group/project" in prompt


def test_build_claude_command_does_not_use_headless_flag():
    command = build_claude_command(
        "claude",
        mcp_config={"mcpServers": {}},
        system_prompt="system",
        initial_prompt="initial",
    )

    assert "-p" not in command
    assert "--print" not in command
    assert "--output-format" not in command


def test_build_claude_command_includes_mcp_config_and_system_prompt():
    mcp_config = {"mcpServers": {"gitlab-adapter": {"command": "python"}}}
    command = build_claude_command(
        "claude",
        mcp_config=mcp_config,
        system_prompt="system prompt",
        initial_prompt="initial prompt",
    )

    assert command[0] == "claude"
    idx = command.index("--mcp-config")
    assert json.loads(command[idx + 1]) == mcp_config
    assert "--strict-mcp-config" in command
    idx_sp = command.index("--append-system-prompt")
    assert command[idx_sp + 1] == "system prompt"
    # 初期プロンプトは末尾の位置引数として渡す(-pは付けない=headlessにならない)
    assert command[-1] == "initial prompt"


def test_build_claude_command_includes_permission_mode_when_given():
    command = build_claude_command(
        "claude",
        mcp_config={},
        system_prompt="s",
        initial_prompt="i",
        permission_mode="plan",
    )

    idx = command.index("--permission-mode")
    assert command[idx + 1] == "plan"


def test_build_claude_command_omits_permission_mode_when_not_given():
    command = build_claude_command(
        "claude", mcp_config={}, system_prompt="s", initial_prompt="i"
    )

    assert "--permission-mode" not in command


class _FakeProcess:
    def __init__(self, returncode: int) -> None:
        self._returncode = returncode
        self.wait_called = False

    def wait(self) -> int:
        self.wait_called = True
        return self._returncode


def test_run_decompose_launches_claude_interactively_and_returns_its_exit_code(
    tmp_path,
):
    captured = {}

    def _fake_popen(command):
        captured["command"] = command
        return _FakeProcess(returncode=0)

    exit_code = run_decompose(
        "group/project",
        config_path=tmp_path / "config.toml",
        env_path=tmp_path / ".env",
        popen=_fake_popen,
    )

    assert exit_code == 0
    command = captured["command"]
    assert command[0] == "claude"
    # 対話型のためheadless実行フラグを一切付けない
    assert "-p" not in command
    assert command[-1] == build_initial_prompt("group/project")


def test_run_decompose_propagates_nonzero_exit_code(tmp_path):
    def _fake_popen(command):
        return _FakeProcess(returncode=17)

    exit_code = run_decompose(
        "group/project",
        config_path=tmp_path / "config.toml",
        env_path=tmp_path / ".env",
        popen=_fake_popen,
    )

    assert exit_code == 17


def test_run_decompose_passes_claude_command_and_permission_mode_through(tmp_path):
    captured = {}

    def _fake_popen(command):
        captured["command"] = command
        return _FakeProcess(returncode=0)

    run_decompose(
        "group/project",
        config_path=tmp_path / "config.toml",
        env_path=tmp_path / ".env",
        claude_command="/opt/claude/bin/claude",
        permission_mode="plan",
        popen=_fake_popen,
    )

    command = captured["command"]
    assert command[0] == "/opt/claude/bin/claude"
    idx = command.index("--permission-mode")
    assert command[idx + 1] == "plan"


def test_run_decompose_raises_claude_command_not_found_error_when_popen_fails(tmp_path):
    def _fake_popen(command):
        raise FileNotFoundError("claude: not found")

    with pytest.raises(ClaudeCommandNotFoundError):
        run_decompose(
            "group/project",
            config_path=tmp_path / "config.toml",
            env_path=tmp_path / ".env",
            popen=_fake_popen,
        )
