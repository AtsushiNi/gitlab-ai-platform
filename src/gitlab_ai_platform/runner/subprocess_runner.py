"""`ClaudeCodeRunner` を満たすsubprocess実装。

方針(M1-7 [#35](https://github.com/AtsushiNi/gitlab-ai-platform/issues/35)、
`docs/adr/0005-claude-code-runner-design.md`、
`references/spike-s1-claude-code-headless.md`):

- `claude -p "<prompt>" --output-format json`をheadless実行し、標準出力のJSONを
  `RunResult`にマッピングする。`result`(自然文)の内容だけで成否を判定しない(spike §2.4)。
- タイムアウトはPythonの`subprocess.run(timeout=...)`ではなく`Popen`を直接使い、
  SIGTERM→(猶予期間)→SIGKILLの2段階で行う。`subprocess.run`はタイムアウト時に
  即座に`kill()`(SIGKILL)するが、spikeの実測ではSIGTERM後もClaude Codeは最終結果JSON
  (`terminal_reason: aborted_*`)を出力してから終了することが確認できており、
  その挙動を活かして通常の実行結果と同様に扱えるようにするため。
- `--dangerously-skip-permissions`は提供しない(サンドボックス外では使うべきでないと
  公式ヘルプにも明記されている。GitLab Adapterが禁止操作をメソッドとして持たせない方針
  (`docs/adr/0002-gitlab-adapter-interface.md`)と同様、危険な操作への近道をコード上
  用意しない)。
- 実行ログ(コマンド・標準出力・標準エラー・所要時間)は`log_dir`配下に
  `<projectスラッグ>/mr-<iid>/<sha先頭12桁>-<timestamp>.json`として保存する。認証情報は
  `env`経由でPopenに渡すのみでコマンド引数には含めないため、ログには含まれない。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..gitlab_adapter.types import MergeRequest
from ..logging_ import get_logger
from .errors import ClaudeCodeNotFoundError, ClaudeCodeOutputError, ClaudeCodeTimeoutError
from .types import ReviewContext, RunResult

_logger = get_logger(__name__)

_DEFAULT_TERMINATE_GRACE_SECONDS = 10.0


class SubprocessClaudeCodeRunner:
    """`claude` CLIをsubprocessとして起動し`ClaudeCodeRunner`を実装する。"""

    def __init__(
        self,
        log_dir: Path | str,
        *,
        claude_command: str = "claude",
        env: Mapping[str, str] | None = None,
        terminate_grace_seconds: float = _DEFAULT_TERMINATE_GRACE_SECONDS,
        popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ) -> None:
        """
        `env`はBedrock認証情報(`CLAUDE_CODE_USE_BEDROCK`/`AWS_*`)やモデルバージョン固定
        (`ANTHROPIC_DEFAULT_SONNET_MODEL`等)を注入するためのもの。`os.environ`と
        マージして渡すため、PATH等の既存環境変数は失われない。
        """
        self._log_dir = Path(log_dir)
        self._claude_command = claude_command
        self._env = dict(env or {})
        self._terminate_grace_seconds = terminate_grace_seconds
        self._popen = popen

    def run(
        self,
        worktree_path: Path,
        instructions: str,
        context: ReviewContext,
        *,
        timeout_seconds: int,
        allowed_tools: Sequence[str] = (),
        disallowed_tools: Sequence[str] = (),
        permission_mode: str | None = None,
    ) -> RunResult:
        prompt = _build_prompt(instructions, context)
        command = _build_command(
            self._claude_command,
            prompt,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            permission_mode=permission_mode,
        )
        env = {**os.environ, **self._env}

        started_at = datetime.now(UTC)
        start = time.monotonic()
        try:
            proc = self._popen(
                command,
                cwd=str(worktree_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
        except FileNotFoundError as exc:
            raise ClaudeCodeNotFoundError(
                f"'{self._claude_command}' コマンドが見つかりません。"
                "Claude Code CLIがインストールされ、PATHが通っていることを確認してください。"
            ) from exc

        stdout, stderr, timed_out, returncode = self._communicate_with_timeout(
            proc, timeout_seconds
        )
        duration = time.monotonic() - start

        log_path = self._write_log(
            context.merge_request,
            command=command,
            worktree_path=worktree_path,
            started_at=started_at,
            duration_seconds=duration,
            timed_out=timed_out,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

        data = _parse_result_json(
            stdout,
            timed_out=timed_out,
            returncode=returncode,
            log_path=log_path,
            stderr=stderr,
            timeout_seconds=timeout_seconds,
        )
        return _build_run_result(
            data, timed_out=timed_out, duration_seconds=duration, log_path=log_path
        )

    # -- 内部ヘルパー ----------------------------------------------------------

    def _communicate_with_timeout(
        self, proc: subprocess.Popen[str], timeout_seconds: int
    ) -> tuple[str, str, bool, int | None]:
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
            return stdout, stderr, False, proc.returncode
        except subprocess.TimeoutExpired:
            pass

        # spikeの実測(§4)通り、SIGTERM後もClaude Codeは最終結果JSONを出力してから
        # 終了することが多いため、まず正常終了を試みる
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=self._terminate_grace_seconds)
            return stdout, stderr, True, proc.returncode
        except subprocess.TimeoutExpired:
            pass

        # 猶予期間内に終了しない(ハング)場合のみ強制終了する
        proc.kill()
        stdout, stderr = proc.communicate()
        return stdout, stderr, True, proc.returncode

    def _write_log(
        self,
        merge_request: MergeRequest,
        *,
        command: list[str],
        worktree_path: Path,
        started_at: datetime,
        duration_seconds: float,
        timed_out: bool,
        returncode: int | None,
        stdout: str,
        stderr: str,
    ) -> Path:
        log_dir = self._log_dir / _slugify_project(merge_request.project) / f"mr-{merge_request.iid}"
        log_dir.mkdir(parents=True, exist_ok=True)
        sha_prefix = (merge_request.sha or "unknown")[:12]
        timestamp = started_at.strftime("%Y%m%dT%H%M%S%fZ")
        log_path = log_dir / f"{sha_prefix}-{timestamp}.json"

        log_path.write_text(
            json.dumps(
                {
                    "command": command,
                    "cwd": str(worktree_path),
                    "started_at": started_at.isoformat(),
                    "duration_seconds": duration_seconds,
                    "timed_out": timed_out,
                    "returncode": returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        _logger.info(
            "runner.execution_log_saved",
            extra={
                "project": merge_request.project,
                "mr_iid": merge_request.iid,
                "log_path": str(log_path),
                "timed_out": timed_out,
            },
        )
        return log_path


def _build_command(
    claude_command: str,
    prompt: str,
    *,
    allowed_tools: Sequence[str],
    disallowed_tools: Sequence[str],
    permission_mode: str | None,
) -> list[str]:
    command = [claude_command, "-p", prompt, "--output-format", "json"]
    if permission_mode is not None:
        command += ["--permission-mode", permission_mode]
    if allowed_tools:
        command += ["--allowedTools", " ".join(allowed_tools)]
    if disallowed_tools:
        command += ["--disallowedTools", " ".join(disallowed_tools)]
    return command


def _build_prompt(instructions: str, context: ReviewContext) -> str:
    mr = context.merge_request
    lines = [instructions.rstrip(), "", "## Merge Request", f"Title: {mr.title}"]
    if mr.description:
        lines += ["", "Description:", mr.description]

    non_system_notes = [
        note for discussion in context.discussions for note in discussion.notes if not note.system
    ]
    if non_system_notes:
        lines += ["", "## Comments"]
        lines += [f"- {note.author}: {note.body}" for note in non_system_notes]

    if context.diffs:
        lines += ["", "## Diff"]
        for diff in context.diffs:
            lines += [f"--- {diff.old_path} -> {diff.new_path} ---", diff.diff]

    return "\n".join(lines)


def _parse_result_json(
    stdout: str,
    *,
    timed_out: bool,
    returncode: int | None,
    log_path: Path,
    stderr: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    if not stdout.strip():
        if timed_out:
            raise ClaudeCodeTimeoutError(
                f"Claude Codeが{timeout_seconds}秒以内に終了せず、強制終了後も"
                "有効な結果を得られませんでした",
                timeout_seconds=timeout_seconds,
                log_path=log_path,
                stderr=stderr,
            )
        raise ClaudeCodeOutputError(
            "Claude Codeの標準出力が空でした",
            returncode=returncode,
            log_path=log_path,
            stdout=stdout,
            stderr=stderr,
        )

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        if timed_out:
            raise ClaudeCodeTimeoutError(
                f"Claude Codeが{timeout_seconds}秒以内に終了せず、強制終了後の出力も"
                "JSONとして解釈できませんでした",
                timeout_seconds=timeout_seconds,
                log_path=log_path,
                stderr=stderr,
            ) from exc
        raise ClaudeCodeOutputError(
            "Claude Codeの標準出力がJSONとして解釈できませんでした",
            returncode=returncode,
            log_path=log_path,
            stdout=stdout,
            stderr=stderr,
        ) from exc

    if not isinstance(data, dict):
        raise ClaudeCodeOutputError(
            "Claude Codeの標準出力が期待するJSONオブジェクト形式ではありませんでした",
            returncode=returncode,
            log_path=log_path,
            stdout=stdout,
            stderr=stderr,
        )

    return data


def _build_run_result(
    data: dict[str, Any], *, timed_out: bool, duration_seconds: float, log_path: Path
) -> RunResult:
    # `is_error`が欠けている(想定外のレスポンス形式)場合は、誤って成功と判定しないよう
    # デフォルトでエラー扱いにする(spike §2.4の「result文言だけを信用しない」方針の延長)
    return RunResult(
        is_error=bool(data.get("is_error", True)),
        result_text=str(data.get("result", "")),
        session_id=str(data.get("session_id", "")),
        terminal_reason=str(data.get("terminal_reason", "")),
        permission_denials=tuple(data.get("permission_denials", ()) or ()),
        num_turns=int(data.get("num_turns", 0)),
        total_cost_usd=float(data.get("total_cost_usd", 0.0)),
        timed_out=timed_out,
        duration_seconds=duration_seconds,
        log_path=log_path,
        raw=data,
    )


def _slugify_project(project: str) -> str:
    # workspace/git_workspace.pyの_slugify_projectと同じ方式(パーセントエンコーディング)。
    # ログ保存先のディレクトリ名を1階層に保ちつつ、project名の"/"を単射に変換する
    return quote(project, safe="")


__all__ = ["SubprocessClaudeCodeRunner"]
