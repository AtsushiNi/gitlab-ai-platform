"""CLIの終了コード定義。

方針(M1-10 [#38](https://github.com/AtsushiNi/gitlab-ai-platform/issues/38)、
`docs/adr/0008-cli-single-run-design.md`、
M1-11 [#39](https://github.com/AtsushiNi/gitlab-ai-platform/issues/39)):

- `argparse`が引数エラー時に自動的に使う`2`と衝突しないよう、10番台をパイプラインの
  各段階(GitLab Adapter/Workspace/Runner/Review/State Store)専用に割り当てる。
- どの段階で失敗したかを終了コードだけで判別できるようにし、デバッグ・自動化スクリプトの
  両方から失敗箇所を特定しやすくする(「デバッグとプロンプト改善の主要導線」という
  M1-10の目的に合わせた設計)。
- `EXIT_ALREADY_RUNNING`(16)は`watch`サブコマンド(M1-11)専用。多重起動防止の
  `ProcessLock`がロック取得に失敗した場合に返す。
- `EXIT_CLAUDE_NOT_FOUND`(17)は`decompose`サブコマンド(M2-11 [#48](https://github.com/AtsushiNi/gitlab-ai-platform/issues/48))
  専用。対話型`claude`プロセスの起動自体に失敗した場合に返す(`decompose.ClaudeCommandNotFoundError`)。
  `decompose`はその他の場合、対話セッション終了時の`claude`プロセス自身の終了コードを
  そのままCLIの終了コードとして返す(構造化された結果がなく、パイプライン各段階の
  終了コードという概念自体が存在しないため)。
- `EXIT_JOB_ERROR`(18)は`worker`サブコマンド(M3-3 [#93](https://github.com/AtsushiNi/gitlab-ai-platform/issues/93)、
  `docs/adr/0020-runner-process-separation.md`)専用。個々のJobの処理失敗は`RunnerDispatcher`が
  `JobRepository.fail`で処理し続行するため、ここに届くのは`SqliteJobRepository`の構築失敗や
  `claim`/`heartbeat`/`complete`/`fail`自体がJob Repository起因のエラー(`JobError`)を送出した
  場合(DB接続不良等、Dispatcher自身が継続できない異常)のみ。
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_UNEXPECTED_ERROR = 1
# 2はargparseの引数エラーで使われるため空けておく
EXIT_CONFIG_ERROR = 10
EXIT_GITLAB_ADAPTER_ERROR = 11
EXIT_WORKSPACE_ERROR = 12
EXIT_RUNNER_ERROR = 13
EXIT_REVIEW_ERROR = 14
EXIT_STATE_STORE_ERROR = 15
EXIT_ALREADY_RUNNING = 16
EXIT_CLAUDE_NOT_FOUND = 17
EXIT_JOB_ERROR = 18
EXIT_INTERRUPTED = 130

__all__ = [
    "EXIT_ALREADY_RUNNING",
    "EXIT_CLAUDE_NOT_FOUND",
    "EXIT_CONFIG_ERROR",
    "EXIT_GITLAB_ADAPTER_ERROR",
    "EXIT_INTERRUPTED",
    "EXIT_JOB_ERROR",
    "EXIT_OK",
    "EXIT_REVIEW_ERROR",
    "EXIT_RUNNER_ERROR",
    "EXIT_STATE_STORE_ERROR",
    "EXIT_UNEXPECTED_ERROR",
    "EXIT_WORKSPACE_ERROR",
]
