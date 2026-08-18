# ADR-0017: Job Queue(取得の排他・可視性タイムアウト・リトライ・デッドレター)の設計

- Issue: [#92](https://github.com/AtsushiNi/gitlab-ai-platform/issues/92) (M3-2)
- 状態: 決定

## 背景・制約

- [ADR-0016](0016-job-abstraction.md)は`JobRepository`の基本5メソッドをM3-1のスコープとし、キューとしての実務(排他取得・可視性タイムアウト・リトライ・デッドレター)をM3-2に委ねた
- M3-3(Runnerのプロセス分離)で複数のRunnerプロセスが同一Job DBから「取り出す」動作(claim)が必要になる。本ADRはM3-2時点で**Job Repositoryのメソッドとして**排他取得等を実装するにとどめ、Runner Dispatcher側の実配線はM3-3のスコープとする
- 外部依存は最小限(専用キューミドルウェアは導入しない)

## 決定

### 排他取得は`claim`メソッドを新設し、「1つのUPDATE文」でアトミックに実現する

`JobRepository`に`claim(worker_id, job_types=None, visibility_timeout_seconds=...) -> Job | None`・`heartbeat`・`complete`・`fail`・`list_dead_letters`を追加する(既存5メソッドは無改修)。

`claim`のSQLite実装は、`PENDING`のJobを1件選んで`RUNNING`へ更新する処理を**単一のUPDATE文**(サブクエリで対象idを選ぶ)で行う。1つのSQL文である限りSQLiteは実行全体をアトミックに処理するため、「SELECTで候補を選ぶ→UPDATEする」の2段階方式で起こりうるTOCTOU競合を、追加ロックなしに防げる。`RETURNING`句(SQLiteバージョン依存)は使わず、`UPDATE`直後に`lease_token`で取得済みJobを`SELECT`する。

### 可視性タイムアウトは`claim`実行時に「先に期限切れJobを回収する」形で実装する

専用のバックグラウンドスレッドは持たない。`claim`のたびに期限切れの`RUNNING` Jobを回収し、リトライ上限未満なら`PENDING`へ戻す。「誰かが次にJobを取りに来たときに、放置されたJobが後片付けされる」という遅延はあるが、常駐の定期実行機構を増やす複雑さを避けた。

### リトライ回数は`attempts`/`max_attempts`カラムで記録する

既定値`DEFAULT_MAX_ATTEMPTS = 3`。`fail(job_id, worker_id, error, retry=True)`はリトライ可能なら`PENDING`へ戻し、上限到達または`retry=False`なら`FAILED`かつデッドレターとして確定する。

### デッドレターは新しい`JobStatus`値を追加せず、`FAILED` + `dead_letter_at`カラムで表現する

[ADR-0016](0016-job-abstraction.md)が確定した状態機械を変更しない。「リトライ上限に達した`FAILED`」は新しい状態ではなく`FAILED`に付随するメタデータと捉える。

### `claim`/`complete`/`fail`はリース所有者(`worker_id`)を検証する

一致しなければ`LeaseLostError`を送出する(可視性タイムアウトで別workerに再取得された後、元workerが遅れて完了報告してくるスプリットブレインを検知するフェンシングの簡易版)。`fail`のリトライ可能パス(`RUNNING → PENDING`)は[ADR-0016](0016-job-abstraction.md)の遷移表にないキュー内部専用の遷移のため、`update_status`を経由せず直接SQLを実行する。

既存の`execute_review_job`は`enqueue`→`update_status`という従来経路のままとし、`claim`系の新メソッドは使わない(M3-3でRunner Dispatcherが使う想定)。

### 複数プロセス/複数ホストからの同時アクセス: SQLiteの単一書き込みロック + `busy_timeout`に委ねる

WALモードは採用しない(却下した選択肢を参照)。プロセス内は`threading.RLock`で直列化し、プロセス間の排他はSQLite自身の書き込みロックに委ねる。

### スキーマ変更は`ALTER TABLE`による後方互換マイグレーションとする

既存DBに`attempts`・`max_attempts`・`lease_owner`・`lease_token`・`lease_expires_at`・`dead_letter_at`カラムを追加する。

## 却下した選択肢

- **専用のキューミドルウェア(Redis/RabbitMQ等)**: 依存最小化方針に反する。M3規模の同時実行数ではSQLiteで十分と判断
- **`update_status`を拡張して`claim`相当の機能も持たせる**: 「単純な状態報告」と「リース検証・リトライ判定」という異なる関心事が混在するため、独立メソッドとして追加した
- **`JobStatus`に`DEAD_LETTER`を新しい値として追加する**: ADR-0016が確定した状態機械の変更になり影響範囲が広がる
- **`SELECT`→`UPDATE`の2段階方式**: TOCTOU競合を防ぐには明示的なトランザクション制御が必要になり複雑になる
- **`RETURNING`句を使う**: 実行環境によっては使えない可能性があるため、追加の`SELECT`で代替した
- **WALモード**: 追加ファイルが増え、Windows環境での挙動に懸念があるため見送り
- **リース検証にフェンシングトークンを`Job`に公開する**: 現状の同時実行規模では`worker_id`一致チェックで十分
- **可視性タイムアウトの回収を専用バックグラウンドスレッドで行う**: `claim`実行時の遅延評価方式で十分と判断

## 影響

- M3-3(Runnerのプロセス分離)は本ADRの`claim`/`heartbeat`/`complete`/`fail`を使ってRunner Dispatcherを実装する形で着手できる
- M3-7(HTTP API層)は`list_dead_letters`を使ってデッドレターJobの一覧・再投入APIを提供できる
