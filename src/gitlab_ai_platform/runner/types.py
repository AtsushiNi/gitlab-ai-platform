"""Claude Code Runner が読み書きするデータの型。

`docs/architecture.md` の Claude Code Runner の責務(worktree上でClaude Codeを
ヘッドレス実行し、MRタイトル・説明・コメント・diffをコンテキストとして渡す)に対応する型を
定義する。MRの情報自体はGitLab Adapter(`gitlab_adapter/types.py`)の型をそのまま再利用し、
Runner側で独自の型に作り直さない。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..gitlab_adapter.types import Discussion, Issue, MergeRequest, MergeRequestDiff


@dataclass(frozen=True)
class ReviewContext:
    """Claude Codeへ渡すMRのコンテキスト(タイトル・説明・コメント・diff)。

    プロンプトとして何を重視するか(レビュー観点)はここに含めない。あくまで
    「渡す事実」だけを持つ(`instructions`側が観点を担う、`docs/architecture.md`の境界)。
    """

    merge_request: MergeRequest
    diffs: tuple[MergeRequestDiff, ...]
    discussions: tuple[Discussion, ...]


@dataclass(frozen=True)
class IssueContext:
    """Claude Codeへ渡すIssueのコンテキスト(タイトル・説明・ラベル)。

    `ReviewContext`と同じ設計方針(`docs/architecture.md`の境界)で、あくまで
    「渡す事実」だけを持つ。GitLab Adapter(`gitlab_adapter/protocol.py`)には現時点で
    MRの`list_merge_request_discussions`に相当するIssueコメント取得メソッドが存在しないため、
    `ReviewContext.discussions`に相当するフィールドは持たない
    ([#108](https://github.com/AtsushiNi/gitlab-ai-platform/issues/108)のスコープ。
    Adapter側にメソッドが追加された時点で拡張する)。
    """

    issue: Issue


@dataclass(frozen=True)
class RunResult:
    """Claude Codeヘッドレス実行1回分の結果。

    `references/spike-s1-claude-code-headless.md` §2.4の通り、`result_text`(自然文)の
    内容だけで成否判定してはならない。`is_error`と`permission_denials`を必ず確認すること。
    """

    is_error: bool
    result_text: str
    session_id: str
    terminal_reason: str
    permission_denials: tuple[Mapping[str, Any], ...]
    num_turns: int
    total_cost_usd: float
    timed_out: bool
    duration_seconds: float
    log_path: Path
    raw: Mapping[str, Any]


__all__ = ["IssueContext", "ReviewContext", "RunResult"]
