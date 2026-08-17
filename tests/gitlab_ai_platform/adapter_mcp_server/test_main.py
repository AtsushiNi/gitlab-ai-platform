"""エントリポイント(`python -m gitlab_ai_platform.adapter_mcp_server`)の挙動を検証する。

正常系(`server.run(transport="stdio")`)は標準入力を読み続けるため、テストでは実際には呼ばない
(`CLAUDE.md`のテスト方針: 実サービス・実プロトコル通信へは繋がない)。`create_server`/
`GitLabRestAdapter`をモンキーパッチし、どのトークンでAdapterを構築しているかだけを検証する。
"""

from __future__ import annotations

from typing import Any

from gitlab_ai_platform.adapter_mcp_server.main import EXIT_CONFIG_ERROR, EXIT_OK, main
from gitlab_ai_platform.config import GITLAB_MCP_TOKEN_ENV_KEY, GITLAB_TOKEN_ENV_KEY


def test_main_returns_config_error_exit_code_without_starting_server(
    tmp_path, capsys
) -> None:
    # projectsが空 → ConfigError(GitLab AdapterへもMCPサーバーへも到達しない)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[gitlab]\nurl = "https://gitlab.example.com"\nprojects = []\n',
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(f"{GITLAB_TOKEN_ENV_KEY}=secret-token\n", encoding="utf-8")

    exit_code = main(["--config", str(config_path), "--env", str(env_path)])

    assert exit_code == EXIT_CONFIG_ERROR
    err = capsys.readouterr().err
    assert "設定エラー" in err
    assert "secret-token" not in err


class _FakeServer:
    def run(self, *, transport: str) -> None:
        # 実プロトコル通信は行わず、呼ばれたことだけを記録する
        self.ran_with_transport = transport


def _write_config(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[gitlab]\nurl = "https://gitlab.example.com"\nprojects = ["group/project-a"]\n',
        encoding="utf-8",
    )
    return config_path


def test_main_uses_mcp_token_when_set(tmp_path, monkeypatch) -> None:
    # 用途別トークンを分けて設定した場合、MCPサーバーはGITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP
    # 側を使ってAdapterを構築する(M3-8, docs/adr/0019-gitlab-token-scoping.md)
    config_path = _write_config(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"{GITLAB_TOKEN_ENV_KEY}=review-token\n{GITLAB_MCP_TOKEN_ENV_KEY}=mcp-token\n",
        encoding="utf-8",
    )

    captured: dict[str, Any] = {}

    def _fake_adapter(url: str, token: str) -> str:
        captured["url"] = url
        captured["token"] = token
        return "fake-adapter"

    def _fake_create_server(adapter: object, **kwargs: Any) -> _FakeServer:
        captured["adapter"] = adapter
        return _FakeServer()

    monkeypatch.setattr(
        "gitlab_ai_platform.adapter_mcp_server.main.GitLabRestAdapter", _fake_adapter
    )
    monkeypatch.setattr(
        "gitlab_ai_platform.adapter_mcp_server.main.create_server", _fake_create_server
    )

    exit_code = main(["--config", str(config_path), "--env", str(env_path)])

    assert exit_code == EXIT_OK
    assert captured["token"] == "mcp-token"
    assert captured["adapter"] == "fake-adapter"


def test_main_falls_back_to_gitlab_token_when_mcp_token_unset(
    tmp_path, monkeypatch
) -> None:
    # MCP用トークンを分けない運用(GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP未設定)では、
    # 自動実行系と同じトークンにフォールバックする(後方互換)
    config_path = _write_config(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text(f"{GITLAB_TOKEN_ENV_KEY}=only-token\n", encoding="utf-8")
    monkeypatch.delenv(GITLAB_MCP_TOKEN_ENV_KEY, raising=False)

    captured: dict[str, Any] = {}

    def _fake_adapter(url: str, token: str) -> str:
        captured["token"] = token
        return "fake-adapter"

    def _fake_create_server(adapter: object, **kwargs: Any) -> _FakeServer:
        return _FakeServer()

    monkeypatch.setattr(
        "gitlab_ai_platform.adapter_mcp_server.main.GitLabRestAdapter", _fake_adapter
    )
    monkeypatch.setattr(
        "gitlab_ai_platform.adapter_mcp_server.main.create_server", _fake_create_server
    )

    exit_code = main(["--config", str(config_path), "--env", str(env_path)])

    assert exit_code == EXIT_OK
    assert captured["token"] == "only-token"
