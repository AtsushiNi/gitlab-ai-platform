"""`python -m gitlab_ai_platform.adapter_mcp_server` で起動できるようにするエントリポイント。"""

from __future__ import annotations

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
