"""対象プロジェクトを定期走査し、無人実行ラベル付きIssueを検出してJobをキューへ投入するIssue Poller。

方針(M4-1 [#107](https://github.com/AtsushiNi/gitlab-ai-platform/issues/107)、
`docs/adr/0025-issue-poller-dedup.md`、`docs/architecture.md`):

- MR Poller(`poller/poller.py`)と同じ設計パターンを踏襲する: GitLab Adapter(M1-1/M1-2)の
  `GitLabReader`のみに依存し、書き込み操作(`GitLabWriter`)は型ヒントにも登場させない。
  1プロジェクトの走査失敗・1件のIssue処理失敗は他の処理を止めず、`IssuePollResult.errors`に
  集約する。ポーリング間隔での`run`ループ・`on_detected`コールバック・`stop_event`経由の
  graceful shutdown連携もMR Pollerと同じ形にする。
- MR Pollerとの最大の違いは、**起票先がJob Queueへの投入である**点(Issue #107本文
  「無人実行Jobをキューへ投入する」)。MR Pollerは検出をState Storeへの記録に留め、実際の
  レビュー実行(≒Job化)はCLI(`cli/watch.py`)側の`on_detected`が担うが、Issue Pollerは
  M4が「無人実行トラック」であり実行はRunner Dispatcher(M3-3)がJob Queueから拾う設計のため、
  検出した時点でPoller自身が`JobRepository.enqueue`を呼ぶ。
- 二重投入防止は専用のIssue Ticket Store(`issue_store/`)が担う。State Storeのような
  `(project, mr_iid, commit_sha)`の「版」の概念がIssueには無いため、`(project, issue_iid)`が
  一度でも起票されたら以後は永続的にスキップする(MRの「新しいcommit_shaなら再起票」に相当する
  仕組みは持たない。ADR-0025「却下した選択肢」参照)。
- `ticket_issue_if_unprocessed`は「Issue Ticket Storeへの記録」→「Job Queueへの投入」の順で
  行う。Job投入が失敗した場合、Issue Ticket Storeのレコードは残ったままになり(次回以降の
  ポーリングでは再試行されない)、これは意図的に受け入れているリスクである
  (ADR-0025「影響」参照。MR PollerのState Storeレコードが`execute_review`失敗後もPENDINGの
  まま残りうるのと同種のトレードオフ)。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any

from ..gitlab_adapter.errors import GitLabAdapterError
from ..gitlab_adapter.protocol import GitLabReader
from ..issue_store.errors import DuplicateIssueTicketError, IssueTicketStoreError
from ..issue_store.protocol import IssueTicketStore
from ..job.errors import JobError
from ..job.protocol import JobRepository, JobType
from ..logging_ import get_logger
from .issue_types import DetectedIssue, IssuePollError, IssuePollResult

_logger = get_logger(__name__)


class IssuePoller:
    """無人実行ラベルの付いたIssueを走査し、未処理Issueに対して無人実行Jobを投入する。"""

    def __init__(
        self,
        adapter: GitLabReader,
        store: IssueTicketStore,
        job_repo: JobRepository,
        projects: Sequence[str],
        *,
        issue_label: str,
    ) -> None:
        self._adapter = adapter
        self._store = store
        self._job_repo = job_repo
        self._projects = tuple(projects)
        self._issue_label = issue_label

    def poll_once(self) -> IssuePollResult:
        """全対象プロジェクトを1回走査し、新規投入と継続可能なエラーをまとめて返す。"""
        created: list[DetectedIssue] = []
        errors: list[IssuePollError] = []

        for project in self._projects:
            try:
                issues = self._adapter.list_issues(project, labels=(self._issue_label,))
            except GitLabAdapterError as exc:
                _logger.warning(
                    "issue_poller.project_scan_failed",
                    extra={"project": project, "error": str(exc)},
                )
                errors.append(
                    IssuePollError(project=project, issue_iid=None, message=str(exc))
                )
                continue

            for issue in issues:
                outcome = ticket_issue_if_unprocessed(
                    self._store, self._job_repo, issue.project, issue.iid
                )
                if isinstance(outcome, DetectedIssue):
                    created.append(outcome)
                elif isinstance(outcome, IssuePollError):
                    errors.append(outcome)

        return IssuePollResult(created=tuple(created), errors=tuple(errors))

    def run(
        self,
        *,
        interval_seconds: int,
        stop_event: threading.Event | None = None,
        on_detected: Callable[[DetectedIssue], None] | None = None,
    ) -> None:
        """`interval_seconds`間隔で`poll_once`を繰り返す。

        `stop_event`がセットされると、実行中のサイクルの完了後に停止する
        (省略時は呼び出し側が別途プロセスを止める手段を用意する必要がある)。
        `on_detected`を渡すと、そのサイクルで新たに投入された`DetectedIssue`ごとに
        (`result.created`の順番で)呼び出す。`on_detected`が送出する例外はそのまま
        `run`の外へ伝播する(MR Pollerと同じ契約)。
        """
        stop_event = stop_event if stop_event is not None else threading.Event()
        while not stop_event.is_set():
            result = self.poll_once()
            _logger.info(
                "issue_poller.cycle_completed",
                # extraのキーは`logging.LogRecord`の予約属性と衝突を避けるため
                # "created_count"を使う(MR Pollerと同じ理由)
                extra={
                    "created_count": len(result.created),
                    "error_count": len(result.errors),
                },
            )
            if on_detected is not None:
                for issue in result.created:
                    on_detected(issue)
            stop_event.wait(interval_seconds)


def build_issue_analysis_job_payload(project: str, issue_iid: int) -> dict[str, Any]:
    """`issue-analysis`種別Jobの`payload`を組み立てる。

    `review/job.py`の`build_review_job_payload`と同じ役割。MR側とは異なりIssueの内容
    (タイトル・説明等)は含めない。取得はJobを処理する側(M4-2/#108以降のJobHandler)が
    `GitLabReader.get_issue`で実行時に行う設計とし、Poller検出時点のスナップショットを
    payloadに固定しない(取得と実行の間に発生しうるIssue編集を常に最新の内容で処理するため)。
    """
    return {"project": project, "issue_iid": issue_iid}


def issue_analysis_job_payload_to_args(payload: dict[str, Any]) -> tuple[str, int]:
    """`issue-analysis`種別Jobの`payload`から`(project, issue_iid)`を取り出す。

    M4-3([#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109))で追加される
    Runner Dispatcher側のJobHandlerが利用する想定。
    """
    return payload["project"], payload["issue_iid"]


def ticket_issue_if_unprocessed(
    store: IssueTicketStore, job_repo: JobRepository, project: str, issue_iid: int
) -> DetectedIssue | IssuePollError | None:
    """`(project, issue_iid)`が未処理ならIssue Ticket Storeに起票し、無人実行Jobを投入する。

    既に起票済みなら`None`を返す。`find`から`create`までの間に別プロセス(複数Poller稼働時)が
    同じレコードを作っていた場合は、`create`が送出する`DuplicateIssueTicketError`を
    「既に起票済み」として無視する(二重投入防止自体はIssue Ticket Store側の一意制約が担う、
    `docs/adr/0025-issue-poller-dedup.md`)。
    """
    try:
        if store.find(project, issue_iid) is not None:
            return None
        store.create(project, issue_iid)
    except DuplicateIssueTicketError:
        _logger.debug(
            "issue_poller.duplicate_ticket_ignored",
            extra={"project": project, "issue_iid": issue_iid},
        )
        return None
    except IssueTicketStoreError as exc:
        _logger.error(
            "issue_poller.ticket_creation_failed",
            extra={"project": project, "issue_iid": issue_iid, "error": str(exc)},
        )
        return IssuePollError(project=project, issue_iid=issue_iid, message=str(exc))

    try:
        job = job_repo.enqueue(
            JobType.ISSUE_ANALYSIS,
            build_issue_analysis_job_payload(project, issue_iid),
        )
    except JobError as exc:
        # Issue Ticket Storeへの記録は既に済んでいるため、このIssueは次回以降の
        # ポーリングでは再試行されない(モジュールdocstring参照、意図的に受け入れているリスク)
        _logger.error(
            "issue_poller.job_enqueue_failed",
            extra={"project": project, "issue_iid": issue_iid, "error": str(exc)},
        )
        return IssuePollError(project=project, issue_iid=issue_iid, message=str(exc))

    _logger.info(
        "issue_poller.issue_ticketed",
        extra={"project": project, "issue_iid": issue_iid, "job_id": job.id},
    )
    return DetectedIssue(project=project, issue_iid=issue_iid, job_id=job.id)


__all__ = [
    "IssuePoller",
    "build_issue_analysis_job_payload",
    "issue_analysis_job_payload_to_args",
    "ticket_issue_if_unprocessed",
]
