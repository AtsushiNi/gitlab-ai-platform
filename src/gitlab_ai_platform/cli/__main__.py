"""`python -m gitlab_ai_platform.cli` で起動できるようにするエントリポイント。"""

from __future__ import annotations

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
