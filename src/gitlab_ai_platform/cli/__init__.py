"""単発レビュー実行(デバッグ・プロンプト改善用)の入口となるCLI(`docs/architecture.md`)。

CLIエントリポイント(`main`関数)は`gitlab_ai_platform.cli.main`から直接importする
(`pyproject.toml`の`[project.scripts]`・`cli/__main__.py`も同様)。ここで`main`を
再エクスポートしないのは、サブモジュール名`main`と関数名`main`が衝突し、
`from .main import main`が`gitlab_ai_platform.cli.main`属性をサブモジュールから関数へ
上書きしてしまう(モジュールとしてimportできなくなる)ため。
"""

from __future__ import annotations

from . import exit_codes
from .single_run import SingleRunResult, execute_review, run_single_review

__all__ = ["SingleRunResult", "execute_review", "exit_codes", "run_single_review"]
