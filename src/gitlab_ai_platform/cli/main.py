"""CLIエントリポイント。

方針(M1-10 [#38](https://github.com/AtsushiNi/gitlab-ai-platform/issues/38)、
M1-11 [#39](https://github.com/AtsushiNi/gitlab-ai-platform/issues/39)、
`docs/architecture.md`「CLI」、`docs/adr/0008-cli-single-run-design.md`、
`docs/adr/0009-cli-watch-design.md`):

- `review`サブコマンドで単発レビュー実行(デバッグ・プロンプト改善用)、`watch`サブコマンドで
  常駐モード(M1-11)、`decompose`サブコマンドで要件→Issue分解の対話型セッション(M2-11
  [#48](https://github.com/AtsushiNi/gitlab-ai-platform/issues/48))を提供する。
- パイプライン(`single_run.run_single_review`)が送出する各段階の例外
  (`GitLabAdapterError` / `WorkspaceError` / `RunnerError` / `ReviewError` /
  `StateStoreError`)を捕まえ、`exit_codes`の対応する終了コードとエラーメッセージ
  (標準エラー出力)に変換する。オーケストレーション(Job間の遷移)はしない
  (`docs/architecture.md`のCLIの境界)。
- 結果(保存先パス・指摘件数のサマリ)は標準出力に表示し、人間がすぐ確認できるようにする。
- `watch`サブコマンドはSIGINT/SIGTERM受信時に`stop_event`をセットするハンドラを登録し、
  `watch.run_watch`のポーリングループへgraceful shutdownを伝える(ハンドラ登録自体は
  ここ、実行中サイクルの完了を待ってから止める判断は`poller.MrPoller.run`側)。
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

from ..config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_ENV_PATH,
    Config,
    ConfigError,
    load_config,
)
from ..gitlab_adapter.errors import GitLabAdapterError
from ..logging_ import execution_id_scope, get_logger, setup_logging
from ..review.errors import ReviewError
from ..runner.errors import RunnerError
from ..store.errors import StateStoreError
from ..workspace.errors import WorkspaceError
from . import exit_codes
from .decompose import ClaudeCommandNotFoundError, run_decompose
from .lock import AlreadyRunningError
from .single_run import SingleRunResult, run_single_review
from .watch import run_watch

_logger = get_logger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    # Ctrl+C(SIGINT)は、設定読み込み・ロガー初期化を含むmain全体のどの時点で
    # 発生してもEXIT_INTERRUPTED(130)に揃える。以前はレビュー実行中のみを
    # 対象にしており、load_config中の中断が未加工のtracebackになっていた
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)

        setup_logging(level=args.log_level, log_dir=args.log_dir)

        try:
            config = load_config(config_path=args.config, env_path=args.env)
        except ConfigError as exc:
            print(f"設定エラー: {exc}", file=sys.stderr)
            return exit_codes.EXIT_CONFIG_ERROR

        if args.command == "review":
            return _run_review_command(config, args)
        if args.command == "watch":
            return _run_watch_command(config)
        if args.command == "decompose":
            return _run_decompose_command(args)

        parser.error(f"不明なコマンドです: {args.command!r}")
        return (
            exit_codes.EXIT_UNEXPECTED_ERROR
        )  # pragma: no cover - parser.errorがexitする
    except KeyboardInterrupt:
        print("中断されました", file=sys.stderr)
        return exit_codes.EXIT_INTERRUPTED


def _run_review_command(config: Config, args: argparse.Namespace) -> int:
    with execution_id_scope():
        try:
            result = run_single_review(
                config,
                args.project,
                args.mr_iid,
                timeout_seconds=args.timeout,
                allowed_tools=tuple(args.allowed_tools),
                disallowed_tools=tuple(args.disallowed_tools),
                permission_mode=args.permission_mode,
            )
        except GitLabAdapterError as exc:
            _logger.error(
                "cli.review_failed",
                extra={"stage": "gitlab_adapter", "error": str(exc)},
            )
            print(f"GitLab Adapterエラー: {exc}", file=sys.stderr)
            return exit_codes.EXIT_GITLAB_ADAPTER_ERROR
        except WorkspaceError as exc:
            _logger.error(
                "cli.review_failed", extra={"stage": "workspace", "error": str(exc)}
            )
            print(f"Workspace Managerエラー: {exc}", file=sys.stderr)
            return exit_codes.EXIT_WORKSPACE_ERROR
        except RunnerError as exc:
            _logger.error(
                "cli.review_failed", extra={"stage": "runner", "error": str(exc)}
            )
            print(f"Claude Code Runnerエラー: {exc}", file=sys.stderr)
            log_path = getattr(exc, "log_path", None)
            if log_path is not None:
                print(f"  実行ログ: {log_path}", file=sys.stderr)
            return exit_codes.EXIT_RUNNER_ERROR
        except ReviewError as exc:
            _logger.error(
                "cli.review_failed", extra={"stage": "review", "error": str(exc)}
            )
            print(f"レビュー結果の解析エラー: {exc}", file=sys.stderr)
            return exit_codes.EXIT_REVIEW_ERROR
        except StateStoreError as exc:
            _logger.error(
                "cli.review_failed", extra={"stage": "state_store", "error": str(exc)}
            )
            print(f"State Storeエラー: {exc}", file=sys.stderr)
            return exit_codes.EXIT_STATE_STORE_ERROR

    _print_summary(result)
    return exit_codes.EXIT_OK


def _run_watch_command(config: Config) -> int:
    stop_event = threading.Event()
    restore_handlers = _install_shutdown_handler(stop_event)
    try:
        run_watch(config, stop_event=stop_event)
    except AlreadyRunningError as exc:
        _logger.error("cli.watch_failed", extra={"stage": "lock", "error": str(exc)})
        print(f"多重起動エラー: {exc}", file=sys.stderr)
        return exit_codes.EXIT_ALREADY_RUNNING
    except GitLabAdapterError as exc:
        # ループ内で発生した分は`watch.build_on_detected`が既に握りつぶしているため、
        # ここに届くのは具象実装の組み立て(構成)段階の失敗のみ(`_run_review_command`と
        # 同じ変換で、`review`/`watch`間の挙動を揃える)
        _logger.error(
            "cli.watch_failed", extra={"stage": "gitlab_adapter", "error": str(exc)}
        )
        print(f"GitLab Adapterエラー: {exc}", file=sys.stderr)
        return exit_codes.EXIT_GITLAB_ADAPTER_ERROR
    except WorkspaceError as exc:
        _logger.error(
            "cli.watch_failed", extra={"stage": "workspace", "error": str(exc)}
        )
        print(f"Workspace Managerエラー: {exc}", file=sys.stderr)
        return exit_codes.EXIT_WORKSPACE_ERROR
    except RunnerError as exc:
        _logger.error("cli.watch_failed", extra={"stage": "runner", "error": str(exc)})
        print(f"Claude Code Runnerエラー: {exc}", file=sys.stderr)
        return exit_codes.EXIT_RUNNER_ERROR
    except ReviewError as exc:
        _logger.error("cli.watch_failed", extra={"stage": "review", "error": str(exc)})
        print(f"レビュー結果の解析エラー: {exc}", file=sys.stderr)
        return exit_codes.EXIT_REVIEW_ERROR
    except StateStoreError as exc:
        _logger.error(
            "cli.watch_failed", extra={"stage": "state_store", "error": str(exc)}
        )
        print(f"State Storeエラー: {exc}", file=sys.stderr)
        return exit_codes.EXIT_STATE_STORE_ERROR
    finally:
        restore_handlers()

    return exit_codes.EXIT_OK


def _run_decompose_command(args: argparse.Namespace) -> int:
    # ConfigError(GitLab認証等の設定不備)はここに来る前に`main`が既に検証・変換済み。
    # decompose自身はConfigの値を読まず、検証済みの--config/--envパスをそのまま
    # GitLab Adapter MCP Serverの起動コマンドへ引き継ぐだけ(decompose.pyのdocstring参照)。
    with execution_id_scope():
        try:
            returncode = run_decompose(
                args.project,
                config_path=args.config,
                env_path=args.env,
                log_dir=args.log_dir,
                permission_mode=args.permission_mode,
            )
        except ClaudeCommandNotFoundError as exc:
            _logger.error("cli.decompose_failed", extra={"error": str(exc)})
            print(f"Claude Code起動エラー: {exc}", file=sys.stderr)
            return exit_codes.EXIT_CLAUDE_NOT_FOUND

    # 対話セッション自体は人間が直接操作するため、成否のサマリ表示は行わない
    # (構造化された結果が存在しない。`claude`プロセス自身の終了コードをそのまま返す)。
    return returncode


def _install_shutdown_handler(stop_event: threading.Event) -> Callable[[], None]:
    """SIGINT/SIGTERM受信時に`stop_event`をセットするハンドラを登録する。

    戻り値は元のハンドラへ戻すための関数(呼び出し側が`finally`で呼ぶ)。
    """

    def _handle_signal(signum: int, frame: object) -> None:
        _logger.info("cli.watch_shutdown_requested", extra={"signal": signum})
        stop_event.set()

    previous_handlers = {
        sig: signal.signal(sig, _handle_signal)
        for sig in (signal.SIGINT, signal.SIGTERM)
    }

    def _restore() -> None:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)

    return _restore


def _print_summary(result: SingleRunResult) -> None:
    counts = Counter(
        finding.severity.value for finding in result.review_result.findings
    )
    print(f"レビュー完了: {result.project} !{result.mr_iid} ({result.sha[:12]})")
    print(f"  概要: {result.review_result.summary}")
    print(
        "  指摘件数: "
        f"critical={counts.get('critical', 0)} "
        f"major={counts.get('major', 0)} "
        f"minor={counts.get('minor', 0)}"
    )
    print(f"  結果(Markdown): {result.review_paths.result_md}")
    print(f"  結果(JSON): {result.review_paths.result_json}")
    print(f"  実行ログ: {result.run_result.log_path}")
    print(f"  worktree: {result.worktree_path}")


def _positive_int(value: str) -> int:
    # config.tomlのrunner.timeout_seconds(_require_positive_int)と同じ制約を
    # コマンドライン引数側にも課す。0や負値がそのままRunnerへ渡ると即タイムアウトする
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"正の整数を指定してください: {value!r}")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitlab-ai-platform",
        description="社内GitLab向けAI開発基盤のCLI",
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="config.tomlのパス"
    )
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV_PATH, help=".envのパス")
    parser.add_argument(
        "--log-level", default="INFO", help="ログレベル(INFO/DEBUG等、既定: INFO)"
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="構造化ログ(JSON、日次ローテーション)の出力先。省略時はコンソール出力のみ",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    review_parser = subparsers.add_parser(
        "review",
        help="指定したproject/MRを1本レビューする(デバッグ・プロンプト改善用)",
    )
    review_parser.add_argument(
        "project", help="GitLabのプロジェクトパス(例: group/project)"
    )
    review_parser.add_argument("mr_iid", type=int, help="MRのIID")
    review_parser.add_argument(
        "--timeout",
        type=_positive_int,
        default=None,
        help="Claude Codeのタイムアウト秒数(省略時はconfig.tomlのrunner.timeout_seconds)",
    )
    review_parser.add_argument(
        "--allowed-tools",
        nargs="*",
        default=(),
        metavar="TOOL",
        help="Claude Codeに明示的に許可するツール名(--allowedToolsに対応)",
    )
    review_parser.add_argument(
        "--disallowed-tools",
        nargs="*",
        default=(),
        metavar="TOOL",
        help="Claude Codeで禁止するツール名(--disallowedToolsに対応)",
    )
    review_parser.add_argument(
        "--permission-mode",
        default=None,
        help="Claude Codeの--permission-modeに対応する値",
    )

    subparsers.add_parser(
        "watch",
        help=(
            "対象プロジェクトを定期走査し、レビュー待ちMRを検出次第レビューし続ける"
            "(常駐モード。Ctrl+C/SIGTERMで終了)"
        ),
    )

    decompose_parser = subparsers.add_parser(
        "decompose",
        help=(
            "新しい開発要件を人間との対話でGitLab Issueへ分解する"
            "(対話型。ターミナルをそのままClaude Codeとの対話に使う)"
        ),
    )
    decompose_parser.add_argument(
        "project", help="GitLabのプロジェクトパス(例: group/project)"
    )
    decompose_parser.add_argument(
        "--permission-mode",
        default=None,
        help="Claude Codeの--permission-modeに対応する値",
    )

    return parser


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main"]
