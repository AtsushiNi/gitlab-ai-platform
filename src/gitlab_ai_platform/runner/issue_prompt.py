"""GitLab IssueをRunnerプロンプトへ正規化する。

方針(M4-2 [#108](https://github.com/AtsushiNi/gitlab-ai-platform/issues/108)):

- MR向けの`build_prompt`(`subprocess_runner.py`, M1-7)と対になる関数として、同じ構成方針
  (instructions + 対象データの見出し付きテキスト)でIssue向けプロンプトを組み立てる。
  無人実行トラック(M4、`issue-analysis`/`design`/`implement`フェーズ)向けの入力整形部分であり、
  Jobハンドラ自体(M4-3以降、別Issue)はこのモジュールのスコープ外。
- `GitLabAdapter.get_issue`(M2-10)で取得できるのはIssue本体(タイトル・説明・ラベル等)の
  みで、MRの`list_merge_request_discussions`に相当するIssueコメント取得メソッドが
  Adapter(`gitlab_adapter/protocol.py`)に存在しないため、コメントはこの関数のスコープに
  含めない。将来Adapter側にメソッドが追加されたら、`ReviewContext`の`discussions`と
  同様の形で拡張する。
- `claude -p`にargvとして渡す際のOS上限(Linuxの`MAX_ARG_STRLEN`)を超えないよう、
  `subprocess_runner.build_prompt`と同じ方針・同じ閾値で切り詰める。Issue本体はMRのdiffほど
  大きくなりにくいが、将来コメントを含めるようになった場合に備え同じ安全策を最初から適用する。
"""

from __future__ import annotations

from ..logging_ import get_logger
from .types import IssueContext

_logger = get_logger(__name__)

# subprocess_runner.pyの_MAX_PROMPT_BYTESと同じ理由・同じ値(Popen引数の上限に対する安全マージン)
_MAX_PROMPT_BYTES = 100_000


def build_issue_prompt(instructions: str, context: IssueContext) -> str:
    """`instructions`と`context`を結合し、Issue向けの完成後プロンプト文字列を返す。

    `subprocess_runner.build_prompt`(MR向け)と同じ構成方針(instructions →
    見出し付きの事実の列挙)。無人実行パイプラインの各フェーズ(issue-analysis等、
    M4-3以降)がClaude Codeへ渡すプロンプトを組み立てる際に使う想定。
    """
    issue = context.issue
    lines = [instructions.rstrip(), "", "## Issue", f"Title: {issue.title}"]
    if issue.description:
        lines += ["", "Description:", issue.description]
    if issue.labels:
        lines += ["", f"Labels: {', '.join(issue.labels)}"]

    return _truncate_prompt("\n".join(lines))


def _truncate_prompt(prompt: str, *, max_bytes: int = _MAX_PROMPT_BYTES) -> str:
    encoded = prompt.encode("utf-8")
    if len(encoded) <= max_bytes:
        return prompt

    _logger.warning(
        "runner.issue_prompt_truncated",
        extra={"original_bytes": len(encoded), "max_bytes": max_bytes},
    )
    # UTF-8のマルチバイト文字の途中で切らないよう、decode時にerrors="ignore"で
    # 不完全な末尾バイト列を捨てる(subprocess_runner.pyの_truncate_promptと同じ方針)
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated + "\n\n...(説明が大きいため以降は切り詰められました)"


__all__ = ["build_issue_prompt"]
