"""`GitLabAdapter`をラップするMCPサーバーの組み立て。

方針(M2-12 [#62](https://github.com/AtsushiNi/gitlab-ai-platform/issues/62)、
`docs/adr/0010-gitlab-mcp-tool-bridge.md`。#67で「GitLab Adapter MCP Server」に改称):

- `tools.TOOL_FACTORIES`(1メソッド=1ファクトリ関数の対応表)を走査し、`GitLabAdapter`の
  インスタンスに束縛したツール関数をすべて登録するだけ。ツールの権限そのものは`tools.py`側
  (=`GitLabAdapter`Protocolの許可リスト)で決まっており、このモジュールは配線するだけで
  新しい操作を追加しない。
- Claude Code Runner(M1-7)の`build_prompt`(静的なプロンプト文字列へMR情報を埋め込む経路)とは
  別物。こちらは対話型Claude Code(Windows VS Code拡張・CLI)が実行中に能動的に呼び出す
  MCPツールの経路であり、`--mcp-config`でこのサーバーのstdio起動コマンドを渡すことを想定する
  (`docs/specs/adapter-mcp-server.md`)。
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ..gitlab_adapter import GitLabAdapter
from .tools import TOOL_DESCRIPTIONS, TOOL_FACTORIES

DEFAULT_SERVER_NAME = "gitlab-adapter"

# 現時点でこのサーバーが公開するツール名の集合(`GitLabReader`7 + `GitLabWriter`7)。
# `TOOL_FACTORIES`のキー集合と一致することをテストで担保する。
ALLOWED_TOOL_NAMES = frozenset(TOOL_FACTORIES)


def create_server(
    adapter: GitLabAdapter,
    *,
    name: str = DEFAULT_SERVER_NAME,
    default_project: str | None = None,
) -> MCPServer:
    """`adapter`の許可された操作をツールとして公開するMCPサーバーを組み立てる。

    `adapter`はこの関数の呼び出し側が用意する(実運用では`GitLabRestAdapter`。認証情報は
    `adapter`のコンストラクタで既に内部化されている想定で、この関数自体は認証情報に触れない)。
    `default_project`は各ツールの`project`引数省略時のフォールバック先(通常は
    `default_project.resolve_default_project`でMCPサーバー起動時のcwdのgit remoteから
    解決した値。`tools.py`の`_resolve_project`参照)。
    """
    server = MCPServer(name)
    for method_name, factory in TOOL_FACTORIES.items():
        tool_fn = factory(adapter, default_project)
        server.add_tool(tool_fn, name=method_name, description=TOOL_DESCRIPTIONS[method_name])
    return server


__all__ = ["create_server", "DEFAULT_SERVER_NAME", "ALLOWED_TOOL_NAMES"]
