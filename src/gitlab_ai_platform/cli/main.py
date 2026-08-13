"""CLIエントリポイント。

方針(M1-10 [#38](https://github.com/AtsushiNi/gitlab-ai-platform/issues/38)、
`docs/architecture.md`「CLI」、`docs/adr/0008-cli-single-run-design.md`):

- `review`サブコマンドで単発レビュー実行(デバッグ・プロンプト改善用)を提供する。
  常駐(watch)モード(M1-11)は別サブコマンドとして後日追加する想定で、あらかじめ
  `argparse`のサブコマンド構成にしてある。
- パイプライン(`single_run.run_single_review`)が送出する各段階の例外
  (`GitLabAdapterError` / `WorkspaceError` / `RunnerError` / `ReviewError` /
  `StateStoreError`)を捕まえ、`exit_codes`の対応する終了コードとエラーメッセージ
  (標準エラー出力)に変換する。オーケストレーション(Job間の遷移)はしない
  (`docs/architecture.md`のCLIの境界)。
- 結果(保存先パス・指摘件数のサマリ)は標準出力に表示し、人間がすぐ確認できるようにする。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from ..config import DEFAULT_CONFIG_PATH, DEFAULT_ENV_PATH, Config, ConfigError, load_config
from ..gitlab_adapter.errors import GitLabAdapterError
from ..logging_ import execution_id_scope, get_logger, setup_logging
from ..review.errors import ReviewError
from ..runner.errors import RunnerError
from ..store.errors import StateStoreError
from ..workspace.errors import WorkspaceError
from . import exit_codes
from .single_run import SingleRunResult, run_single_review

_logger = get_logger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
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

    parser.error(f"不明なコマンドです: {args.command!r}")
    return exit_codes.EXIT_UNEXPECTED_ERROR  # pragma: no cover - parser.errorがexitする


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
            _logger.error("cli.review_failed", extra={"stage": "gitlab_adapter", "error": str(exc)})
            print(f"GitLab Adapterエラー: {exc}", file=sys.stderr)
            return exit_codes.EXIT_GITLAB_ADAPTER_ERROR
        except WorkspaceError as exc:
            _logger.error("cli.review_failed", extra={"stage": "workspace", "error": str(exc)})
            print(f"Workspace Managerエラー: {exc}", file=sys.stderr)
            return exit_codes.EXIT_WORKSPACE_ERROR
        except RunnerError as exc:
            _logger.error("cli.review_failed", extra={"stage": "runner", "error": str(exc)})
            print(f"Claude Code Runnerエラー: {exc}", file=sys.stderr)
            log_path = getattr(exc, "log_path", None)
            if log_path is not None:
                print(f"  実行ログ: {log_path}", file=sys.stderr)
            return exit_codes.EXIT_RUNNER_ERROR
        except ReviewError as exc:
            _logger.error("cli.review_failed", extra={"stage": "review", "error": str(exc)})
            print(f"レビュー結果の解析エラー: {exc}", file=sys.stderr)
            return exit_codes.EXIT_REVIEW_ERROR
        except StateStoreError as exc:
            _logger.error("cli.review_failed", extra={"stage": "state_store", "error": str(exc)})
            print(f"State Storeエラー: {exc}", file=sys.stderr)
            return exit_codes.EXIT_STATE_STORE_ERROR
        except KeyboardInterrupt:
            print("中断されました", file=sys.stderr)
            return exit_codes.EXIT_INTERRUPTED

    _print_summary(result)
    return exit_codes.EXIT_OK


def _print_summary(result: SingleRunResult) -> None:
    counts = Counter(finding.severity.value for finding in result.review_result.findings)
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitlab-ai-platform",
        description="社内GitLab向けAI開発基盤のCLI",
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH, help="config.tomlのパス"
    )
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV_PATH, help=".envのパス")
    parser.add_argument("--log-level", default="INFO", help="ログレベル(INFO/DEBUG等、既定: INFO)")
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
    review_parser.add_argument("project", help="GitLabのプロジェクトパス(例: group/project)")
    review_parser.add_argument("mr_iid", type=int, help="MRのIID")
    review_parser.add_argument(
        "--timeout",
        type=int,
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

    return parser


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main"]
