"""エントリポイント(`python -m gitlab_ai_platform.mcp_bridge`)の設定エラーハンドリングを検証する。

正常系(`server.run(transport="stdio")`)は標準入力を読み続けるため、テストでは呼ばない
(`CLAUDE.md`のテスト方針: 実サービス・実プロトコル通信へは繋がない)。ここでは`config`
読み込みに失敗した場合に、MCPサーバーを起動せずエラー終了することだけを検証する。
"""

from __future__ import annotations

from gitlab_ai_platform.config import GITLAB_TOKEN_ENV_KEY
from gitlab_ai_platform.mcp_bridge.main import EXIT_CONFIG_ERROR, main


def test_main_returns_config_error_exit_code_without_starting_server(tmp_path, capsys) -> None:
    # projectsが空 → ConfigError(GitLab AdapterへもMCPサーバーへも到達しない)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[gitlab]\nurl = "https://gitlab.example.com"\nprojects = []\n', encoding="utf-8"
    )
    env_path = tmp_path / ".env"
    env_path.write_text(f"{GITLAB_TOKEN_ENV_KEY}=secret-token\n", encoding="utf-8")

    exit_code = main(["--config", str(config_path), "--env", str(env_path)])

    assert exit_code == EXIT_CONFIG_ERROR
    err = capsys.readouterr().err
    assert "設定エラー" in err
    assert "secret-token" not in err
