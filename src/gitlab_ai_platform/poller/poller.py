"""対象プロジェクトを定期走査し、レビュー待ちMRの未処理commitを起票する MR Poller。

方針(M1-5 [#33](https://github.com/AtsushiNi/gitlab-ai-platform/issues/33)、
`docs/architecture.md`「MR Poller」):

- GitLab Adapter(M1-1/M1-2)の`GitLabReader`のみに依存する。書き込み操作
  (`GitLabWriter`)は型ヒントにも登場させないことで、GitLabへの書き込みをしないという
  境界を構造的に保証する(プロンプトの約束事に頼らないという`docs/architecture.md`の方針と同様)。
- State Store(M1-4)の`find`で`(project, mr_iid, commit_sha)`が未処理かを確認してから
  `create`で起票する。`find`から`create`までの間に別プロセス(複数Poller稼働時)が
  同じレコードを作っていた場合は、`create`が送出する`DuplicateReviewError`を「既に
  起票済み」として無視する(二重起票の防止自体はState Store側の一意制約が担う、
  `docs/adr/0003-state-store-interface.md`)。
- 1プロジェクトの走査失敗(GitLab APIエラー)や1レコードの起票失敗(State Store障害)は、
  他プロジェクト・他MRの処理を止めない。失敗は`PollResult.errors`に集約して返し、
  呼び出し側(CLI)がログ・監視に使えるようにする。
- ポーリング間隔でのループ(`run`)自体はPollerの責務(`docs/architecture.md`の
  「30〜60秒間隔で走査」)。プロセスのgraceful shutdown(SIGINT/SIGTERM等)はCLI(M1-10/11)の
  責務であり、`stop_event`経由でPollerのループに停止を伝える形で連携する。
- Webhookは当面採用しない(MVP時点)。走査対象プロジェクトは構築時に渡された一覧固定で、
  実行中の動的な追加/削除は扱わない。
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

from ..gitlab_adapter.errors import GitLabAdapterError
from ..gitlab_adapter.protocol import GitLabReader
from ..logging_ import get_logger
from ..store.errors import DuplicateReviewError, StateStoreError
from ..store.protocol import StateStore
from .types import DetectedReview, PollError, PollResult

_logger = get_logger(__name__)


class MrPoller:
    """`レビュー待ち`ラベルのMRを走査し、未処理commitをState Storeに起票する。"""

    def __init__(
        self,
        adapter: GitLabReader,
        store: StateStore,
        projects: Sequence[str],
        *,
        review_label: str,
    ) -> None:
        self._adapter = adapter
        self._store = store
        self._projects = tuple(projects)
        self._review_label = review_label

    def poll_once(self) -> PollResult:
        """全対象プロジェクトを1回走査し、新規起票と継続可能なエラーをまとめて返す。"""
        created: list[DetectedReview] = []
        errors: list[PollError] = []

        for project in self._projects:
            try:
                merge_requests = self._adapter.list_merge_requests(
                    project, labels=(self._review_label,)
                )
            except GitLabAdapterError as exc:
                _logger.warning(
                    "poller.project_scan_failed",
                    extra={"project": project, "error": str(exc)},
                )
                errors.append(PollError(project=project, mr_iid=None, message=str(exc)))
                continue

            for mr in merge_requests:
                outcome = self._ticket_if_unprocessed(mr.project, mr.iid, mr.sha)
                if isinstance(outcome, DetectedReview):
                    created.append(outcome)
                elif isinstance(outcome, PollError):
                    errors.append(outcome)

        return PollResult(created=tuple(created), errors=tuple(errors))

    def run(
        self,
        *,
        interval_seconds: int,
        stop_event: threading.Event | None = None,
    ) -> None:
        """`interval_seconds`間隔で`poll_once`を繰り返す。

        `stop_event`がセットされると、実行中のサイクルの完了後に停止する
        (省略時は呼び出し側が別途プロセスを止める手段を用意する必要がある)。
        """
        stop_event = stop_event if stop_event is not None else threading.Event()
        while not stop_event.is_set():
            result = self.poll_once()
            _logger.info(
                "poller.cycle_completed",
                extra={"created": len(result.created), "errors": len(result.errors)},
            )
            stop_event.wait(interval_seconds)

    def _ticket_if_unprocessed(
        self, project: str, mr_iid: int, commit_sha: str
    ) -> DetectedReview | PollError | None:
        try:
            if self._store.find(project, mr_iid, commit_sha) is not None:
                return None
            self._store.create(project, mr_iid, commit_sha)
        except DuplicateReviewError:
            # findからcreateまでの間の競合(複数Poller稼働時等)。二重起票にはならないため無視する
            _logger.debug(
                "poller.duplicate_review_ignored",
                extra={"project": project, "mr_iid": mr_iid, "commit_sha": commit_sha},
            )
            return None
        except StateStoreError as exc:
            _logger.error(
                "poller.ticket_creation_failed",
                extra={
                    "project": project,
                    "mr_iid": mr_iid,
                    "commit_sha": commit_sha,
                    "error": str(exc),
                },
            )
            return PollError(project=project, mr_iid=mr_iid, message=str(exc))

        _logger.info(
            "poller.review_ticketed",
            extra={"project": project, "mr_iid": mr_iid, "commit_sha": commit_sha},
        )
        return DetectedReview(project=project, mr_iid=mr_iid, commit_sha=commit_sha)


__all__ = ["MrPoller"]
