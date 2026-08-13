"""`python -m gitlab_ai_platform.mcp_bridge`で起動するMCPサーバーのエントリポイント。

方針(M2-12 [#62](https://github.com/AtsushiNi/gitlab-ai-platform/issues/62)、
`docs/adr/0010-gitlab-mcp-tool-bridge.md`):

- 対話型Claude Code(Windows VS Code拡張・CLI)が`--mcp-config`でこのコマンドをstdio MCP
  サーバーとして起動する想定。標準入出力はMCPプロトコル専用に使われるため、ログは
  `config`(M0-2)の`runner_log_dir`と同じ考え方でファイルにのみ出力し、コンソール(標準出力)
  には出さない。
- GitLab PAT等の認証情報は、既存の`config.load_config`(`.env`または実環境変数経由)を
  再利用して取得する。このモジュール自身がコマンドライン引数や環境変数を直接パースして
  認証情報を扱うことはしない(`config`層への一本化)。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from ..config import DEFAULT_CONFIG_PATH, DEFAULT_ENV_PATH, ConfigError, load_config
from ..gitlab_adapter import GitLabRestAdapter
from ..logging_ import get_logger, setup_logging
from .server import DEFAULT_SERVER_NAME, create_server

_logger = get_logger(__name__)

EXIT_OK = 0
EXIT_CONFIG_ERROR = 10


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # 標準出力はMCPのstdioプロトコル専用のため、コンソールへのログ出力は行わない
    setup_logging(log_dir=args.log_dir, console=False)

    try:
        config = load_config(config_path=args.config, env_path=args.env)
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    adapter = GitLabRestAdapter(config.gitlab_url, config.gitlab_token)
    server = create_server(adapter, name=DEFAULT_SERVER_NAME)

    _logger.info("mcp_bridge.start", extra={"server_name": DEFAULT_SERVER_NAME})
    server.run(transport="stdio")
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitlab_ai_platform.mcp_bridge",
        description="GitLab AdapterをラップしたMCPサーバー(stdio)を起動する。",
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="config.tomlのパス")
    parser.add_argument("--env", default=DEFAULT_ENV_PATH, help=".envのパス")
    parser.add_argument("--log-dir", default=None, help="ログ出力先ディレクトリ")
    return parser


__all__ = ["main"]
