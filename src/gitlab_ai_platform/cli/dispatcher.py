"""Runner Dispatcher: `JobRepository.claim`でJobを取り出し、別プロセスとして処理し続ける。

方針(M3-3 [#93](https://github.com/AtsushiNi/gitlab-ai-platform/issues/93)、
`docs/adr/0020-runner-process-separation.md`):

- `cli/single_run.py`の`execute_review_job`(`enqueue`直後に同一プロセス内で同期処理する経路)は
  変更しない。本モジュールはそれとは別の経路として、`JobRepository.claim`(M3-2、
  `docs/adr/0017-job-queue.md`)でJobを取り出して処理する常駐/単発実行のループを提供する。
  `cli/watch.py`(パイプライン本体+合成ルートを同じファイルに置く配置パターン)を踏襲する。
- Job受け渡しプロトコル: `JobType` → `JobHandler`(`Callable[[Job], dict[str, Any] | None]`)の
  ディスパッチテーブル(`build_job_handlers`)を組み立て、`RunnerDispatcher`はそのテーブルと
  `JobRepository`だけを知っていればよい(`review`固有のロジックを知らない)。M4で
  `issue-analysis`/`design`/`implement`用のhandlerを追加する際も`RunnerDispatcher`自体は
  無改修で済む。
- `review`種別の`JobHandler`(`build_review_handler`)は`execute_review`(GitLab Adapter→
  Workspace Manager→Claude Code Runner→Review→State Storeの結線本体、変更しない)をそのまま
  呼び出す。
- `job_types`省略時は`handlers`に登録済みの種別のみをclaim対象にする(未実装種別を誤って
  claimして即デッドレター化させないため)。
- Jobの処理中は専用のdaemonスレッドで一定間隔ごとに`heartbeat`を呼び、可視性タイムアウトの
  リースを延長する。
- `handler`が`NotImplementedError`を送出した場合(未対応のJobType、ADR-0016の契約)は
  `fail(..., retry=False)`で即座にデッドレター化する。それ以外の例外は`fail(..., retry=True)`
  とし、リトライ上限判定は`JobRepository`(ADR-0017の`attempts`/`max_attempts`)に委ねる。
  1件のJobの失敗は他のJobの処理を止めない(`watch`の「想定外の例外はプロセスを落とす」方針
  [ADR-0009]とは異なる。`worker`は無人・ヘッドレスな運用を前提とするため、ADR-0020参照)。
- 排他は`JobRepository.claim`のアトミックなUPDATE文(ADR-0017)に委ね、`ProcessLock`
  (`cli/lock.py`)は使わない。複数の`worker`プロセス(将来は複数ホスト)が同一`job_db_path`に
  対して同時に稼働することを前提とする設計そのものであるため(ADR-0020「決定」参照)。
"""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..config import Config
from ..gitlab_adapter import GitLabRestAdapter
from ..gitlab_adapter.protocol import GitLabReader
from ..job import SqliteJobRepository
from ..job.errors import JobError, LeaseLostError
from ..job.protocol import (
    DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    Job,
    JobRepository,
    JobType,
)
from ..logging_ import execution_id_scope, get_logger
from ..review import (
    REVIEW_JOB_TYPE,
    build_review_job_result,
    review_job_payload_to_args,
)
from ..runner import SubprocessClaudeCodeRunner
from ..runner.protocol import ClaudeCodeRunner
from ..store import SqliteStateStore
from ..store.protocol import StateStore
from ..workspace.protocol import WorkspaceManager
from .single_run import build_workspace_manager, execute_review

_logger = get_logger(__name__)

# claimが空振り(処理対象なし)した際、次のclaimまで待つ既定秒数
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
# Job処理中にリースを延長するためheartbeatを呼ぶ既定間隔(秒)。
# DEFAULT_VISIBILITY_TIMEOUT_SECONDS(既定600秒)より十分短く保つ(ADR-0020)
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 120.0

# JobType→処理関数のディスパッチテーブルの値の型(ADR-0020「Job受け渡しプロトコル」)。
# `complete`にそのまま渡せる`result`(またはNone)を返す契約
JobHandler = Callable[[Job], "dict[str, Any] | None"]


def build_review_handler(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
) -> JobHandler:
    """review種別の`JobHandler`を組み立てる。

    `execute_review`本体(GitLab Adapter→Workspace Manager→Claude Code Runner→Review→
    State Storeの結線)は変更しない。`execute_review_job`(`cli/single_run.py`)と同じ
    payload分解・result組み立てロジックを再利用する。
    """

    def _handle(job: Job) -> dict[str, Any]:
        project, mr_iid, sha = review_job_payload_to_args(job.payload)
        result = execute_review(
            adapter, workspace, runner, store, config, project, mr_iid, sha=sha
        )
        return build_review_job_result(
            result.project, result.mr_iid, result.sha, str(result.review_paths.dir)
        )

    return _handle


def build_job_handlers(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
) -> dict[JobType, JobHandler]:
    """`JobType` → `JobHandler`のディスパッチテーブルを組み立てる(ADR-0020)。

    現時点では`review`のみ実装済み。`issue-analysis`/`design`/`implement`(M4)は、対応する
    handlerをこの辞書に追加するだけで`RunnerDispatcher`側の変更なしに配線できる想定。
    """
    return {
        REVIEW_JOB_TYPE: build_review_handler(
            adapter, workspace, runner, store, config
        ),
    }


class RunnerDispatcher:
    """`JobRepository.claim`でJobを取り出し、対応する`JobHandler`で処理するループ本体。

    `job_repo`/`handlers`ともにProtocol型・関数のマッピングのみに依存し、`review`固有の
    ロジックを知らない(ADR-0020)。
    """

    def __init__(
        self,
        job_repo: JobRepository,
        handlers: Mapping[JobType, JobHandler],
        *,
        worker_id: str,
        job_types: Sequence[JobType] | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    ) -> None:
        self._job_repo = job_repo
        self._handlers = dict(handlers)
        self._worker_id = worker_id
        # 未指定ならhandlersに登録済みの種別のみをclaim対象にする(未実装種別を誤って
        # claimしてデッドレター化させないため、ADR-0020「決定」参照)
        self._job_types: tuple[JobType, ...] = (
            tuple(job_types) if job_types is not None else tuple(self._handlers.keys())
        )
        self._poll_interval_seconds = poll_interval_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._visibility_timeout_seconds = visibility_timeout_seconds

    def run_once(self) -> bool:
        """1件Jobをclaimして処理する。claimできるJobが無ければ`False`を返す。"""
        job = self._job_repo.claim(
            self._worker_id,
            job_types=self._job_types or None,
            visibility_timeout_seconds=self._visibility_timeout_seconds,
        )
        if job is None:
            return False
        # 1Job分の実行ごとに新しいexecution_idを振る(同一workerプロセス内で複数Jobを
        # 順次処理してもログを実行単位で追跡できるようにするため、`cli/watch.py`と同じ方針)
        with execution_id_scope():
            self._process(job)
        return True

    def run_forever(self, stop_event: threading.Event) -> None:
        """`stop_event`がセットされるまで、claim→処理を繰り返す。

        claimが空振りした場合のみ`poll_interval_seconds`だけ待つ(ADR-0020「決定」)。
        """
        while not stop_event.is_set():
            processed = self.run_once()
            if not processed:
                stop_event.wait(self._poll_interval_seconds)

    def _process(self, job: Job) -> None:
        """1件のJobを処理し、結果に応じて`complete`/`fail`を呼び分ける(ADR-0020)。

        1件の失敗は他のJobの処理を止めない(例外を再送出しない)。`worker`は無人・ヘッドレスな
        運用を前提とするため、`watch`の「想定外の例外はプロセスを落とす」方針とは意図的に異なる。
        """
        handler = self._handlers.get(job.job_type)
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(job.id, heartbeat_stop),
            daemon=True,
            name=f"dispatcher-heartbeat-{job.id}",
        )
        heartbeat_thread.start()
        try:
            if handler is None:
                # ADR-0016: 未実装のJobTypeを受け取った場合はNotImplementedErrorを送出する契約
                raise NotImplementedError(f"未対応のJobTypeです: {job.job_type.value}")
            result = handler(job)
        except NotImplementedError as exc:
            # 未実装種別はリトライしても状況が変わらないため、即座にデッドレター化する
            self._job_repo.fail(job.id, self._worker_id, str(exc), retry=False)
            _logger.error(
                "dispatcher.job_type_not_implemented",
                extra={"job_id": job.id, "job_type": job.job_type.value},
            )
        except Exception as exc:  # noqa: BLE001 - 1件の失敗を他Jobから隔離するため
            self._job_repo.fail(job.id, self._worker_id, str(exc), retry=True)
            _logger.error(
                "dispatcher.job_failed",
                extra={
                    "job_id": job.id,
                    "job_type": job.job_type.value,
                    "error": str(exc),
                },
            )
        else:
            self._job_repo.complete(job.id, self._worker_id, result=result)
            _logger.info(
                "dispatcher.job_completed",
                extra={"job_id": job.id, "job_type": job.job_type.value},
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join()

    def _heartbeat_loop(self, job_id: str, stop_event: threading.Event) -> None:
        """`stop_event`がセットされるまで、一定間隔ごとにリースを延長する(ADR-0020)。"""
        while not stop_event.wait(self._heartbeat_interval_seconds):
            try:
                self._job_repo.heartbeat(
                    job_id,
                    self._worker_id,
                    visibility_timeout_seconds=self._visibility_timeout_seconds,
                )
            except LeaseLostError:
                # 既に可視性タイムアウトを超えて別workerに再取得された。これ以上延長を
                # 試みても意味がないため、ログのみでスレッドを終了する(handler本体は
                # 中断せず最後まで実行させる。State Storeのレコードを不整合な状態のまま
                # 放置しないため)
                _logger.warning(
                    "dispatcher.heartbeat_lease_lost", extra={"job_id": job_id}
                )
                return
            except JobError as exc:
                _logger.warning(
                    "dispatcher.heartbeat_failed",
                    extra={"job_id": job_id, "error": str(exc)},
                )


def default_worker_id() -> str:
    """`--worker-id`省略時に使う既定のworker識別子(`hostname:pid`)を組み立てる。"""
    return f"{socket.gethostname()}:{os.getpid()}"


def run_dispatcher(
    config: Config,
    *,
    stop_event: threading.Event | None = None,
    worker_id: str | None = None,
    job_types: Sequence[JobType] | None = None,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    run_once: bool = False,
) -> None:
    """`config`から具象実装を組み立て、Runner Dispatcherを起動する(合成ルート、ADR-0020)。

    `run_single_review`/`run_watch`と同じ流儀で`GitLabRestAdapter`/`build_workspace_manager`/
    `SubprocessClaudeCodeRunner`/`SqliteStateStore`/`SqliteJobRepository`を組み立てる。
    複数の`worker`プロセス(別ホストも可)が同一`config.job_db_path`に対して同時に起動される
    ことを前提とする設計のため、`watch`と異なり`ProcessLock`は使わない(排他は
    `JobRepository.claim`のアトミックなUPDATE文がプロセス/ホスト境界を越えて担保する)。
    """
    adapter = GitLabRestAdapter(config.gitlab_url, config.gitlab_token)
    workspace = build_workspace_manager(config)
    runner = SubprocessClaudeCodeRunner(config.runner_log_dir)
    store = SqliteStateStore(config.state_db_path)
    job_repo = SqliteJobRepository(config.job_db_path)
    try:
        handlers = build_job_handlers(adapter, workspace, runner, store, config)
        dispatcher = RunnerDispatcher(
            job_repo,
            handlers,
            worker_id=worker_id if worker_id is not None else default_worker_id(),
            job_types=job_types,
            poll_interval_seconds=poll_interval_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            visibility_timeout_seconds=visibility_timeout_seconds,
        )
        if run_once:
            dispatcher.run_once()
        else:
            effective_stop_event = (
                stop_event if stop_event is not None else threading.Event()
            )
            dispatcher.run_forever(effective_stop_event)
    finally:
        job_repo.close()
        store.close()


__all__ = [
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "JobHandler",
    "RunnerDispatcher",
    "build_job_handlers",
    "build_review_handler",
    "default_worker_id",
    "run_dispatcher",
]
