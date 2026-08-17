"""常駐(watch)モードのパイプライン。

方針(M1-11 [#39](https://github.com/AtsushiNi/gitlab-ai-platform/issues/39)、
M2-1 [#80](https://github.com/AtsushiNi/gitlab-ai-platform/issues/80)、
`docs/architecture.md`「CLI」、`docs/adr/0009-cli-watch-design.md`、
`docs/adr/0015-parallel-review-execution.md`):

- `MrPoller.run(interval_seconds, stop_event, on_detected)`で定期走査させ、`poll_once`が
  検出した`DetectedReview`ごとに単発実行パイプライン(`cli/single_run.py`の
  `execute_review_job`、M1-10・M3-1)を呼び出して実際にレビューを実行する。パイプライン本体の
  結線・エラー処理(State Storeの`RUNNING`/`FAILED`/`DONE`遷移等)は`execute_review_job`が
  委ねる`execute_review`にすべて委ね、このモジュールは「いつ・どのMRに対して呼ぶか」だけを
  担う(オーケストレーションはしない、`docs/architecture.md`のCLIの境界)。
- `run_watch_loop`(パイプライン本体)は`execute_review_job`と同じくGitLab Adapter/Workspace
  Manager/Claude Code Runner/State Store/Job RepositoryをすべてProtocol型の引数として受け取り、
  `run_watch`(合成ルート)がそれらの具象実装(REST/git/subprocess/SQLiteまたはPostgreSQL)を`config`から
  組み立てて委譲する(`ADR-0008`が確立した「パイプライン本体と合成ルートを分離する」パターンを
  そのまま踏襲。テストは手書きフェイクを注入して行い、実サービスには繋がない)。
- M2-1: 検出された各MRのレビューは`ReviewWorkerPool`(`cli/worker_pool.py`)経由で
  `config.max_parallel`個までのワーカースレッドが並行実行する。`build_on_detected`が組み立てる
  コールバック自体は従来通り1件を同期的に処理する関数のままで(単体テストは変更不要)、
  `run_watch_loop`がその呼び出しをプールへの`submit`に置き換えることで並列化する
  (`MrPoller.run`のループ自体・`build_on_detected`の中身は変更しない)。
- Job抽象(M3-1 [#91](https://github.com/AtsushiNi/gitlab-ai-platform/issues/91)、
  `docs/adr/0016-job-abstraction.md`): `build_on_detected`は`execute_review`を直接
  同期呼び出しするのではなく、`cli/single_run.py`の`execute_review_job`(review Jobとして
  ラップした`execute_review`)を呼ぶ。Job Repositoryの具象実装(SQLite)の組み立てと
  `close`は`run_watch`(合成ルート)の責務。
- 1件のレビュー失敗(`GitLabAdapterError`/`WorkspaceError`/`RunnerError`/`ReviewError`/
  `StateStoreError`)はログに記録してプロセス全体は止めず、次のMR・次のサイクルの処理を
  続ける。State Store側は既に`execute_review`が`FAILED`へ更新済みのため、再度自動リトライは
  しない(MR Pollerが既存レコードを「処理済み」として無視する、という既存の挙動のまま)。
  一方、上記5種類に属さない想定外の例外は握りつぶさずそのまま伝播させ、プロセスを
  終了させる(このCLIはWindows上で人間が近くにいる運用が前提であり、想定外のバグを
  ログに埋もれさせるより目に見える形で落とす方を優先する、`docs/adr/0009`参照)。ワーカー
  スレッド内で発生した想定外の例外も`ReviewWorkerPool`が捕まえて`stop_event`をセットし、
  `run_watch_loop`が`pool.shutdown_and_reraise()`で同じ経路に乗せて再送出する
  (`docs/adr/0015`参照)。
- graceful shutdown: `stop_event`をセットする(通常はSIGINT/SIGTERM経由、ハンドラ登録自体は
  `cli/main.py`の責務)と、実行中のサイクルの完了後に停止する。
- 多重起動防止: `ProcessLock`(`cli/lock.py`)で`state_db_path`に対応するロックファイルを
  排他ロックし、同一設定に対する多重起動を防ぐ。
- Webhook受信(M3-6 [#96](https://github.com/AtsushiNi/gitlab-ai-platform/issues/96)、
  `docs/adr/0018-webhook-receiver.md`): `config.webhook_enabled`が真の場合のみ、
  `webhook.WebhookServer`を背景スレッドで起動する(任意有効化。既定は無効で、
  Pollerのみ従来通り動く)。WebhookサーバーもPollerと同じ`on_detected`ラッパー
  (`pool.submit`)を共有するため、検出後の実行経路(並列数制御・エラー処理・ログ)は
  完全に共通。重複起票の防止は`poller.ticket_if_unprocessed`をWebhookサーバー内部から
  呼ぶことで(State Storeの一意制約に頼る形で)実現しており、このモジュールは
  Webhookサーバーの起動/停止のライフサイクル管理だけを担う。
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable
from pathlib import Path

from ..config import Config
from ..gitlab_adapter import GitLabRestAdapter
from ..gitlab_adapter.errors import GitLabAdapterError
from ..gitlab_adapter.protocol import GitLabReader
from ..job import SqliteJobRepository
from ..job.protocol import JobRepository
from ..logging_ import execution_id_scope, get_logger
from ..poller import DetectedReview, MrPoller
from ..review.errors import ReviewError
from ..runner import SubprocessClaudeCodeRunner
from ..runner.errors import RunnerError
from ..runner.protocol import ClaudeCodeRunner
from ..store import build_state_store
from ..store.errors import StateStoreError
from ..store.protocol import StateStore
from ..webhook import WebhookServer
from ..workspace.errors import WorkspaceError
from ..workspace.protocol import WorkspaceManager
from .lock import ProcessLock
from .single_run import build_workspace_manager, execute_review_job
from .worker_pool import ReviewWorkerPool

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
    Claude Code Runner・State Store・Job Repositoryの具象実装(REST/git/subprocess/SQLiteまたはPostgreSQL)を
    組み立て、`run_watch_loop`に委譲する。同一`state_db_path`に対する多重起動は
    `ProcessLock.acquire`が`AlreadyRunningError`を送出することで防ぐ。
    """
    lock_path = _lock_path_for(config.state_db_path)
    with ProcessLock(lock_path):
        adapter = GitLabRestAdapter(config.gitlab_url, config.gitlab_token)
        workspace = build_workspace_manager(config)
        runner = SubprocessClaudeCodeRunner(config.runner_log_dir)
        store = build_state_store(config)
        job_repo = SqliteJobRepository(config.job_db_path)
        try:
            run_watch_loop(
                adapter,
                workspace,
                runner,
                store,
                job_repo,
                config,
                stop_event=stop_event,
            )
        finally:
            job_repo.close()
            store.close()


def run_watch_loop(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    job_repo: JobRepository,
    config: Config,
    *,
    stop_event: threading.Event | None = None,
) -> None:
    """`MrPoller`と`execute_review_job`を結線するパイプライン本体(Protocol型のみに依存)。

    `adapter`/`workspace`/`runner`/`store`/`job_repo`は`execute_review_job`
    (`cli/single_run.py`)と同じProtocol型の引数として受け取り、具象実装には依存しない。
    テスト時は手書きフェイクを注入できる。

    検出された各MRの処理(`build_on_detected`が組み立てるコールバック1回分)は
    `ReviewWorkerPool`(`config.max_parallel`個までのワーカースレッド、M2-1)へ投入し、
    並行実行する(`cli/worker_pool.py`のモジュールdocstring参照)。`stop_event`を省略した
    場合、ここで作る`threading.Event`を`MrPoller.run`とワーカープールの両方に渡す。同じ
    オブジェクトを共有することで、ワーカースレッド内の想定外の例外がポーリングループの
    早期終了(`stop_event.set()`)にそのまま反映される。

    `config.webhook_enabled`が真の場合(M3-6、`docs/adr/0018-webhook-receiver.md`)、
    `WebhookServer`を背景スレッドで起動し、同じ`ReviewWorkerPool`への投入ラッパーを
    `on_detected`として渡す(Poller/Webhook間で検出後の実行経路を完全に共有する)。
    """
    effective_stop_event = stop_event if stop_event is not None else threading.Event()
    poller = MrPoller(adapter, store, config.projects, review_label=config.review_label)
    review_job = build_on_detected(adapter, workspace, runner, store, job_repo, config)
    pool = ReviewWorkerPool(config.max_parallel, effective_stop_event)

    def _submit_to_pool(review: DetectedReview) -> None:
        pool.submit(functools.partial(review_job, review))

    webhook_server = _build_webhook_server(store, config, on_detected=_submit_to_pool)
    if webhook_server is not None:
        webhook_server.start()
    try:
        poller.run(
            interval_seconds=config.poll_interval_seconds,
            stop_event=effective_stop_event,
            on_detected=_submit_to_pool,
        )
    finally:
        if webhook_server is not None:
            webhook_server.stop()
        # 実行中のジョブの完了を待ってから、ワーカースレッドで発生した想定外の例外
        # (あれば)をここで再送出する(`run_watch`→`cli.main`へそのまま伝播させる)
        pool.shutdown_and_reraise()


def _build_webhook_server(
    store: StateStore,
    config: Config,
    *,
    on_detected: Callable[[DetectedReview], None],
) -> WebhookServer | None:
    """`config.webhook_enabled`が真の場合のみ`WebhookServer`を組み立てる(任意有効化)。"""
    if not config.webhook_enabled:
        return None
    return WebhookServer(
        store,
        review_label=config.review_label,
        secret_token=config.webhook_secret_token,
        host=config.webhook_host,
        port=config.webhook_port,
        path=config.webhook_path,
        on_detected=on_detected,
    )


def build_on_detected(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    job_repo: JobRepository,
    config: Config,
) -> Callable[[DetectedReview], None]:
    """`DetectedReview`ごとに`execute_review_job`を呼ぶコールバックを組み立てる。

    既知のパイプライン例外(`_PIPELINE_ERROR_TYPES`)はログに記録して握りつぶし、
    呼び出し元(`MrPoller.run`のループ)を止めない。それ以外の想定外の例外は
    そのまま伝播させる(モジュールdocstring参照)。
    """

    def _on_detected(review: DetectedReview) -> None:
        # 1MR分のレビュー実行ごとに新しいexecution_idを振る(同一watchプロセス内で
        # 複数MRを順次処理しても、ログを実行単位で追跡できるようにするため)
        with execution_id_scope():
            try:
                execute_review_job(
                    job_repo,
                    adapter,
                    workspace,
                    runner,
                    store,
                    config,
                    review.project,
                    review.mr_iid,
                    sha=review.commit_sha,
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
    # (同じstate.dbを指す設定=同一の稼働対象、という前提で自然に一意になる)。
    # ただし`:memory:`(SqliteStateStoreがインメモリDBとして特別扱いする値、主にテスト用)は
    # そのまま`with_suffix`するとファイル名に`:`を含んでしまいWindowsで不正なパスになるため、
    # 固定のロックファイル名にフォールバックする
    if state_db_path == ":memory:":
        return Path(".gitlab-ai-platform-watch-memory.lock")
    return Path(state_db_path).with_suffix(".lock")


__all__ = ["build_on_detected", "run_watch", "run_watch_loop"]
