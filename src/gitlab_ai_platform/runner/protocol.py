"""Claude Code Runner のインターフェース定義。

方針(M1-7 [#35](https://github.com/AtsushiNi/gitlab-ai-platform/issues/35)、
`docs/architecture.md`「Claude Code Runner」、
`docs/adr/0005-claude-code-runner-design.md`):

- Workspace Manager(M1-6)・GitLab Adapter(M1-1)と同じく`typing.Protocol`で抽象化し、
  実装(subprocess実装。将来Linux/Docker上での差し替え)を呼び出し側(Poller/Review/CLI)から
  切り離す。
- Runnerは「渡されたコンテキストで実行する」だけ。レビュー観点の判断そのもの
  (何を重大とするか)は`instructions`(呼び出し側が組み立てるプロンプト文字列)の責務であり、
  Runner自身はその中身を解釈しない。
- M4-3([#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109)、ADR-0027)で
  `run_prompt`を追加した。`run`は`instructions`+`ReviewContext`をRunner内部で結合する
  MRレビュー専用の経路のままとし(既存の呼び出し元・契約は変更しない)、`run_prompt`は
  呼び出し側が組み立て済みの完成後プロンプト文字列をそのまま実行する汎用の経路として
  分離した(`issue-analysis`/`design`/`implement`等、MR以外のフェーズ向け)。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from .types import ReviewContext, RunResult


@runtime_checkable
class ClaudeCodeRunner(Protocol):
    """worktree上でClaude Codeをヘッドレス実行する。"""

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
        """`worktree_path`配下でClaude Codeを非対話実行し、結果を返す。

        `instructions`(レビュー観点等、呼び出し側が組み立てたプロンプト本文)と`context`
        (MRタイトル・説明・コメント・diff)を結合してプロンプトを組み立てる。
        `timeout_seconds`経過してもClaude Codeが自発的に終了しない場合は強制終了を試みる
        (`references/spike-s1-claude-code-headless.md` §4)。SIGTERM後の猶予期間内に
        自発的に終了した場合は`RunResult.timed_out=True`の通常の戻り値になり、
        猶予期間内に終了しない(ハングした)場合のみ`ClaudeCodeTimeoutError`を送出する。
        `allowed_tools`/`disallowed_tools`/`permission_mode`はClaude Code CLIの権限フラグに
        対応する(`--dangerously-skip-permissions`相当のフラグは意図的に提供しない)。
        """
        ...

    def run_prompt(
        self,
        worktree_path: Path,
        prompt: str,
        *,
        log_key: str,
        timeout_seconds: int,
        allowed_tools: Sequence[str] = (),
        disallowed_tools: Sequence[str] = (),
        permission_mode: str | None = None,
    ) -> RunResult:
        """組み立て済みの`prompt`をそのままClaude Codeへ渡して非対話実行し、結果を返す(ADR-0027)。

        `run`と異なり、Runnerはプロンプトの組み立て(instructions+コンテキストの結合)を
        一切行わない(呼び出し側が完了済みであることが前提)。`log_key`は実行ログの
        保存先ディレクトリ(`log_dir`からの相対パス)を決めるための呼び出し側指定の識別子で、
        Runnerはこの文字列の中身を解釈しない(例: `"<projectスラッグ>/issue-<iid>"`)。
        タイムアウト・強制終了・`allowed_tools`等の意味は`run`と同じ。
        """
        ...


__all__ = ["ClaudeCodeRunner"]
