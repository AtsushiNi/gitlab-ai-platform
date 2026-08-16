"""対話型Claude CodeからGitLab Adapterの許可された操作を呼び出せるようにするMCPサーバー。

`docs/architecture.md`「コンポーネントの責務と境界」、
`docs/adr/0010-gitlab-mcp-tool-bridge.md`、`docs/specs/adapter-mcp-server.md`を参照。
"""

from __future__ import annotations

from .server import ALLOWED_TOOL_NAMES, DEFAULT_SERVER_NAME, create_server
from .tools import TOOL_DESCRIPTIONS, TOOL_FACTORIES

__all__ = [
    "ALLOWED_TOOL_NAMES",
    "DEFAULT_SERVER_NAME",
    "TOOL_DESCRIPTIONS",
    "TOOL_FACTORIES",
    "create_server",
]
