# ADR-0016: Job抽象・状態機械のインターフェース設計

- Issue: [#91](https://github.com/AtsushiNi/gitlab-ai-platform/issues/91) (M3-1)
- 状態: 決定

## 背景・制約

- `docs/architecture.md`の「MVP → AI Platformへの成長パス」により、Job層はPoller(検出)と
  Runner(実行)の間に挿入される新規レイヤーであり、`PENDING` `RUNNING` `WAITING_HUMAN` `DONE`
  `FAILED`の状態機械を持つ。既存のレビュー処理をこの型に再構成し、M4のIssue駆動開発の各フェーズ
  (要求分析/設計/実装)も同じ型で表現することが要件になっている
- M3-1は本来「既存レビュー処理のJobとしての再構成」まで含む実装タスクだが、`references/タスク整理.md`
  のクリティカルパス上、M3-2(Job Queue)・M3-3(Runner分離)・M3-6(Webhook対応)・M3-7(HTTP API層)が
  すべてM3-1の成果物に依存しており、実装完了を待つと並行作業ができない。そのため、まず
  **インターフェース(型・Protocol)だけを本ADRで先に決定し**、複数Issueが同時に着手できる状態を作る
- ADR-0003(State Store)は「`status`は`PENDING`/`RUNNING`/`DONE`/`FAILED`の4値とし、Job状態機械とは
  別物。M3-1でJob抽象を導入する際にState Storeとの関係を再整理する」と明記しており、本ADRはその
  再整理を兼ねる
- ADR-0001の方針により外部依存は最小限(`requests`/`pytest`のみ)。ORM等は導入しない

## 決定

### JobとState Storeは別のコンポーネントとして併存させる

State Store(ADR-0003)は「`(project, mr_iid, commit_sha)`単位で二重レビューを防ぐ」ことだけに
責務を絞ったレビュー固有の台帳であり、`docs/architecture.md`の成長パス表でも「バックエンドが
PostgreSQLに移行(M3-5)。APIは不変」と、Job層の新設とは無関係に扱われている。この設計をそのまま
踏襲し、**Jobはレビューに限らないタスク種別を横断的に管理する新規のリポジトリとして追加する**
(State Storeを置き換えない、統合しない)。`review`種別のJobが実行される際、レビュー結果の二重実行
防止は引き続きState Storeが担い、Jobはその実行1回分の「キューに積まれた作業」というライフサイクルの
管理に専念する。

### `JobType`は4値を今のうちに列挙し、未実装の種別は明示的に拒否する

```python
class JobType(str, Enum):
    REVIEW = "review"
    ISSUE_ANALYSIS = "issue-analysis"  # M4で実装
    DESIGN = "design"  # M4で実装
    IMPLEMENT = "implement"  # M4で実装
```

M3-1時点で実際にRunnerが処理できるのは`REVIEW`のみ。`ISSUE_ANALYSIS`/`DESIGN`/`IMPLEMENT`は
値としては予約するが、Job実行側(Runner Dispatcher)は未実装の種別を受け取った場合
`NotImplementedError`を送出する。値を先に確定させる理由は、Jobレコードは永続化されるため、
後から列挙値を追加するとDBに保存済みの過去レコードとの互換性を考える必要が出るため
(GitLab AdapterのProtocol確定と同じ「後から差し替えられるように先に型を決める」考え方)。

### `JobStatus`の遷移は一方向を基本とし、`WAITING_HUMAN`からの復帰のみ例外とする

```python
class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    DONE = "done"
    FAILED = "failed"
```

許可される遷移:

```text
PENDING → RUNNING
RUNNING → DONE
RUNNING → FAILED
RUNNING → WAITING_HUMAN
WAITING_HUMAN → RUNNING   (人間の回答を受けて再開)
WAITING_HUMAN → FAILED    (タイムアウト・却下)
```

`DONE`/`FAILED`は終端状態で、そこからの遷移は存在しない。遷移の妥当性チェックはJobRepository
実装側の責務とし、不正な遷移は`InvalidJobTransitionError`を送出する契約とする(State Storeの
`DuplicateReviewError`と同様、契約違反を型で表現する方針を踏襲)。

### `Job`はペイロード・結果を種別非依存の`dict`として持つ

```python
@dataclass
class Job:
    id: str
    job_type: JobType
    status: JobStatus
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime
```

`payload`/`result`をJSON互換の`dict`にとどめ、`review`固有のフィールド(MR情報など)や
将来の`design`/`implement`固有のフィールドをJob抽象そのものに持たせない。型ごとの構造は
呼び出し側(Review pipelineなど)が`payload`/`result`の中身として定義する。これはGitLab
Adapterが個別のGitLabリソース構造をラップして`types.py`で公開する方針とは逆に、
Job層ではあえて「箱」のまま扱う判断。理由は、Job層がM4以降も追加されるJobType全てを
知る必要がないようにするため(GitLab Adapterは実装が1つ(GitLab)しかないので構造を型で
表現できたが、Jobは種別が今後増え続けるため、種別ごとの構造をJob抽象に持ち込むと
JobType追加のたびにJob抽象自体の変更が必要になってしまう)。

### `JobRepository`はStateStoreと同じ形の`typing.Protocol`とする

`src/gitlab_ai_platform/job/protocol.py`に以下のProtocolを置く(ADR-0002/0003と同じ方針で
`abc.ABC`ではなく構造的部分型を採用し、`@runtime_checkable`を付ける):

```python
class JobRepository(Protocol):
    def enqueue(self, job_type: JobType, payload: dict[str, Any]) -> Job: ...
    def get(self, job_id: str) -> Job | None: ...
    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Job: ...
    def list_by_status(self, status: JobStatus) -> list[Job]: ...
    def close(self) -> None: ...
```

**この5メソッドだけを本ADR(M3-1)のスコープとし、キューとしての実務(取得の排他・可視性
タイムアウト・リトライ・デッドレター)はM3-2のスコープとする。** M3-2は`JobRepository`を
実装したまま、内部実装として排他制御やリトライを追加する(Protocolのメソッド追加は
「取得の排他」に必要な`claim`のような新メソッドが要る場合のみ、M3-2側のADRで検討する)。
M3-1では単一プロセス・逐次実行を前提にした最小のSQLite実装
(`src/gitlab_ai_platform/job/sqlite.py`)を用意し、既存レビュー処理をこの`JobRepository`
経由の`review` Jobとして再構成する。M3-2はこのSQLite実装を土台に、複数Runnerからの
同時`claim`に対する排他や可視性タイムアウトを追加する形で拡張する(作り直さない)。

### スキーマは`job_type`/`status`双方にインデックスを張ったテーブルとする

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
```

ADR-0003の`review_records`とは別テーブルとする(前述の「State Storeと併存」の決定通り)。
`payload`/`result`はJSON文字列としてTEXTカラムに保存し、アプリケーション側で
`json.dumps`/`json.loads`する(ADR-0001の依存最小方針によりORMは使わない)。

## 却下した選択肢

- **State StoreをJob Repositoryとして拡張・統合する**: State Storeの主キーは
  `(project, mr_iid, commit_sha)`というレビュー固有の複合キーであり、`issue-analysis`/`design`/
  `implement`のような非レビューJobには意味を持たない。無理に統合すると、レビュー以外のJob種別が
  増えるたびにState Storeのスキーマを歪める必要が出るため見送った。
- **`JobType`を`review`のみ最小実装し、他の値はM4着手時に追加する**: 値の後追い自体は
  技術的には可能だが、DBに保存済みのJobレコード(`job_type`カラム)との互換性検証や、
  Job Repository実装のマイグレーションが後から必要になる。M3-1時点で列挙だけ済ませておく方が
  「後から作り直さずにJob層を差し込める」というプロジェクト全体の基本方針
  (`references/タスク整理.md`冒頭)に沿う。
- **`Job`のpayload/resultを種別ごとのdataclassにする(`ReviewJobPayload`など)**: 型安全性は
  上がるが、Job抽象自体がJobType追加のたびに変更対象になってしまい、Job層を挟む目的
  (Poller/Runner側のロジックをほぼそのまま残す)に反する。種別ごとの構造化は呼び出し側
  (Review pipelineなど)の責務とし、Job抽象は関与しない。
- **`JobRepository`に`claim`(排他取得)を今から含める**: 排他制御の具体的な設計
  (可視性タイムアウトの単位、リースの再取得方法など)はM3-2のスコープであり、M3-1時点で
  先取りして決めると後から変更しづらい制約を残すことになる。M3-1は単一プロセス実行を
  前提にした最小Protocolにとどめ、`claim`が必要かどうかはM3-2のADRで判断する。

## 影響

- M3-2(Job Queue)は本ADRの`JobRepository`を実装・拡張する形で着手できる。トラック2として
  M3-1のインターフェース確定後すぐに並行着手可能になる([#92](https://github.com/AtsushiNi/gitlab-ai-platform/issues/92))
- M3-6(Webhook対応)はJob起票のインターフェース(`enqueue`)にのみ依存するため、同じくトラック2として
  並行着手できる([#96](https://github.com/AtsushiNi/gitlab-ai-platform/issues/96))
- M3-3(Runner分離)・M3-7(HTTP API層)はM3-2完了後のトラック3
- M3-8(トークンスコープ設計)・M3-4(Docker実行環境)・M3-5(PostgreSQL対応)は本ADRの決定に
  依存しないトラック1として、本Issue(M3-1)と並行に着手できる
- M4のJobType(`issue-analysis`/`design`/`implement`)は、本ADRで型を予約済みのため、
  Runner Dispatcher側の実装追加のみで着手できる見込み
- 詳細仕様は実装時に`docs/specs/job-model.md`として文書化する(D-6のフォーマットに従う)
