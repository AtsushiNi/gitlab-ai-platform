"""要件→Issue分解ワークフロー(対話型、Windows・CLI)。

方針(M2-11 [#48](https://github.com/AtsushiNi/gitlab-ai-platform/issues/48)、
`docs/requirements.md` 3-C、`docs/adr/0012-decompose-interactive-session.md`):

- 大きな開発要件を人間とClaude Codeの対話で複数のGitLab Issueへ分解する作業を支援する。
  GitLab Adapter MCP Server(M2-12, `adapter_mcp_server`)を`--mcp-config`で登録した
  対話型Claude Codeセッションを起動し、エージェント自身が`create_issue`等のツールを
  能動的に呼び出せるようにする。
- `runner/subprocess_runner.py`のheadless実行(`-p`付きで起動し、標準出力のJSONを`RunResult`へ
  パースする)とは異なる経路であることに注意。こちらは`-p`を付けず、stdin/stdout/stderrを
  そのまま人間に継承させる**対話型**実行であり、構造化された実行結果(`RunResult`相当のもの)は
  存在しない。`claude`プロセスが終了した時点の終了コードをそのまま返すだけにとどめる。
- 要件の粒度・優先度・依存関係の切り方には人間の判断が本質的に必要なため
  (`docs/requirements.md` 3-C「Bとの違い」)、Claude Code自身が推測で断定して起票してしまわない
  よう、システムプロンプト(`--append-system-prompt`)で明示的に指示する。
- GitLab認証(PAT等)は、呼び出し元(`cli.main`)が`config.load_config`で検証済みの
  `--config`/`--env`パスをそのままGitLab Adapter MCP Serverの起動コマンドへ引き継ぐだけで、
  このモジュール自身はトークンの値を一切読み書きしない。
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..logging_ import get_logger

_logger = get_logger(__name__)

# `--mcp-config`のJSON内でGitLab Adapter MCP Serverに割り当てるサーバー名
MCP_SERVER_NAME = "gitlab-adapter"


class ClaudeCommandNotFoundError(Exception):
    """`claude`コマンドが見つからないことを表す(未インストール・PATH未設定等)。"""


def build_mcp_config(
    config_path: Path,
    env_path: Path,
    *,
    python_executable: str = sys.executable,
    log_dir: Path | None = None,
) -> dict[str, Any]:
    """GitLab Adapter MCP Serverを登録した`--mcp-config`用のJSON構造を組み立てる。

    `python -m gitlab_ai_platform.adapter_mcp_server`をstdio MCPサーバーとして起動する
    コマンドを`mcpServers`に1件だけ登録する。`python_executable`は既定で`sys.executable`
    (この`decompose`コマンド自身を実行しているPython)を使い、PATH上の`python`に依存しない。
    `config_path`/`env_path`はそのままサーバー起動時の`--config`/`--env`に引き継ぐだけで、
    ここにGitLab PAT等の値そのものは一切含まれない。
    """
    args: list[str] = [
        "-m",
        "gitlab_ai_platform.adapter_mcp_server",
        "--config",
        str(config_path),
        "--env",
        str(env_path),
    ]
    if log_dir is not None:
        args += ["--log-dir", str(log_dir)]

    return {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": python_executable,
                "args": args,
            }
        }
    }


def build_system_prompt(project: str) -> str:
    """対話セッション全体に効く指示(`--append-system-prompt`)を組み立てる。

    初期メッセージ(`build_initial_prompt`)は最初の1ターンだけの入力だが、こちらは
    セッション中ずっと有効なシステムプロンプトへ追記されるため、「対象プロジェクトの明示」
    「人間の判断を仰ぐこと」「起票時のproject明示」といった、対話が長引いても薄れてほしくない
    ルールをここに置く。
    """
    return (
        "あなたはこれから、人間と対話しながら新しい開発要件を複数のGitLab Issueへ"
        "分解する作業を支援します。\n"
        f"対象プロジェクトは `{project}` です。\n"
        "\n"
        "厳守事項:\n"
        "- 要件の大きさ・優先度・依存関係の切り方には人間の判断が本質的に必要です。"
        "粒度やマイルストーンを勝手に断定せず、不明点や複数の分け方が考えられる場合は"
        "必ず人間に質問し、合意を得てから先に進めてください。\n"
        "- GitLab Issueを実際に起票する前に、分解案(タイトル・概要・優先度・依存関係)を"
        "一覧で提示し、人間の承認を得てください。承認が得られるまで`create_issue`は"
        "呼び出さないでください。\n"
        "- Issueの起票・更新には`create_issue`/`update_issue`ツールを使い、`project`引数には"
        f"必ず `{project}` を明示してください(MCPサーバーのデフォルトプロジェクト自動解決には"
        "頼らない。誤って別プロジェクトへ起票することを防ぐためです)。\n"
        "- merge・ブランチ削除・管理操作等の破壊的操作を行うツールはそもそも提供されていません。"
    )


def build_initial_prompt(project: str) -> str:
    """対話セッション開始時に自動送信される最初のユーザーメッセージを組み立てる。"""
    return (
        f"プロジェクト `{project}` に対する新しい開発要件をGitLab Issueへ分解する作業を"
        "始めます。まず、分解したい開発要件の概要を教えてください。内容を伺った上で、"
        "粒度・優先度・依存関係について質問しながら、一緒に分解案を検討しましょう。"
    )


def build_claude_command(
    claude_command: str,
    *,
    mcp_config: dict[str, Any],
    system_prompt: str,
    initial_prompt: str,
    permission_mode: str | None = None,
) -> list[str]:
    """対話型`claude`セッションを起動するコマンド列を組み立てる。

    `subprocess_runner._build_command`(headless実行、`-p --output-format json`付き)とは対照的に、
    `-p`を付けない。`--strict-mcp-config`で、ユーザー環境に他のMCP設定(グローバル設定・
    起動時cwdの`.mcp.json`等)があってもここで組み立てたGitLab Adapter MCP Serverのみが
    有効になるようにし、「起票時は必ず`create_issue`ツールを使う」という前提を安定させる。
    """
    command = [
        claude_command,
        "--mcp-config",
        json.dumps(mcp_config),
        "--strict-mcp-config",
        "--append-system-prompt",
        system_prompt,
    ]
    if permission_mode is not None:
        command += ["--permission-mode", permission_mode]
    # 位置引数のprompt: `-p`を付けずに渡すと、対話セッションを開始しつつ最初のユーザー
    # メッセージとして自動送信される(ヘッドレス実行にはならない)
    command.append(initial_prompt)
    return command


def run_decompose(
    project: str,
    *,
    config_path: Path,
    env_path: Path,
    log_dir: Path | None = None,
    claude_command: str = "claude",
    python_executable: str = sys.executable,
    permission_mode: str | None = None,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
) -> int:
    """対話型のIssue分解セッションを起動する(合成ルート)。

    headlessなレビュー実行(`runner.SubprocessClaudeCodeRunner`)と異なり、stdin/stdout/stderrを
    継承した対話型プロセスとして`claude`を起動し、人間が直接ターミナルで対話する
    (`subprocess.PIPE`には繋がない)。構造化された`RunResult`は存在しないため、
    `claude`プロセスが終了した時点の終了コードをそのまま返す。

    `claude`コマンド自体が見つからない場合のみ`ClaudeCommandNotFoundError`を送出する。
    GitLab認証エラー(`ConfigError`)は呼び出し元(`cli.main`)が本関数を呼ぶ前に検証済みの
    前提とする。
    """
    mcp_config = build_mcp_config(
        config_path, env_path, python_executable=python_executable, log_dir=log_dir
    )
    command = build_claude_command(
        claude_command,
        mcp_config=mcp_config,
        system_prompt=build_system_prompt(project),
        initial_prompt=build_initial_prompt(project),
        permission_mode=permission_mode,
    )

    _logger.info(
        "decompose.session_starting",
        extra={"project": project, "claude_command": claude_command},
    )
    try:
        # 対話型セッションのため標準入出力・標準エラーは継承させる(headless実行のように
        # PIPEへ繋いでパースする必要がない)
        proc = popen(command)
    except FileNotFoundError as exc:
        raise ClaudeCommandNotFoundError(
            f"'{claude_command}' コマンドが見つかりません。"
            "Claude Code CLIがインストールされ、PATHが通っていることを確認してください。"
        ) from exc

    returncode = proc.wait()
    _logger.info(
        "decompose.session_ended",
        extra={"project": project, "returncode": returncode},
    )
    return returncode


__all__ = [
    "MCP_SERVER_NAME",
    "ClaudeCommandNotFoundError",
    "build_claude_command",
    "build_initial_prompt",
    "build_mcp_config",
    "build_system_prompt",
    "run_decompose",
]
