"""常駐(watch)モードのパイプライン。

方針(M1-11 [#39](https://github.com/AtsushiNi/gitlab-ai-platform/issues/39)、
`docs/architecture.md`「CLI」、`docs/adr/0009-cli-watch-design.md`):

- `MrPoller.run(interval_seconds, stop_event, on_detected)`で定期走査させ、`poll_once`が
  検出した`DetectedReview`ごとに単発実行パイプライン(`cli/single_run.py`の`execute_review`、
  M1-10)を呼び出して実際にレビューを実行する。パイプライン本体の結線・エラー処理
  (State Storeの`RUNNING`/`FAILED`/`DONE`遷移等)は`execute_review`にすべて委ね、この
  モジュールは「いつ・どのMRに対して呼ぶか」だけを担う(オーケストレーションはしない、
  `docs/architecture.md`のCLIの境界)。
- `run_watch_loop`(パイプライン本体)は`execute_review`と同じくGitLab Adapter/Workspace
  Manager/Claude Code Runner/State StoreをすべてProtocol型の引数として受け取り、`run_watch`
  (合成ルート)がそれらの具象実装(REST/git/subprocess/SQLite)を`config`から組み立てて
  委譲する(`ADR-0008`が確立した「パイプライン本体と合成ルートを分離する」パターンをそのまま
  踏襲。テストは手書きフェイクを注入して行い、実サービスには繋がない)。
- 1件のレビュー失敗(`GitLabAdapterError`/`WorkspaceError`/`RunnerError`/`ReviewError`/
  `StateStoreError`)はログに記録してプロセス全体は止めず、次のMR・次のサイクルの処理を
  続ける。State Store側は既に`execute_review`が`FAILED`へ更新済みのため、再度自動リトライは
  しない(MR Pollerが既存レコードを「処理済み」として無視する、という既存の挙動のまま)。
  一方、上記5種類に属さない想定外の例外は握りつぶさずそのまま伝播させ、プロセスを
  終了させる(このCLIはWindows上で人間が近くにいる運用が前提であり、想定外のバグを
  ログに埋もれさせるより目に見える形で落とす方を優先する、`docs/adr/0009`参照)。
- graceful shutdown: `stop_event`をセットする(通常はSIGINT/SIGTERM経由、ハンドラ登録自体は
  `cli/main.py`の責務)と、実行中のサイクルの完了後に停止する。
- 多重起動防止: `ProcessLock`(`cli/lock.py`)で`state_db_path`に対応するロックファイルを
  排他ロックし、同一設定に対する多重起動を防ぐ。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from ..config import Config
from ..gitlab_adapter import GitLabRestAdapter
from ..gitlab_adapter.errors import GitLabAdapterError
from ..gitlab_adapter.protocol import GitLabReader
from ..logging_ import execution_id_scope, get_logger
from ..poller import DetectedReview, MrPoller
from ..review.errors import ReviewError
from ..runner import SubprocessClaudeCodeRunner
from ..runner.errors import RunnerError
from ..runner.protocol import ClaudeCodeRunner
from ..store import SqliteStateStore
from ..store.errors import StateStoreError
from ..store.protocol import StateStore
from ..workspace.errors import WorkspaceError
from ..workspace.protocol import WorkspaceManager
from .lock import ProcessLock
from .single_run import build_workspace_manager, execute_review

_logger = get_logger(__name__)

_PIPELINE_ERROR_TYPES: tuple[type[Exception], ...] = (
    GitLabAdapterError,
    WorkspaceError,
    RunnerError,
    ReviewError,
    StateStoreError,
)


def run_watch(config: Config, *, stop_event: threading.Event | None = None) -> None:
    """`config`が指す全プロジェクトを定期走査し、検出したMRを順次レビューし続ける(合成ルート)。

    `stop_event`がセットされるまでブロックする(省略時は呼び出し側が別途プロセスを
    止める手段を用意する必要がある)。`config`からGitLab Adapter・Workspace Manager・
    Claude Code Runner・State Storeの具象実装(REST/git/subprocess/SQLite)を組み立て、
    `run_watch_loop`に委譲する。同一`state_db_path`に対する多重起動は`ProcessLock.acquire`が
    `AlreadyRunningError`を送出することで防ぐ。
    """
    lock_path = _lock_path_for(config.state_db_path)
    with ProcessLock(lock_path):
        adapter = GitLabRestAdapter(config.gitlab_url, config.gitlab_token)
        workspace = build_workspace_manager(config)
        runner = SubprocessClaudeCodeRunner(config.runner_log_dir)
        store = SqliteStateStore(config.state_db_path)
        try:
            run_watch_loop(adapter, workspace, runner, store, config, stop_event=stop_event)
        finally:
            store.close()


def run_watch_loop(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
    *,
    stop_event: threading.Event | None = None,
) -> None:
    """`MrPoller`と`execute_review`を結線するパイプライン本体(Protocol型のみに依存)。

    `adapter`/`workspace`/`runner`/`store`は`execute_review`(`cli/single_run.py`)と同じ
    Protocol型の引数として受け取り、具象実装には依存しない。テスト時は手書きフェイクを
    注入できる。
    """
    poller = MrPoller(adapter, store, config.projects, review_label=config.review_label)
    on_detected = build_on_detected(adapter, workspace, runner, store, config)
    poller.run(
        interval_seconds=config.poll_interval_seconds,
        stop_event=stop_event,
        on_detected=on_detected,
    )


def build_on_detected(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
) -> Callable[[DetectedReview], None]:
    """`DetectedReview`ごとに`execute_review`を呼ぶコールバックを組み立てる。

    既知のパイプライン例外(`_PIPELINE_ERROR_TYPES`)はログに記録して握りつぶし、
    呼び出し元(`MrPoller.run`のループ)を止めない。それ以外の想定外の例外は
    そのまま伝播させる(モジュールdocstring参照)。
    """

    def _on_detected(review: DetectedReview) -> None:
        # 1MR分のレビュー実行ごとに新しいexecution_idを振る(同一watchプロセス内で
        # 複数MRを順次処理しても、ログを実行単位で追跡できるようにするため)
        with execution_id_scope():
            try:
                execute_review(
                    adapter, workspace, runner, store, config, review.project, review.mr_iid
                )
            except _PIPELINE_ERROR_TYPES as exc:
                _logger.error(
                    "watch.review_failed",
                    extra={
                        "project": review.project,
                        "mr_iid": review.mr_iid,
                        "commit_sha": review.commit_sha,
                        "error": str(exc),
                    },
                )

    return _on_detected


def _lock_path_for(state_db_path: str) -> Path:
    # ロック専用の設定項目は増やさず、対象のstate.dbと同じ場所に`<db名>.lock`として置く
    # (同じstate.dbを指す設定=同一の稼働対象、という前提で自然に一意になる)
    return Path(state_db_path).with_suffix(".lock")


__all__ = ["run_watch", "run_watch_loop", "build_on_detected"]
