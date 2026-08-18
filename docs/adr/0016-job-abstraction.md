# ADR-0016: Job抽象・状態機械のインターフェース設計

- Issue: [#91](https://github.com/AtsushiNi/gitlab-ai-platform/issues/91) (M3-1)
- 状態: 決定

## 背景・制約

- Job層はPoller(検出)とRunner(実行)の間に挿入される新規レイヤーであり、`PENDING` `RUNNING` `WAITING_HUMAN` `DONE` `FAILED`の状態機械を持つ。既存のレビュー処理をこの型に再構成し、M4のIssue駆動開発の各フェーズも同じ型で表現する
- State Store(レビュー固有の二重実行防止台帳)とJob層の関係を整理する必要がある
- 外部依存は最小限(ORM等は導入しない)

## 決定

### JobとState Storeは別のコンポーネントとして併存させる

State Storeは「`(project, mr_iid, commit_sha)`単位で二重レビューを防ぐ」ことだけに責務を絞ったレビュー固有の台帳。この設計を踏襲し、**Jobはレビューに限らないタスク種別を横断的に管理する新規のリポジトリとして追加する**(State Storeを置き換えない、統合しない)。

### `JobType`は4値を今のうちに列挙し、未実装の種別は明示的に拒否する

```python
class JobType(str, Enum):
    REVIEW = "review"
    ISSUE_ANALYSIS = "issue-analysis"  # M4で実装
    DESIGN = "design"  # M4で実装
    IMPLEMENT = "implement"  # M4で実装
```

M3-1時点で実際にRunnerが処理できるのは`REVIEW`のみ。未実装の種別を受け取った場合はRunner Dispatcher側が`NotImplementedError`を送出する。値を先に確定させる理由は、Jobレコードが永続化されるため、後から列挙値を追加すると保存済みレコードとの互換性を考える必要が出るため。

### `JobStatus`の遷移は一方向を基本とし、`WAITING_HUMAN`からの復帰のみ例外とする

```text
PENDING → RUNNING
RUNNING → DONE / FAILED / WAITING_HUMAN
WAITING_HUMAN → RUNNING(人間の回答を受けて再開) / FAILED(タイムアウト・却下)
```

`DONE`/`FAILED`は終端状態。不正な遷移は`InvalidJobTransitionError`を送出する契約とする。

### `Job`はペイロード・結果を種別非依存の`dict`として持つ

`payload`/`result`をJSON互換の`dict`にとどめ、種別固有のフィールドをJob抽象そのものに持たせない。Job層がM4以降も追加されるJobType全てを知る必要がないようにするための判断。

### `JobRepository`はState Storeと同じ形の`typing.Protocol`とする

```python
class JobRepository(Protocol):
    def enqueue(self, job_type: JobType, payload: dict[str, Any]) -> Job: ...
    def get(self, job_id: str) -> Job | None: ...
    def update_status(self, job_id: str, status: JobStatus, result=None, error=None) -> Job: ...
    def list_by_status(self, status: JobStatus) -> list[Job]: ...
    def close(self) -> None: ...
```

この5メソッドのみを本ADR(M3-1)のスコープとし、キューとしての実務(取得の排他・可視性タイムアウト・リトライ・デッドレター)はM3-2([ADR-0017](0017-job-queue.md))のスコープとする。M3-1では単一プロセス・逐次実行を前提にした最小のSQLite実装を用意し、既存レビュー処理をこの`JobRepository`経由の`review` Jobとして再構成する。

### スキーマは`job_type`/`status`双方にインデックスを張ったテーブルとする

State Storeのテーブルとは別テーブルとする。`payload`/`result`はJSON文字列としてTEXTカラムに保存する(ORMは使わない)。

## 却下した選択肢

- **State StoreをJob Repositoryとして拡張・統合する**: State Storeの主キーはレビュー固有の複合キーであり、他のJob種別には意味を持たない
- **`JobType`を`review`のみ最小実装し、他の値はM4着手時に追加する**: DBに保存済みレコードとの互換性検証やマイグレーションが後から必要になる
- **`Job`のpayload/resultを種別ごとのdataclassにする**: Job抽象自体がJobType追加のたびに変更対象になってしまう
- **`JobRepository`に`claim`(排他取得)を今から含める**: 排他制御の具体的な設計はM3-2のスコープであり、先取りして決めると後から変更しづらい制約を残す

## 影響

- M3-2(Job Queue)はトラック2としてM3-1完了後すぐに並行着手可能
- M4のJobType(`issue-analysis`/`design`/`implement`)は、本ADRで型を予約済みのため実装追加のみで着手できる
- 詳細仕様は`docs/specs/job-model.md`に文書化する
