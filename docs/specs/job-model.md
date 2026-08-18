# Job Model

- 実装場所: `src/gitlab_ai_platform/job/`
- 対応Issue: [#91](https://github.com/AtsushiNi/gitlab-ai-platform/issues/91) (M3-1)、
  [#92](https://github.com/AtsushiNi/gitlab-ai-platform/issues/92) (M3-2)、
  [#93](https://github.com/AtsushiNi/gitlab-ai-platform/issues/93) (M3-3、Runner Dispatcher側の実配線)、
  [#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109) (M4-3、`wait_for_human`の追加)
- 関連ADR: [ADR-0016](../adr/0016-job-abstraction.md)、[ADR-0017](../adr/0017-job-queue.md)、
  [ADR-0022](../adr/0022-runner-process-separation.md)、
  [ADR-0026](../adr/0026-job-waiting-human-transition.md)
- ステータス: 実装済み(Protocol定義 + SQLite実装 + 既存レビュー処理のJob化 [M3-1] +
  取得の排他・可視性タイムアウト・リトライ・デッドレター [M3-2] +
  Runner Dispatcherによる実配線 [M3-3] + `WAITING_HUMAN`遷移の`wait_for_human` [M4-3])

## 責務

タスク種別(`review`/`issue-analysis`/`design`/`implement`)を横断して、1件のタスク実行の
ライフサイクル(`PENDING → RUNNING → (DONE | FAILED | WAITING_HUMAN)`という状態機械)を
記録・照会する。実装(SQLite)を`typing.Protocol`で抽象化し、呼び出し側(CLI・将来の
Orchestrator)を具象実装から切り離す。M3-2で、複数Runner(M3-3で別プロセス/別ホストに
分離される想定)からの排他取得(`claim`)・可視性タイムアウト・リトライ・デッドレターを
Job Repositoryのメソッドとして追加した([ADR-0017](../adr/0017-job-queue.md))。M3-3で、
`claim`/`heartbeat`/`complete`/`fail`を実際に呼び出すRunner Dispatcher(`cli/dispatcher.py`の
`RunnerDispatcher`、`worker`サブコマンド)を追加し、Jobを別プロセス/別ホストで処理できるように
なった([ADR-0022](../adr/0022-runner-process-separation.md)、詳細は[specs/cli.md](cli.md)の
`worker`サブコマンドの節)。

## 前提と非対象

- 前提:
  - 呼び出し側は`JobRepository`のProtocol型だけを見て実装し、具象クラス
    (`SqliteJobRepository`)に直接依存しない
  - `payload`/`result`は種別非依存の`dict[str, Any]`(JSON互換)として扱う。`review`固有の
    フィールド(対象project/MR/commit、結果保存先パス)はJob抽象そのものには持たせず、
    呼び出し側(`review/job.py`)が定義する([ADR-0016](../adr/0016-job-abstraction.md)
    「Jobはペイロード・結果を種別非依存のdictとして持つ」)
  - State Store(`store/`、[ADR-0003](../adr/0003-state-store-interface.md))は
    `(project, mr_iid, commit_sha)`単位の二重レビュー防止に責務を絞ったまま残る。Jobは
    State Storeを置き換えず、統合もしない、別コンポーネントとして併存する
  - `enqueue`/`get`/`update_status`/`list_by_status`/`close`(M3-1)は、既存の
    `execute_review_job`(`cli/single_run.py`)が使う「起票直後に同一プロセス内で同期処理する」
    経路として無改修のまま残っている。`claim`/`heartbeat`/`complete`/`fail`/
    `list_dead_letters`(M3-2)は、M3-3のRunner Dispatcher(`cli/dispatcher.py`の
    `RunnerDispatcher`、`worker`サブコマンド)が実際に呼び出す
    ([ADR-0017](../adr/0017-job-queue.md)、[ADR-0022](../adr/0022-runner-process-separation.md))
- 非対象:
  - `review`以外のJobType(`issue-analysis`/`design`/`implement`)の実際の実行(Runner
    Dispatcher側のhandler実装)はM4のスコープ。M3-1時点では値としての予約のみで、M3-3時点でも
    未実装種別をclaimすると`RunnerDispatcher`が`NotImplementedError`を送出し即座に
    デッドレター化する([ADR-0016](../adr/0016-job-abstraction.md)の契約、
    [ADR-0022](../adr/0022-runner-process-separation.md))
  - 二重レビュー防止そのもの(State Storeの責務のまま)。Jobは「実行1回分のライフサイクル」の
    管理に専念する
  - 専用のバックグラウンドスレッド/定期実行による可視性タイムアウトの回収は行わない。
    `claim`実行時に期限切れJobを回収する遅延評価方式とする([ADR-0017](../adr/0017-job-queue.md))

## 公開インターフェース

`JobRepository`を`@runtime_checkable`な`typing.Protocol`として定義する。
実装場所: `src/gitlab_ai_platform/job/protocol.py`(`JobType`/`JobStatus`/`Job`データクラスも
同ファイルに定義する。State Store等と異なり`types.py`を分けていない)。

```python
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_VISIBILITY_TIMEOUT_SECONDS = 600


class JobType(str, Enum):
    REVIEW = "review"
    ISSUE_ANALYSIS = "issue-analysis"  # M4で実装
    DESIGN = "design"  # M4で実装
    IMPLEMENT = "implement"  # M4で実装


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class Job:
    id: str
    job_type: JobType
    status: JobStatus
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    # M3-2で追加したキュー関連の付帯情報(末尾にデフォルト値付きで追加、既存フィールドは不変)
    attempts: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    dead_letter_at: datetime | None = None


@runtime_checkable
class JobRepository(Protocol):
    def enqueue(
        self,
        job_type: JobType,
        payload: dict[str, Any],
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> Job:
        """新しいJobを`PENDING`状態で作成する。`max_attempts`は`claim`によるリトライの上限。"""
        ...

    def get(self, job_id: str) -> Job | None:
        """指定IDのJobを返す。存在しなければ`None`。"""
        ...

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Job:
        """Jobの状態を更新する。不正な遷移は`InvalidJobTransitionError`、
        存在しないJobは`JobNotFoundError`を送出する。"""
        ...

    def list_by_status(self, status: JobStatus) -> list[Job]:
        """指定した状態のJobを一覧する。"""
        ...

    def close(self) -> None:
        """DB接続等の内部リソースを解放する。"""
        ...

    def claim(
        self,
        worker_id: str,
        *,
        job_types: Sequence[JobType] | None = None,
        visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    ) -> Job | None:
        """`PENDING`のJobを1件、他のworkerと排他して取得し`RUNNING`へ遷移させる(M3-2)。

        取得前に可視性タイムアウトを過ぎた`RUNNING`のJobを回収する。取得できるJobが
        なければ`None`を返す。
        """
        ...

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    ) -> Job:
        """claim済みJobのリース(可視性タイムアウトの期限)を延長する。"""
        ...

    def complete(
        self, job_id: str, worker_id: str, result: dict[str, Any] | None = None
    ) -> Job:
        """claim済みJobを正常終了として報告し`DONE`へ遷移させる。"""
        ...

    def fail(
        self, job_id: str, worker_id: str, error: str, *, retry: bool = True
    ) -> Job:
        """claim済みJobの失敗を報告する。リトライ上限未満なら`PENDING`へ戻し、
        上限到達または`retry=False`なら`FAILED`へ遷移させデッドレターとして確定する。"""
        ...

    def wait_for_human(
        self, job_id: str, worker_id: str, result: dict[str, Any] | None = None
    ) -> Job:
        """claim済みJobが人間の確認を必要とすると報告し`WAITING_HUMAN`へ遷移させる(M4-3)。

        `complete`と対になるメソッドで、`RUNNING → WAITING_HUMAN`へ遷移させたうえで
        リース情報をクリアする。
        """
        ...

    def list_dead_letters(self) -> list[Job]:
        """デッドレター化したJobを一覧する。"""
        ...
```

SQLite実装: `src/gitlab_ai_platform/job/sqlite.py`の`SqliteJobRepository`。
`SqliteJobRepository(db_path: Path | str = ":memory:")`でDBファイルパス(またはインメモリ)を
指定して構築する。State Store(`SqliteStateStore`)と同じく`threading.RLock`で全メソッドの
本体を直列化しており、`ReviewWorkerPool`(M2-1)のような複数ワーカースレッドから同一インスタンス
を安全に共有できる。`heartbeat`/`complete`/`fail`/`wait_for_human`は、要求元の`worker_id`が
対象Jobの現在のリース所有者と一致しない場合`LeaseLostError`を送出する(可視性タイムアウトで
別workerに再取得された後の遅延報告を検知するため、[ADR-0017](../adr/0017-job-queue.md))。

### review Jobのpayload/result構造

実装場所: `src/gitlab_ai_platform/review/job.py`。Job抽象は`payload`/`result`をdictとして
扱うため、`review`種別固有のキー構成はこのモジュールが定義する。

```python
REVIEW_JOB_TYPE: JobType  # = JobType.REVIEW


def build_review_job_payload(
    project: str, mr_iid: int, *, sha: str | None = None
) -> dict[str, Any]:
    """review Jobの`payload`を組み立てる。`sha`省略時はキー自体を含めない。"""


def review_job_payload_to_args(payload: dict[str, Any]) -> tuple[str, int, str | None]:
    """review Jobの`payload`から`(project, mr_iid, sha)`を取り出す。"""


def build_review_job_result(
    project: str, mr_iid: int, sha: str, result_path: str
) -> dict[str, Any]:
    """`execute_review`の実行結果からreview Jobの`result`を組み立てる。"""
```

### 既存レビュー処理のJob経由への再構成

実装場所: `src/gitlab_ai_platform/cli/single_run.py`の`execute_review_job`。

```python
def execute_review_job(
    job_repo: JobRepository,
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
    project: str,
    mr_iid: int,
    *,
    sha: str | None = None,
    timeout_seconds: int | None = None,
    allowed_tools: Sequence[str] = (),
    disallowed_tools: Sequence[str] = (),
    permission_mode: str | None = None,
) -> SingleRunResult:
    """`execute_review`を`review`種別のJobとしてラップして実行する。"""
```

`execute_review`本体(GitLab Adapter→Workspace Manager→Claude Code Runner→Review→
State Storeの結線)は変更しない。`execute_review_job`は`job_repo.enqueue`で`PENDING`の
Jobを起票し、`RUNNING`へ更新してから`execute_review`を呼び出す。成功時はJobを`DONE`へ
(`result`にproject/mr_iid/sha/結果保存先パスを記録)、`execute_review`が送出する例外
発生時はJobを`FAILED`へ更新してから、元の例外をそのまま再送出する(呼び出し側=
`cli/watch.py`の`build_on_detected`・CLIのエラーハンドリングは変えない)。

呼び出し元は2箇所:

- `cli/single_run.py`の`run_single_review`(単発実行の合成ルート)
- `cli/watch.py`の`build_on_detected`(常駐モードの検出時コールバック。Job Repositoryの
  具象実装の組み立て・`close`は`run_watch`が担う)

M3-1時点では「起票してすぐに処理する」単一プロセス・逐次実行のモデル。この経路は
M3-3([#93](https://github.com/AtsushiNi/gitlab-ai-platform/issues/93))でも**変更していない**
(`review`/`watch`サブコマンドは引き続きこの経路を使う)。

### Runner Dispatcherによる実配線(M3-3)

実装場所: `src/gitlab_ai_platform/cli/dispatcher.py`。詳細は
[ADR-0022](../adr/0022-runner-process-separation.md)、CLIオプション一覧は
[specs/cli.md](cli.md)の`worker`サブコマンドの節を参照。

`execute_review_job`とは別の経路として、`JobRepository.claim`でJobを取り出し続ける常駐/単発
実行のループ(`RunnerDispatcher`)を追加した。`worker`サブコマンド(`cli/main.py`)から起動する。

```python
JobHandler = Callable[[Job], dict[str, Any] | None]


def build_job_handlers(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
) -> dict[JobType, JobHandler]:
    """JobType → JobHandlerのディスパッチテーブルを組み立てる。M4-3時点でreview/issue-analysisが実装済み。"""


class RunnerDispatcher:
    """claimでJobを取り出し、対応するJobHandlerで処理するループ本体。"""

    def __init__(
        self,
        job_repo: JobRepository,
        handlers: Mapping[JobType, JobHandler],
        *,
        worker_id: str,
        # 省略時はhandlersに登録済みの種別のみをclaim対象にする
        job_types: Sequence[JobType] | None = None,
        poll_interval_seconds: float = 5.0,
        heartbeat_interval_seconds: float = 120.0,
        visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    ) -> None: ...

    def run_once(self) -> bool:
        """1件Jobをclaimして処理する。claimできるJobが無ければFalseを返す。"""

    def run_forever(self, stop_event: threading.Event) -> None:
        """stop_eventがセットされるまでclaim→処理を繰り返す。"""
```

- `review`種別の`JobHandler`(`build_review_handler`)は`execute_review`本体(GitLab Adapter→
  Workspace Manager→Claude Code Runner→Review→State Storeの結線、変更しない)をそのまま呼び出す。
  `issue-analysis`種別の`JobHandler`(`build_issue_analysis_handler`、M4-3)は
  [specs/issue-analysis.md](issue-analysis.md)を参照
- 未指定時の`job_types`は`handlers`に登録済みの種別のみ(M4-3時点では`review`/`issue-analysis`)。
  未実装の種別を誤ってclaimしてデッドレター化させないための既定挙動
- Jobの処理中は専用のdaemonスレッドが`heartbeat_interval_seconds`ごとに`heartbeat`を呼び、
  可視性タイムアウトのリースを延長する
- `handler`が正常終了 → `complete`。`WaitingForHumanError`(人間の確認が必要、M4-3、
  [ADR-0026](../adr/0026-job-waiting-human-transition.md)の契約)→ `wait_for_human`で
  `WAITING_HUMAN`へ遷移。`NotImplementedError`(未対応のJobType、
  [ADR-0016](../adr/0016-job-abstraction.md)の契約)→ `fail(..., retry=False)`で即デッドレター化。
  それ以外の例外 → `fail(..., retry=True)`でリトライ判定を`JobRepository`(`attempts`/
  `max_attempts`)に委ねる。1件のJobの失敗は他のJobの処理を止めない
- 排他は`claim`のアトミックなUPDATE文([ADR-0017](../adr/0017-job-queue.md))に委ね、`watch`の
  `ProcessLock`のような多重起動防止は行わない(同一`job_db_path`に対する複数`worker`プロセス
  /複数ホストの同時稼働を前提とする設計そのもののため、[ADR-0022](../adr/0022-runner-process-separation.md))
- 合成ルート`run_dispatcher(config, ...)`は`run_single_review`/`run_watch`と同じ流儀で
  `config`から具象実装(`GitLabRestAdapter`/`build_workspace_manager`/
  `SubprocessClaudeCodeRunner`/`SqliteStateStore`/`SqliteJobRepository`)を組み立てる

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/job/protocol.py`(`Job`/`JobType`/`JobStatus`)。

| 型 | フィールド | 補足 |
|---|---|---|
| `JobType` (Enum) | `REVIEW` / `ISSUE_ANALYSIS` / `DESIGN` / `IMPLEMENT` | タスク種別。M4-3時点で実際にRunnerが処理できるのは`REVIEW`/`ISSUE_ANALYSIS`。`DESIGN`/`IMPLEMENT`はM4での実装を見越した予約値 |
| `JobStatus` (Enum) | `PENDING` / `RUNNING` / `WAITING_HUMAN` / `DONE` / `FAILED` | Jobの進行状態(状態機械)。M3-2でもこの5値のまま変更していない([ADR-0017](../adr/0017-job-queue.md)) |
| `Job` (frozen dataclass) | `id: str`, `job_type: JobType`, `status: JobStatus`, `payload: dict[str, Any]`, `result: dict[str, Any] \| None`, `error: str \| None`, `created_at: datetime`, `updated_at: datetime`, `attempts: int = 0`, `max_attempts: int = 3`, `lease_owner: str \| None = None`, `lease_expires_at: datetime \| None = None`, `dead_letter_at: datetime \| None = None` | `id`はUUID(SQLite実装が`uuid.uuid4()`で生成)。`attempts`以降はM3-2で追加(末尾にデフォルト値付き) |

許可される状態遷移([ADR-0016](../adr/0016-job-abstraction.md)、`update_status`が検証する):

```text
PENDING → RUNNING
RUNNING → DONE
RUNNING → FAILED
RUNNING → WAITING_HUMAN
WAITING_HUMAN → RUNNING   (人間の回答を受けて再開)
WAITING_HUMAN → FAILED    (タイムアウト・却下)
```

`DONE`/`FAILED`は終端状態で、そこからの遷移は無い。上記以外への`update_status`
(同一状態への遷移も含む)は`InvalidJobTransitionError`を送出する。この遷移表は
`update_status`が検証する「アプリケーションが明示的に報告する状態変化」のみを対象とし、
`fail`のリトライパス(`RUNNING → PENDING`、可視性タイムアウトの回収時も含む)はキュー内部
専用の遷移として`update_status`を経由せず実装している([ADR-0017](../adr/0017-job-queue.md))。

SQLiteスキーマ(`jobs`テーブル、`status`/`job_type`/`lease_expires_at`にインデックス):

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,        -- JSON
    result TEXT,                  -- JSON
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_owner TEXT,             -- claim中のworker_id(M3-2)
    lease_token TEXT,             -- claim呼び出しごとの一意token。Jobには公開しない内部用
    lease_expires_at TEXT,        -- 可視性タイムアウトの期限(ISO 8601)
    dead_letter_at TEXT           -- デッドレター化した日時(ISO 8601、非NULLならデッドレター)
);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_job_type ON jobs(job_type);
CREATE INDEX idx_jobs_lease_expires_at ON jobs(lease_expires_at);
```

`payload`/`result`はJSON文字列としてTEXTカラムに保存し、アプリケーション側で
`json.dumps`/`json.loads`する([ADR-0001](../adr/0001-repository-structure.md)の依存最小
方針によりORMは使わない)。`created_at`/`updated_at`/`lease_expires_at`/`dead_letter_at`は
State Storeの`reviewed_at`と同じくISO 8601文字列で保存し、呼び出し側には`datetime`として
返す。M3-1時点のDBファイル(これらのカラムを持たない)は、`SqliteJobRepository`の起動時に
`ALTER TABLE`で不足しているカラムのみ後方互換で追加する([ADR-0017](../adr/0017-job-queue.md)
「スキーマ変更は`ALTER TABLE`による後方互換マイグレーションとする」)。

review Jobの`payload`/`result`(`review/job.py`):

| フィールド | 型 | 補足 |
|---|---|---|
| `payload.project` | `str` | 対象プロジェクトパス |
| `payload.mr_iid` | `int` | 対象MRのIID |
| `payload.sha` | `str`(省略可) | 起票対象のcommit。省略時は`execute_review`がMR取得時点の最新shaを使う |
| `result.project` / `result.mr_iid` / `result.sha` | `str` / `int` / `str` | 実行結果(`SingleRunResult`)から転記 |
| `result.result_path` | `str` | レビュー結果(JSON/Markdown)の保存先ディレクトリ(`docs/specs/review-output.md`) |

issue-analysis Jobの`payload`/`result`(payloadは`poller/issue_poller.py`、resultは
`issue_analysis/job.py`。詳細は[specs/issue-analysis.md](issue-analysis.md)、M4-3):

| フィールド | 型 | 補足 |
|---|---|---|
| `payload.project` | `str` | 対象プロジェクトパス |
| `payload.issue_iid` | `int` | 対象IssueのIID |
| `result.requirements` / `result.acceptance_criteria` / `result.assumptions` | `string[]` | Claude Codeの要求分析結果 |
| `result.assumed_uncertainties` | `object[]` | `ASSUME`判定された不明点(M4-9で使用予定) |
| `result.questions` | `object[]` | `ASK`判定された不明点。`WAITING_HUMAN`のときのみ非空 |

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/job/errors.py`。

- `JobError(Exception)` — Job Repository経由の操作が失敗したことを表す基底例外。
  呼び出し側はまずこの型でcatchすればJob Repository起因の失敗を一括して扱える
- `InvalidJobTransitionError(JobError)` — `update_status`が許可されていない状態遷移
  (モジュールdocstring・上記の遷移表参照)で呼ばれたことを表す
- `JobNotFoundError(JobError)` — `update_status`が存在しない`job_id`に対して呼ばれたことを
  表す。呼び出し側は先に`enqueue`を呼ぶべきだったことを意味し、通常は呼び出し側のバグとして
  扱う(リトライで解決しない)
- `LeaseLostError(JobError)` — `heartbeat`/`complete`/`fail`/`wait_for_human`が、要求元の
  `worker_id`と対象Jobの現在のリース所有者が一致しない状態で呼ばれたことを表す(M3-2、
  [ADR-0017](../adr/0017-job-queue.md))。可視性タイムアウトで別workerに再取得された後、
  元のworkerが遅れて完了/失敗/`WAITING_HUMAN`遷移を報告してきた場合などに送出する

`execute_review_job`は`execute_review`が送出する例外(`GitLabAdapterError`/`WorkspaceError`/
`RunnerError`/`ReviewError`/`StateStoreError`等)を捕まえてJobを`FAILED`へ更新したうえで、
元の例外をそのまま再送出する(Job更新自体の失敗は`JobError`として別途伝播しうる。State Store
の`_ticket_running`と異なり、Job更新の失敗を握りつぶして元の例外を優先する処理は現時点では
実装していない)。

## テスト方針

実装場所: `tests/gitlab_ai_platform/job/`・`tests/gitlab_ai_platform/review/test_job.py`
(`src/`をミラー、[ADR-0001](../adr/0001-repository-structure.md))。

- `job/test_protocol.py`: `JobRepository`の公開メソッド集合が`enqueue`/`get`/
  `update_status`/`list_by_status`/`close`/`claim`/`heartbeat`/`complete`/`fail`/
  `list_dead_letters`/`wait_for_human`と完全一致することを検証する(将来メソッドが意図せず
  増減した場合にこのテストで落ちる)。`Job`の`frozen=True`、`JobType`/`JobStatus`の値、
  M3-2で追加したフィールドの既定値(`attempts=0`等)、Protocolを満たすダミー実装
  (新メソッドのスタブ実装を含む)への`isinstance`も検証する
- `job/test_errors.py`: `InvalidJobTransitionError`/`JobNotFoundError`/`LeaseLostError`が
  `JobError`のサブクラスであることを検証する
- `job/test_sqlite.py`: `SqliteJobRepository`をインメモリDB(`:memory:`)で実行し、実DB・
  実サービスへは繋がない(CLAUDE.mdのテスト方針)。以下を検証する:
  - `enqueue`→`get`の往復、`PENDING`状態での起票、`max_attempts`のカスタム指定
  - 許可される状態遷移(`PENDING → RUNNING → DONE/FAILED/WAITING_HUMAN`、
    `WAITING_HUMAN → RUNNING/FAILED`)が`update_status`で成功すること
  - 許可されない遷移(終端状態からの遷移、逆行、同一状態への遷移含む)が
    `InvalidJobTransitionError`になること(全パターンを`pytest.mark.parametrize`で網羅)
  - 存在しない`job_id`への`update_status`が`JobNotFoundError`になること
  - `result`/`error`を省略した更新が既存値を維持すること(COALESCE)
  - `list_by_status`が指定状態のJobのみを返すこと
  - 複数ワーカースレッドからの同時`enqueue`/`update_status`が例外を送出しないこと
    (`store/test_sqlite.py`と同じ並行アクセスの回帰テスト)
  - `claim`: `PENDING`のJobを`RUNNING`へ遷移させて返すこと、既にclaim済みのJobは再取得
    されないこと、`job_types`によるフィルタ、対象が無ければ`None`を返すこと、複数ワーカー
    スレッドからの同時`claim`で同一Jobが二重取得されないこと(排他取得の回帰テスト)
  - `heartbeat`: リース期限を延長すること、`worker_id`不一致/未claimのJobで
    `LeaseLostError`になること
  - `complete`: `DONE`へ遷移しリース情報をクリアすること、`worker_id`不一致で
    `LeaseLostError`になること
  - `wait_for_human`(M4-3、ADR-0026): `WAITING_HUMAN`へ遷移しリース情報をクリアすること、
    `worker_id`不一致で`LeaseLostError`になること、未claimのJobで`LeaseLostError`になること、
    `WAITING_HUMAN`から`update_status`で`RUNNING`へ再開できること(既存の許可遷移)
  - `fail`: リトライ余地があれば`PENDING`へ戻すこと、`retry=False`または上限到達時に
    `FAILED`+デッドレター化すること、`worker_id`不一致で`LeaseLostError`になること
  - 可視性タイムアウト: 期限切れのリースを次の`claim`が回収し、リトライ余地があれば
    再取得可能に戻すこと、上限到達時はデッドレター化すること
  - `list_dead_letters`が空/デッドレター化したJobのみを返すこと
  - M3-1時点のスキーマ(新カラムを持たないDBファイル)を後方互換でマイグレーションできること
- `review/test_job.py`: `build_review_job_payload`/`review_job_payload_to_args`/
  `build_review_job_result`の組み立て・分解が正しいことを検証する
- `cli/test_single_run.py`: `execute_review_job`が成功時にJobを`DONE`(`result`に
  project/mr_iid/sha/result_pathを含む)へ、`execute_review`が例外を送出した場合にJobを
  `FAILED`(`error`にメッセージを含む)へ更新すること、`sha`が`payload`経由で
  `execute_review`へ正しく伝わることを検証する
- `cli/test_watch.py`: `build_on_detected`/`run_watch_loop`が検出したMRをreview Jobとして
  起票し、`execute_review`の成否に応じて`DONE`/`FAILED`へ更新することを検証する
  (既存のState Store側のアサーションに加え、Job側のアサーションを追加)
- `cli/test_dispatcher.py`(M3-3、M4-3): `build_review_handler`/`build_issue_analysis_handler`/
  `build_job_handlers`を手書きフェイクで検証する(`execute_review`/`ClaudeCodeRunner.run_prompt`を
  実際に呼び出し、`result`の組み立てが正しいこと。`build_issue_analysis_handler`は
  [specs/issue-analysis.md](issue-analysis.md)のテスト方針も参照)。`RunnerDispatcher`は
  `claim`/`heartbeat`/`complete`/`fail`/`wait_for_human`のみを満たす手書きフェイク
  `JobRepository`で制御フローを検証する: `run_once`がJobの有無に応じて`True`/`False`を
  返すこと、handler成功時に`complete`、`WaitingForHumanError`送出時に`wait_for_human`
  (ADR-0026)、例外送出時に`fail(..., retry=True)`、未対応JobType(handlerが無い)時に
  `fail(..., retry=False)`を呼ぶこと、`job_types`省略時は`handlers`に登録済みの種別のみを
  claim対象にすること、Job処理中に`heartbeat_interval_seconds`ごとに`heartbeat`が呼ばれること、
  `heartbeat`が`LeaseLostError`を送出してもhandler本体は中断されず完了すること、`run_forever`が
  `stop_event`まで空振り時のみ`poll_interval_seconds`待ってポーリングし続けること。
  `run_dispatcher`(合成ルート)は実サービスに繋がない範囲(`stop_event`を起動前にセットする、
  Jobが無い状態で`run_once=True`を渡す)で、具象実装の組み立てが例外を出さないことを検証する

## 関連ドキュメント

- [architecture.md](../architecture.md) 「MVP → AI Platformへの成長パス」のJob抽象・状態機械の行
- [ADR-0016: Job抽象・状態機械のインターフェース設計](../adr/0016-job-abstraction.md)
- [ADR-0017: Job Queue(取得の排他・可視性タイムアウト・リトライ・デッドレター)の設計](../adr/0017-job-queue.md)
- [ADR-0022: Runner のプロセス分離(Runner Dispatcher)の設計](../adr/0022-runner-process-separation.md)
- [ADR-0015: 並列レビュー実行の設計](../adr/0015-parallel-review-execution.md) — SQLite実装の
  ロック方針(`threading.RLock`)の前例、複数プロセス/ホストからの同時アクセスの検討経緯
- [ADR-0003: State Store のインターフェースとスキーマ設計](../adr/0003-state-store-interface.md) —
  State StoreとJobを別コンポーネントとして併存させる設計判断
- [specs/state-store.md](state-store.md) — State Store(併存する二重レビュー防止の仕組み)の仕様
- [specs/cli.md](cli.md) — `execute_review_job`の呼び出し元(単発実行・watchモード)、
  `RunnerDispatcher`の呼び出し元(`worker`サブコマンド)、`WAITING_HUMAN → RUNNING → DONE`を
  非リース方式(`update_status`)で辿る`respond`サブコマンド(M4-5)の仕様
- [ADR-0028: `WAITING_HUMAN`後の回答取り込み・Job完了の設計](../adr/0028-waiting-human-answer-integration.md) —
  `wait_for_human`で`WAITING_HUMAN`にしたJobを、非リース方式の`update_status`で
  `RUNNING`→`DONE`へ再開させる`respond`サブコマンド(M4-5)の設計判断
- ソースコード: `src/gitlab_ai_platform/job/`(`protocol.py` / `sqlite.py` / `errors.py` /
  `__init__.py`)、`src/gitlab_ai_platform/review/job.py`、
  `src/gitlab_ai_platform/cli/dispatcher.py`(M3-3)、`src/gitlab_ai_platform/cli/respond.py`(M4-5)
