# Job Model

- 実装場所: `src/gitlab_ai_platform/job/`
- 対応Issue: [#91](https://github.com/AtsushiNi/gitlab-ai-platform/issues/91) (M3-1)
- 関連ADR: [ADR-0016](../adr/0016-job-abstraction.md)
- ステータス: 実装済み(Protocol定義 + SQLite実装 + 既存レビュー処理のJob化)

## 責務

タスク種別(`review`/`issue-analysis`/`design`/`implement`)を横断して、1件のタスク実行の
ライフサイクル(`PENDING → RUNNING → (DONE | FAILED | WAITING_HUMAN)`という状態機械)を
記録・照会する。実装(SQLite、M3-1。将来は複数Runnerからの排他取得を伴うJob Queue、M3-2)を
`typing.Protocol`で抽象化し、呼び出し側(CLI・将来のOrchestrator)を具象実装から切り離す。

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
- 非対象:
  - 取得の排他(`claim`)・可視性タイムアウト・リトライ・デッドレターはM3-2(Job Queue)の
    スコープ。本実装(`SqliteJobRepository`)は単一プロセス・逐次実行を前提にした最小実装
  - `review`以外のJobType(`issue-analysis`/`design`/`implement`)の実際の実行(Runner
    Dispatcher側の処理)はM4のスコープ。M3-1時点では値としての予約のみ
  - 二重レビュー防止そのもの(State Storeの責務のまま)。Jobは「実行1回分のライフサイクル」の
    管理に専念する

## 公開インターフェース

`JobRepository`を`@runtime_checkable`な`typing.Protocol`として定義する。
実装場所: `src/gitlab_ai_platform/job/protocol.py`(`JobType`/`JobStatus`/`Job`データクラスも
同ファイルに定義する。State Store等と異なり`types.py`を分けていない)。

```python
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable


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


@runtime_checkable
class JobRepository(Protocol):
    def enqueue(self, job_type: JobType, payload: dict[str, Any]) -> Job:
        """新しいJobを`PENDING`状態で作成する。"""
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
```

SQLite実装: `src/gitlab_ai_platform/job/sqlite.py`の`SqliteJobRepository`。
`SqliteJobRepository(db_path: Path | str = ":memory:")`でDBファイルパス(またはインメモリ)を
指定して構築する。State Store(`SqliteStateStore`)と同じく`threading.RLock`で全メソッドの
本体を直列化しており、`ReviewWorkerPool`(M2-1)のような複数ワーカースレッドから同一インスタンス
を安全に共有できる。

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

M3-1時点では「起票してすぐに処理する」単一プロセス・逐次実行のモデルであり、
Jobが`PENDING`のまま別プロセスに取り出されて処理される、という実際のキューイングは
M3-2(Job Queue)のスコープ。

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/job/protocol.py`(`Job`/`JobType`/`JobStatus`)。

| 型 | フィールド | 補足 |
|---|---|---|
| `JobType` (Enum) | `REVIEW` / `ISSUE_ANALYSIS` / `DESIGN` / `IMPLEMENT` | タスク種別。M3-1で実際にRunnerが処理できるのは`REVIEW`のみ。他はM4での実装を見越した予約値 |
| `JobStatus` (Enum) | `PENDING` / `RUNNING` / `WAITING_HUMAN` / `DONE` / `FAILED` | Jobの進行状態(状態機械) |
| `Job` (frozen dataclass) | `id: str`, `job_type: JobType`, `status: JobStatus`, `payload: dict[str, Any]`, `result: dict[str, Any] \| None`, `error: str \| None`, `created_at: datetime`, `updated_at: datetime` | `id`はUUID(SQLite実装が`uuid.uuid4()`で生成) |

許可される状態遷移([ADR-0016](../adr/0016-job-abstraction.md)):

```text
PENDING → RUNNING
RUNNING → DONE
RUNNING → FAILED
RUNNING → WAITING_HUMAN
WAITING_HUMAN → RUNNING   (人間の回答を受けて再開)
WAITING_HUMAN → FAILED    (タイムアウト・却下)
```

`DONE`/`FAILED`は終端状態で、そこからの遷移は無い。上記以外への`update_status`
(同一状態への遷移も含む)は`InvalidJobTransitionError`を送出する。

SQLiteスキーマ(`jobs`テーブル、`status`/`job_type`双方にインデックス):

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,   -- JSON
    result TEXT,             -- JSON
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_job_type ON jobs(job_type);
```

`payload`/`result`はJSON文字列としてTEXTカラムに保存し、アプリケーション側で
`json.dumps`/`json.loads`する([ADR-0001](../adr/0001-repository-structure.md)の依存最小
方針によりORMは使わない)。`created_at`/`updated_at`はState Storeの`reviewed_at`と同じく
ISO 8601文字列で保存し、呼び出し側には`datetime`として返す。

review Jobの`payload`/`result`(`review/job.py`):

| フィールド | 型 | 補足 |
|---|---|---|
| `payload.project` | `str` | 対象プロジェクトパス |
| `payload.mr_iid` | `int` | 対象MRのIID |
| `payload.sha` | `str`(省略可) | 起票対象のcommit。省略時は`execute_review`がMR取得時点の最新shaを使う |
| `result.project` / `result.mr_iid` / `result.sha` | `str` / `int` / `str` | 実行結果(`SingleRunResult`)から転記 |
| `result.result_path` | `str` | レビュー結果(JSON/Markdown)の保存先ディレクトリ(`docs/specs/review-output.md`) |

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/job/errors.py`。

- `JobError(Exception)` — Job Repository経由の操作が失敗したことを表す基底例外。
  呼び出し側はまずこの型でcatchすればJob Repository起因の失敗を一括して扱える
- `InvalidJobTransitionError(JobError)` — `update_status`が許可されていない状態遷移
  (モジュールdocstring・上記の遷移表参照)で呼ばれたことを表す
- `JobNotFoundError(JobError)` — `update_status`が存在しない`job_id`に対して呼ばれたことを
  表す。呼び出し側は先に`enqueue`を呼ぶべきだったことを意味し、通常は呼び出し側のバグとして
  扱う(リトライで解決しない)

`execute_review_job`は`execute_review`が送出する例外(`GitLabAdapterError`/`WorkspaceError`/
`RunnerError`/`ReviewError`/`StateStoreError`等)を捕まえてJobを`FAILED`へ更新したうえで、
元の例外をそのまま再送出する(Job更新自体の失敗は`JobError`として別途伝播しうる。State Store
の`_ticket_running`と異なり、Job更新の失敗を握りつぶして元の例外を優先する処理は現時点では
実装していない)。

## テスト方針

実装場所: `tests/gitlab_ai_platform/job/`・`tests/gitlab_ai_platform/review/test_job.py`
(`src/`をミラー、[ADR-0001](../adr/0001-repository-structure.md))。

- `job/test_protocol.py`: `JobRepository`の公開メソッド集合が`enqueue`/`get`/
  `update_status`/`list_by_status`/`close`と完全一致することを検証する(将来メソッドが
  意図せず増減した場合にこのテストで落ちる)。`Job`の`frozen=True`、`JobType`/`JobStatus`の
  値、Protocolを満たすダミー実装への`isinstance`も検証する
- `job/test_errors.py`: `InvalidJobTransitionError`/`JobNotFoundError`が`JobError`の
  サブクラスであることを検証する
- `job/test_sqlite.py`: `SqliteJobRepository`をインメモリDB(`:memory:`)で実行し、実DB・
  実サービスへは繋がない(CLAUDE.mdのテスト方針)。以下を検証する:
  - `enqueue`→`get`の往復、`PENDING`状態での起票
  - 許可される状態遷移(`PENDING → RUNNING → DONE/FAILED/WAITING_HUMAN`、
    `WAITING_HUMAN → RUNNING/FAILED`)が`update_status`で成功すること
  - 許可されない遷移(終端状態からの遷移、逆行、同一状態への遷移含む)が
    `InvalidJobTransitionError`になること(全パターンを`pytest.mark.parametrize`で網羅)
  - 存在しない`job_id`への`update_status`が`JobNotFoundError`になること
  - `result`/`error`を省略した更新が既存値を維持すること(COALESCE)
  - `list_by_status`が指定状態のJobのみを返すこと
  - 複数ワーカースレッドからの同時`enqueue`/`update_status`が例外を送出しないこと
    (`store/test_sqlite.py`と同じ並行アクセスの回帰テスト)
- `review/test_job.py`: `build_review_job_payload`/`review_job_payload_to_args`/
  `build_review_job_result`の組み立て・分解が正しいことを検証する
- `cli/test_single_run.py`: `execute_review_job`が成功時にJobを`DONE`(`result`に
  project/mr_iid/sha/result_pathを含む)へ、`execute_review`が例外を送出した場合にJobを
  `FAILED`(`error`にメッセージを含む)へ更新すること、`sha`が`payload`経由で
  `execute_review`へ正しく伝わることを検証する
- `cli/test_watch.py`: `build_on_detected`/`run_watch_loop`が検出したMRをreview Jobとして
  起票し、`execute_review`の成否に応じて`DONE`/`FAILED`へ更新することを検証する
  (既存のState Store側のアサーションに加え、Job側のアサーションを追加)

## 関連ドキュメント

- [architecture.md](../architecture.md) 「MVP → AI Platformへの成長パス」のJob抽象・状態機械の行
- [ADR-0016: Job抽象・状態機械のインターフェース設計](../adr/0016-job-abstraction.md)
- [ADR-0003: State Store のインターフェースとスキーマ設計](../adr/0003-state-store-interface.md) —
  State StoreとJobを別コンポーネントとして併存させる設計判断
- [specs/state-store.md](state-store.md) — State Store(併存する二重レビュー防止の仕組み)の仕様
- [specs/cli.md](cli.md) — `execute_review_job`の呼び出し元(単発実行・watchモード)の仕様
- ソースコード: `src/gitlab_ai_platform/job/`(`protocol.py` / `sqlite.py` / `errors.py` /
  `__init__.py`)、`src/gitlab_ai_platform/review/job.py`
