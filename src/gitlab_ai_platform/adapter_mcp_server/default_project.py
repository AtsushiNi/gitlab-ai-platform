"""サーバー起動時のカレントディレクトリのgit remoteから、デフォルトのGitLab project pathを解決する。

方針(M2-12フォローアップ [#69](https://github.com/AtsushiNi/gitlab-ai-platform/issues/69)):

- 対話型Claude Code(VSCode拡張・CLI)は、開いているプロジェクトのディレクトリをcwdとして
  MCPサーバー(`python -m gitlab_ai_platform.adapter_mcp_server`)を起動する。このcwdの
  `git remote get-url origin`からGitLabのproject path(`group/project`)を解決できれば、
  ツール呼び出しのたびに`project`引数を書かなくても「今開いているプロジェクト」に対して
  動作するようにできる。
- 解決に失敗した場合(gitリポジトリでない、`origin`が存在しない、コマンド失敗等)は
  例外を送出せず`None`を返す。デフォルトが無いこと自体はエラーではなく、`project`引数の
  明示指定を要求する通常のフローとして扱う(`tools.py`の`_resolve_project`参照)。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# `https://gitlab.example.com/group/project.git`・`ssh://git@host:22/group/project.git`等、
# スキーム付きURL形式。
_URL_STYLE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+/(?P<path>.+)$")
# `git@gitlab.example.com:group/project.git`のようなscp風のSSH形式。
_SCP_STYLE_RE = re.compile(r"^[^@/]+@[^:/]+:(?P<path>.+)$")


def resolve_default_project(cwd: Path | str | None = None) -> str | None:
    """`cwd`(省略時はプロセスのカレントディレクトリ)のgit remote "origin" から
    GitLabのproject path(`group/project`)を解決する。解決できなければ`None`を返す。
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _parse_project_path(result.stdout.strip())


def _parse_project_path(remote_url: str) -> str | None:
    """SSH形式・URL形式いずれのremote URLからも`group/project`(サブグループ含む)を取り出す。"""
    if not remote_url:
        return None

    match = _URL_STYLE_RE.match(remote_url) or _SCP_STYLE_RE.match(remote_url)
    if match is None:
        return None

    path = match.group("path").removesuffix(".git").strip("/")
    return path or None


__all__ = ["resolve_default_project"]
