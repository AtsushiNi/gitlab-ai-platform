"""Claude Code Runner が送出する例外の基底クラス。

具体的な判定ロジック(タイムアウトの猶予時間、JSON出力の検証)は実装
(`subprocess_runner.py`)の責務。ここではインターフェースとして呼び出し側が握れる型だけを
定義する(`workspace/errors.py`と同じ方針)。
"""

from __future__ import annotations

from pathlib import Path


class RunnerError(Exception):
    """Claude Code Runner経由の実行が失敗したことを表す基底例外。"""


class ClaudeCodeNotFoundError(RunnerError):
    """`claude`コマンドが見つからないことを表す(未インストール・PATH未設定等)。"""

    def __init__(self, message: str, *, log_path: Path) -> None:
        super().__init__(message)
        self.log_path = log_path


class ClaudeCodeTimeoutError(RunnerError):
    """タイムアウトし、かつ有効な最終結果(JSON)を得られないまま終了したことを表す。

    SIGTERM送出後の猶予期間内にClaude Codeが自発的に終了した場合は、`terminal_reason`が
    `aborted_*`のJSONが得られるため例外にはしない
    (`references/spike-s1-claude-code-headless.md` §4)。この例外はSIGKILLでの強制終了が
    必要になった(=ハングした)場合、または強制終了後も出力が得られなかった場合のみ送出する。
    """

    def __init__(
        self, message: str, *, timeout_seconds: int, log_path: Path, stderr: str
    ) -> None:
        super().__init__(message)
        self.timeout_seconds = timeout_seconds
        self.log_path = log_path
        self.stderr = stderr


class ClaudeCodeOutputError(RunnerError):
    """Claude Codeの標準出力が期待するJSON形式でなかったことを表す。"""

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None,
        log_path: Path,
        stdout: str,
        stderr: str,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.log_path = log_path
        self.stdout = stdout
        self.stderr = stderr


__all__ = [
    "ClaudeCodeNotFoundError",
    "ClaudeCodeOutputError",
    "ClaudeCodeTimeoutError",
    "RunnerError",
]
