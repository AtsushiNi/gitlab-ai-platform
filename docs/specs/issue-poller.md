# Issue Poller

- 実装場所: `src/gitlab_ai_platform/poller/issue_poller.py`(型は`poller/issue_types.py`)、
  二重投入防止用の状態管理は`src/gitlab_ai_platform/issue_store/`
- 対応Issue: [#107](https://github.com/AtsushiNi/gitlab-ai-platform/issues/107) (M4-1)
- 関連ADR: [ADR-0024](../adr/0024-issue-poller-dedup.md)
- ステータス: 実装済み(検出・二重投入防止・Job投入までが対象。CLI結線・Job実行は別Issue)

## 責務

対象プロジェクトを定期的に走査し、無人実行ラベル(既定`AI実装`、`config`で設定可能)が付いた
Issueを抽出する。Issue Ticket Storeと突き合わせて`(project, issue_iid)`が未処理なものを検出し、
Issue Ticket Storeにレコードを作成する(起票する)と同時に、`JobType.ISSUE_ANALYSIS`のJobを
`JobRepository`に投入する。

MR Poller([poller.md](poller.md)、M1-5)と同じ設計パターン(GitLabReaderのみに依存、1件の
失敗が他を止めない、`run`ループ+`on_detected`コールバック)を踏襲するが、**起票先が
Job Queueへの直接投入である点がMR Pollerと異なる**([ADR-0024](../adr/0024-issue-poller-dedup.md)
「背景・制約」)。MR Pollerは検出をState Storeへの記録に留め、実際のレビュー実行(Job化)は
CLI側の`on_detected`が担うが、Issue Pollerは検出と同時にJobを投入する。

## 前提と非対象

- 前提:
  - GitLab Adapter(M1-1/M1-2、`gitlab_adapter.protocol.GitLabReader`)・Issue Ticket Store
    (本Issue、`issue_store.protocol.IssueTicketStore`)・Job Repository(M3-1/M3-2、
    `job.protocol.JobRepository`)がいずれもProtocol型として利用可能であること。
    `IssuePoller`はこの3つのProtocol型にのみ依存し、具象実装
    (`GitLabRestAdapter`/`SqliteIssueTicketStore`/`SqliteJobRepository`)には直接依存しない
  - 呼び出し側(CLI、将来のIssue)が対象プロジェクト一覧・無人実行ラベル名・ポーリング間隔を
    `config`(`gitlab_ai_platform.config.Config`)から組み立てて渡すこと
  - `list_issues`(M2-10で実装済み、`gitlab_adapter.protocol.GitLabReader.list_issues`)を
    そのまま利用する。新規実装はしない
- 非対象:
  - GitLabへの書き込みは一切しない。コンストラクタの型ヒントも読み取り専用の`GitLabReader`
    のみを受け付ける(MR Pollerと同じ境界)
  - `issue-analysis` Jobの実行(GitLab AdapterでのIssue取得、Runnerプロンプトへの正規化、
    要求分析の実施)はしない。「投入する」とはJob Repositoryにレコードを作成することのみを
    指す。実行はRunner Dispatcher(M3-3)側にJobHandlerを追加するM4-2/M4-3
    ([#108](https://github.com/AtsushiNi/gitlab-ai-platform/issues/108)、
    [#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109))のスコープ
  - 走査対象プロジェクトはPoller構築時に渡された一覧固定で、実行中の動的な追加/削除は
    扱わない。Webhookは扱わない(MR Pollerと同じ、[ADR-0024](../adr/0024-issue-poller-dedup.md)
    「背景・制約」)
  - プロセスのgraceful shutdown(シグナルハンドリング)・多重起動防止はしない。`run`は
    `stop_event`(`threading.Event`)を受け取って停止するだけで、シグナルの登録自体は
    CLI側の責務(MR Pollerと同じ、[ADR-0007](../adr/0007-mr-poller-design.md))
  - CLI(`cli/watch.py`等)への結線は本Issueのスコープ外。`IssuePoller.run`を呼び出す
    合成ルートは別Issueで追加する(MR Poller(M1-5)がCLI結線(M1-11)と別Issueだったのと
    同じ切り方)
  - 一度`(project, issue_iid)`が起票されると、以後は永続的にスキップされる。MRの
    「新しいcommit_shaなら再起票」に相当する再処理の仕組みは持たない(Issueには`commit_sha`に
    相当する「版」の概念が無いため。[ADR-0024](../adr/0024-issue-poller-dedup.md)「決定」参照)

## 公開インターフェース

実装場所: `src/gitlab_ai_platform/poller/issue_poller.py`。`typing.Protocol`は使わず、
`GitLabReader`/`IssueTicketStore`/`JobRepository`という既存の3つのProtocolを組み合わせる
具象クラス(MR Pollerと同じ方針、[ADR-0007](../adr/0007-mr-poller-design.md))。

```python
import threading
from collections.abc import Callable, Sequence

from gitlab_ai_platform.gitlab_adapter import GitLabReader
from gitlab_ai_platform.issue_store import IssueTicketStore
from gitlab_ai_platform.job import JobRepository
from gitlab_ai_platform.poller import DetectedIssue


class IssuePoller:
    def __init__(
        self,
        adapter: GitLabReader,
        store: IssueTicketStore,
        job_repo: JobRepository,
        projects: Sequence[str],
        *,
        issue_label: str,
    ) -> None:
        """走査対象プロジェクト一覧と無人実行ラベル名を固定して構築する。"""

    def poll_once(self) -> "IssuePollResult":
        """全対象プロジェクトを1回走査し、新規投入と継続可能なエラーをまとめて返す。"""

    def run(
        self,
        *,
        interval_seconds: int,
        stop_event: threading.Event | None = None,
        on_detected: Callable[[DetectedIssue], None] | None = None,
    ) -> None:
        """`interval_seconds`間隔で`poll_once`を繰り返す。`stop_event`がセットされると、
        実行中のサイクル完了後に停止する。`on_detected`を渡すと、そのサイクルで新たに
        投入された`DetectedIssue`ごと(`result.created`の順)に呼ぶ。`on_detected`が
        送出する例外は`run`の外へそのまま伝播する。"""
```

`ticket_issue_if_unprocessed`は「Issue Ticket Storeへの記録」→「Job Queueへの投入」の
二重投入防止ロジック本体をモジュール関数として切り出したもの(MR Pollerの
`ticket_if_unprocessed`と対になる):

```python
from gitlab_ai_platform.issue_store import IssueTicketStore
from gitlab_ai_platform.job import JobRepository
from gitlab_ai_platform.poller import (
    DetectedIssue,
    IssuePollError,
    ticket_issue_if_unprocessed,
)


def ticket_issue_if_unprocessed(
    store: IssueTicketStore, job_repo: JobRepository, project: str, issue_iid: int
) -> DetectedIssue | IssuePollError | None:
    """`(project, issue_iid)`が未処理ならIssue Ticket Storeに起票し、無人実行Jobを投入する。
    既に起票済みなら`None`。Issue Ticket Storeへの記録に(`DuplicateIssueTicketError`以外で)
    失敗した場合、またはJob投入(`job_repo.enqueue`)に失敗した場合は`IssuePollError`を返す
    (例外は送出しない)。"""
```

`build_issue_analysis_job_payload(project, issue_iid) -> dict[str, Any]`/
`issue_analysis_job_payload_to_args(payload) -> tuple[str, int]`は`issue-analysis`種別Jobの
`payload`の組み立て・分解を行うヘルパー(`review/job.py`の`build_review_job_payload`と同じ役割)。
payloadは`{"project": str, "issue_iid": int}`のみを持ち、Issueのタイトル・説明等は含めない
(実行時にJobHandler側が`GitLabReader.get_issue`で最新の内容を取得する設計、
[ADR-0024](../adr/0024-issue-poller-dedup.md)「影響」)。

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/poller/issue_types.py`。

| 型 | フィールド | 補足 |
|---|---|---|
| `DetectedIssue` (frozen dataclass) | `project: str`, `issue_iid: int`, `job_id: str` | 1回のポーリングで新たに起票・Job投入されたIssue。`job_id`は投入した`Job.id` |
| `IssuePollError` (frozen dataclass) | `project: str`, `issue_iid: int \| None`, `message: str` | 走査を継続可能なエラー。`issue_iid`はプロジェクト単位の走査自体が失敗した場合に`None` |
| `IssuePollResult` (frozen dataclass) | `created: tuple[DetectedIssue, ...]`, `errors: tuple[IssuePollError, ...]` | `poll_once`1回分の結果 |

Issueそのものの型は`gitlab_adapter.types.Issue`をそのまま利用する。Issue Ticket Storeの記録は
`issue_store.types.IssueTicketRecord`(`project: str`, `issue_iid: int`,
`ticketed_at: datetime`)。

## エラー時の振る舞い

Issue Pollerは独自の例外型を持たない。GitLab Adapter(`gitlab_adapter.errors`)・Issue Ticket
Store(`issue_store.errors`)・Job Repository(`job.errors`)の例外型を握り、継続可能なものは
`IssuePollResult.errors`に集約してログに残し、呼び出し側には例外として伝播させない。

| 例外 | 発生元 | Issue Pollerの扱い |
|---|---|---|
| `GitLabAdapterError`(`GitLabApiError`等) | `adapter.list_issues` | そのプロジェクトの走査を打ち切り、`IssuePollError(issue_iid=None)`として記録して継続(ログ`issue_poller.project_scan_failed`) |
| `DuplicateIssueTicketError` | `store.create` | 無視する(既に起票済みとして扱う。ログには`debug`レベルで記録`issue_poller.duplicate_ticket_ignored`) |
| `DuplicateIssueTicketError`以外の`IssueTicketStoreError` | `store.find` / `store.create` | そのIssueの処理を打ち切り、`IssuePollError(issue_iid=...)`として記録して継続(ログ`issue_poller.ticket_creation_failed`) |
| `JobError` | `job_repo.enqueue` | そのIssueの処理を打ち切り、`IssuePollError(issue_iid=...)`として記録して継続(ログ`issue_poller.job_enqueue_failed`)。**Issue Ticket Storeへの記録は既に成功しているため、そのIssueは次回以降のポーリングで再試行されない**([ADR-0024](../adr/0024-issue-poller-dedup.md)「決定」の意図的なトレードオフ) |

`run`自体は例外を送出しない前提(`poll_once`が内部で継続可能なエラーを吸収するため)。
`store`/`job_repo`/`adapter`の初期化失敗など、継続不能な設定不備は呼び出し側(CLI)が
Poller構築前に検知する。

## テスト方針

実装場所: `tests/gitlab_ai_platform/poller/`(`test_issue_poller.py`/`test_issue_types.py`)、
`tests/gitlab_ai_platform/issue_store/`(`src/`をミラー、[ADR-0001](../adr/0001-repository-structure.md))。

- `test_issue_types.py`: `DetectedIssue`/`IssuePollResult`のイミュータブル性、
  `IssuePollError.issue_iid`がプロジェクト単位の失敗で`None`になりうることを検証する
- `test_issue_poller.py`: `GitLabReader`/`IssueTicketStore`/`JobRepository`を満たす手書き
  フェイク(`unittest.mock`は使わない)に対して`IssuePoller`を実行し、実サービスへは繋がない。
  以下を検証する:
  - 複数プロジェクトを走査し、未処理Issueがそれぞれ起票・Job投入されること
  - `list_issues`に`labels=(issue_label,)`が渡ること
  - Issue Ticket Storeに既に記録済みの`(project, issue_iid)`はスキップされ、Jobも投入
    されないこと
  - 1プロジェクトの走査失敗が他プロジェクトの走査を止めないこと
  - `create`が`DuplicateIssueTicketError`を送出した場合、`created`にも`errors`にも計上せず
    無視し、Jobも投入されないこと
  - `create`が(`DuplicateIssueTicketError`以外の)`IssueTicketStoreError`を送出した場合、
    そのIssueだけ`errors`に記録し、他のIssueの処理は継続すること
  - `job_repo.enqueue`が`JobError`を送出した場合、そのIssueは`errors`に記録されるが
    Issue Ticket Storeのレコードは残り、次回のポーリングでも再試行されないこと(回帰テスト)
  - `run`が`stop_event`のセットで停止し、`on_detected`がそのサイクルで投入された
    `DetectedIssue`ごとに呼ばれ、例外は`run`の外へ伝播すること
  - `ticket_issue_if_unprocessed`を直接呼び出し、契約(戻り値・Issue Ticket Store/Job
    Repositoryとのやり取り)そのものを検証する
- `issue_store/test_sqlite.py`: `SqliteIssueTicketStore`が`(project, issue_iid)`の一意制約を
  実際に守ること、複数スレッドからの同時書き込みで例外を起こさないこと、接続クローズ後の
  操作が`IssueTicketStoreError`にラップされることを検証する(`store/test_sqlite.py`と同じ方針)

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表のIssue Poller /
  Issue Ticket Store行
- [ADR-0024: Issue Poller の二重投入防止設計](../adr/0024-issue-poller-dedup.md)
- [poller.md](poller.md) — MR Poller(M1-5)の仕様。設計パターンの元
- [job-model.md](job-model.md) — Job抽象・状態機械(M3-1)の仕様。`JobType.ISSUE_ANALYSIS`への
  投入先
- ソースコード: `src/gitlab_ai_platform/poller/issue_poller.py` /
  `src/gitlab_ai_platform/poller/issue_types.py` / `src/gitlab_ai_platform/issue_store/`
