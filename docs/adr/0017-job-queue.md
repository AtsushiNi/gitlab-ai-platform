# ADR-0017: Job Queue(取得の排他・可視性タイムアウト・リトライ・デッドレター)の設計

- Issue: [#92](https://github.com/AtsushiNi/gitlab-ai-platform/issues/92) (M3-2)
- 状態: 決定

## 背景・制約

- [ADR-0016](0016-job-abstraction.md)は`JobRepository`(`enqueue`/`get`/`update_status`/
  `list_by_status`/`close`の5メソッド)をM3-1のスコープとし、「キューとしての実務(取得の
  排他・可視性タイムアウト・リトライ・デッドレター)はM3-2のスコープとする」「`claim`のような
  新メソッドが必要かどうかはM3-2側のADRで検討する」と明記している。本ADRはその判断を行う
- `references/タスク整理.md`のM3-2は「まずはDBベースのキュー(取得の排他、可視性タイムアウト、
  リトライ、デッドレター)」を求めている
- M3-1時点の`SqliteJobRepository`(`src/gitlab_ai_platform/job/sqlite.py`)は単一プロセス・
  逐次実行前提で、`cli/single_run.py`の`execute_review_job`が`enqueue`直後に同一プロセス内で
  同期的に処理する構成。M3-3(Runnerのプロセス分離、#93)でRunnerが別プロセス/別ホストに
  分離されると、複数のRunnerプロセスが同一のJob DBから「取り出す」動作(claim)が必要になる。
  本ADRはM3-3を見据えつつ、M3-2時点では**Job Repositoryのメソッドとして排他取得・可視性
  タイムアウト・リトライ・デッドレターを実装する**にとどめ、Runner Dispatcher側の実配線は
  M3-3のスコープとする(既存の`execute_review_job`はM3-1と同じ「enqueue直後に同期処理する」
  経路のまま変更しない)
- [ADR-0015](0015-parallel-review-execution.md)は`SqliteStateStore`について「単一コネクション+
  `threading.RLock`でプロセス内スレッドを直列化する」設計を確立済み。`SqliteJobRepository`も
  同じ方式(単一コネクション+`RLock`)を踏襲しているが、M3-3以降は「同一ホスト内の複数スレッド」
  だけでなく「複数プロセス(将来は複数ホスト)からの同一DBファイルへの同時アクセス」が発生しうる。
  `RLock`はプロセス内にしか効かないため、プロセスをまたぐ排他はSQLite自体の機構に委ねる必要がある
- ADR-0001の方針により外部依存は最小限(`requests`/`pytest`のみ)。ORM・専用キューミドルウェア
  (Redis/RabbitMQ等)は導入しない

## 決定

### 排他取得は`claim`メソッドを新設し、「1つのUPDATE文」でアトミックに実現する

`JobRepository`に以下のメソッドを追加する(既存5メソッドのシグネチャ・挙動は変更しない。
`references/タスク整理.md`M3-6([#96](https://github.com/AtsushiNi/gitlab-ai-platform/issues/96))が
並行して`enqueue`に依存している可能性があるため、既存メソッドへの後方互換を最優先する):

```python
def claim(
    self,
    worker_id: str,
    *,
    job_types: Sequence[JobType] | None = None,
    visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
) -> Job | None: ...


def heartbeat(
    self,
    job_id: str,
    worker_id: str,
    *,
    visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
) -> Job: ...


def complete(
    self, job_id: str, worker_id: str, result: dict[str, Any] | None = None
) -> Job: ...


def fail(
    self, job_id: str, worker_id: str, error: str, *, retry: bool = True
) -> Job: ...


def list_dead_letters(self) -> list[Job]: ...
```

`claim`のSQLite実装は、`PENDING`のJobを1件選んで`RUNNING`へ更新する処理を**単一のUPDATE文**
(サブクエリで対象idを選ぶ)で行う:

```sql
UPDATE jobs
SET status = 'running', lease_owner = ?, lease_token = ?, lease_expires_at = ?,
    attempts = attempts + 1, updated_at = ?
WHERE id = (
    SELECT id FROM jobs WHERE status = 'pending' [AND job_type IN (...)]
    ORDER BY created_at LIMIT 1
)
```

1つのSQL文である限り、SQLiteは文の実行全体を単一の書き込みロックの下でアトミックに処理する
(内部的に複数のB-tree操作があっても、他コネクションがその途中に割り込むことはない)。これにより
「`SELECT`で候補を選ぶ→アプリ側で`UPDATE`する」という2段階方式で起こりうるTOCTOU競合を、
アプリケーションコードでの追加ロックなしに防げる。取得できるJobがなければ`WHERE id = (...)`の
サブクエリが`NULL`を返し、`UPDATE`は0行に対して適用されて`claim`は`None`を返す。

`lease_token`(呼び出しごとに生成する`uuid4`)は、`UPDATE`直後に「どの行を取得できたか」を
一意に特定するために使う(`RETURNING`句はSQLiteのバージョンによって使えない場合があるため
使わない。ADR-0001の「依存最小化」と同じ考え方で、広くサポートされたSQL機能のみに頼る)。
`UPDATE`実行後、`SELECT ... WHERE lease_token = ?`で取得済みのJobを1件取り出して返す。

### 可視性タイムアウトは`claim`実行時に「先に期限切れJobを回収する」形で実装する(専用のバックグラウンドスレッドは持たない)

`claim`は本体のUPDATE文を実行する前に、`lease_expires_at`が過ぎた`RUNNING`のJobを一括で
回収する(`_reclaim_expired_locked`)。回収時の扱いは後述の「リトライ・デッドレター」と共通の
ロジック(`_fail_locked`)を再利用し、`attempts < max_attempts`ならリース情報をクリアして
`PENDING`へ戻す(再取得可能にする)、上限に達していれば`FAILED`かつデッドレターとして
確定させる。

専用のバックグラウンドスレッド/cron的な仕組みを持たない理由: 本リポジトリは`ProcessLock`
([ADR-0009](0009-cli-watch-design.md))はあるが常駐の定期実行機構は持たず、新たに定期実行の
仕組みを追加するとテスト・運用の複雑さが増す。`claim`のたびに期限切れJobを回収する方式なら、
「誰かが次にJobを取りに来たときに、放置されたJobが後片付けされる」という遅延はあるが、
`claim`を呼ぶプロセスが1つもない状態(=そもそも処理が進まない状態)でしか問題にならず、実害が
小さい。将来Runner Dispatcher(M3-3)が定期的に`claim`をポーリングする構成になるため、実質的に
可視性タイムアウトの検知間隔はポーリング間隔と一致する。

### リトライ回数は`attempts`/`max_attempts`カラムで記録し、`enqueue`時に上限を指定できる

`jobs`テーブルに`attempts INTEGER NOT NULL DEFAULT 0`・
`max_attempts INTEGER NOT NULL DEFAULT {DEFAULT_MAX_ATTEMPTS}`を追加する。`attempts`は
`claim`が成功するたびに1インクリメントする(「何回取り出されたか」)。`max_attempts`は
Job単位で上書きできるよう、`enqueue`にキーワード専用引数を追加する(**位置引数の並びは
変更しないため既存呼び出しは無改修で動く**):

```python
def enqueue(
    self,
    job_type: JobType,
    payload: dict[str, Any],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Job: ...
```

既定値`DEFAULT_MAX_ATTEMPTS = 3`は、Claude Codeのheadless実行のように1回の失敗が
ネットワーク瞬断等の一過性要因である可能性がある一方、無限リトライはコスト
(Bedrock APIコール・Claude Codeプロセス起動)が際限なく積み上がるリスクがあるため、
小さい固定値から始める(値そのものの妥当性はM3-3でRunner Dispatcherを実配線した後、
実運用のログを見て調整する)。

`fail(job_id, worker_id, error, retry=True)`は、claim済みJobの失敗をworker(将来のRunner
Dispatcher)が明示的に報告するための入口。`retry=True`(既定)かつ`attempts < max_attempts`
であれば`PENDING`へ戻し再取得対象にする。`retry=False`(呼び出し側が「リトライ不可能な
エラーだと判断した」場合。例: payloadが不正で何度実行しても同じ結果になることが明らかな場合)、
または`attempts >= max_attempts`に達している場合は、`FAILED`へ遷移させデッドレターとして
確定する。

### デッドレターは新しい`JobStatus`値を追加せず、`FAILED` + `dead_letter_at`カラムで表現する

却下した選択肢に後述する理由により、`JobStatus`に`DEAD_LETTER`のような新しい値は追加しない。
`jobs`テーブルに`dead_letter_at TEXT`(ISO 8601、`NULL`可)を追加し、リトライ上限に達した
(または`retry=False`で失敗が確定した)`FAILED`Jobにのみこの値を設定する。`list_dead_letters()`
で`dead_letter_at IS NOT NULL`のJobを一覧できる。

この設計により、[ADR-0016](0016-job-abstraction.md)が確定した状態機械
(`PENDING → RUNNING → (DONE | FAILED | WAITING_HUMAN)`、`WAITING_HUMAN`からの復帰/却下)は
一切変更しない。既存の`update_status`は無改修のまま、`_ALLOWED_TRANSITIONS`による遷移検証も
そのまま使い続ける。

### `claim`/`complete`/`fail`はリース所有者(`worker_id`)を検証し、`update_status`を内部で再利用する

`complete(job_id, worker_id, result)`・`fail(job_id, worker_id, error, retry)`は、まず対象Jobの
`lease_owner`が呼び出し元の`worker_id`と一致するか検証し、一致しなければ`LeaseLostError`
(新設、`JobError`のサブクラス)を送出する。これは「可視性タイムアウトで別workerに再取得された
後に、元workerが遅れて完了/失敗を報告してくる」というスプリットブレイン状況を検知するための
ガードで、SQSやCelery等の一般的なキュー実装が採用する「フェンシング」の簡易版にあたる
(却下した選択肢を参照)。

検証後、`complete`は内部で既存の`update_status(job_id, JobStatus.DONE, result=result)`を
呼び出し(`RUNNING → DONE`は[ADR-0016](0016-job-abstraction.md)で既に許可されている遷移)、
リース関連カラム(`lease_owner`/`lease_token`/`lease_expires_at`)をクリアする。`fail`の
「上限到達 or `retry=False`」パスも同様に内部で`update_status(..., JobStatus.FAILED, ...)`を
呼び出したうえで`dead_letter_at`を設定する。一方`fail`の「リトライ可能」パス(`RUNNING → PENDING`)
は[ADR-0016](0016-job-abstraction.md)の遷移表にない**キュー内部専用の遷移**のため、
`update_status`を経由せず直接SQLを実行する(`_ALLOWED_TRANSITIONS`はアプリケーションが
明示的に報告する状態変化の検証であり、キューが可視性タイムアウト/リトライのために内部で
Jobを出し直す操作とは責務が異なるため、意図的に迂回する。この区別を本ADRで明文化することで、
「なぜ`RUNNING → PENDING`が一部の経路でだけ許可されるのか」を後から追えるようにする)。

既存の`execute_review_job`(`cli/single_run.py`)は`enqueue`→`update_status(RUNNING)`→
`execute_review`→`update_status(DONE/FAILED)`という、M3-1と同じ「取り出さず直接処理する」
経路のままとし、`claim`/`complete`/`fail`は使わない(変更しない)。これらの新メソッドは
M3-3でRunner Dispatcherが「別プロセスとしてJobを取り出して処理する」経路を実装する際に
使われる想定で、M3-2時点ではJob Repositoryのメソッドとしての実装とテストのみを行う。

### 複数プロセス/複数ホストからの同時アクセス: SQLiteの単一書き込みロック + `busy_timeout`に委ねる

`SqliteJobRepository`はコネクションごとに`PRAGMA busy_timeout`(既定5000ms)を設定する。
複数プロセスが同時に`claim`(書き込みを伴う)を実行しようとした場合、SQLiteは書き込みロックを
早い者勝ちで1つのコネクションにのみ許可し、他のコネクションは`busy_timeout`の間ロック解放を
待ってから再試行する(ADR-0015が課題視した`sqlite3.OperationalError: database is locked`を、
即時エラーではなく「一定時間内なら自動的に待って再試行する」形に緩和する)。`claim`のUPDATE文
自体が「対象を選ぶ→更新する」を1文で行うアトミックな設計(前述)のため、ロックを獲得した
コネクションが必ず一貫した状態を書き込める。

WALモード(Write-Ahead Logging)は採用しない(却下した選択肢を参照)。プロセス内はこれまで通り
`threading.RLock`で直列化し(ADR-0015と同じ理由: `check_same_thread=False`の単一コネクションを
複数スレッドで共有するため)、プロセス間の排他はSQLite自身の書き込みロックに委ねる、という
二段構えにする。

### スキーマ変更は`ALTER TABLE`による後方互換マイグレーションとする

既存のM3-1のJob DB(ファイル)に新しいカラムを追加できるよう、`SqliteJobRepository.__init__`で
`PRAGMA table_info(jobs)`を見て不足しているカラムのみ`ALTER TABLE jobs ADD COLUMN`する
(新規DBは`CREATE TABLE`時点で最初から全カラムを持つため、マイグレーションは実質何もしない)。
追加するカラム:

```sql
ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE jobs ADD COLUMN lease_owner TEXT;
ALTER TABLE jobs ADD COLUMN lease_token TEXT;
ALTER TABLE jobs ADD COLUMN lease_expires_at TEXT;
ALTER TABLE jobs ADD COLUMN dead_letter_at TEXT;
```

`Job`(frozen dataclass)にも同名の新フィールドを**末尾にデフォルト値付きで**追加する
(`attempts: int = 0`等)。既存フィールドの並び・型は変更しないため、キーワード引数で`Job`を
構築している既存コード(テストの`_job()`ヘルパー等)は無改修で動く。

## 却下した選択肢

- **専用のキューミドルウェア(Redis/RabbitMQ/Amazon SQS等)を導入する**: ADR-0001の依存最小化
  方針に反する。M3規模の同時実行数(`max_parallel`は既定5、ADR-0015)では、SQLiteの単一書き込み
  ロック+`busy_timeout`で十分安全に運用できると判断した。将来ホスト数・スループットが増え、
  この前提が崩れた時点で再検討する
- **`update_status`を拡張し、`worker_id`/`retry`引数を追加して`claim`相当の機能も持たせる**:
  1メソッドに「単純な状態報告」と「リース検証・リトライ判定・デッドレター化」という異なる関心事が
  混在し、可読性が落ちる。CLAUDE.mdの指示(「既存メソッドのシグネチャ変更より、新メソッド追加を
  優先する」)にも沿い、`claim`/`complete`/`fail`/`heartbeat`を独立したメソッドとして追加した
- **`JobStatus`に`DEAD_LETTER`を新しい値として追加する**: [ADR-0016](0016-job-abstraction.md)が
  確定した状態機械(5値・許可される遷移一覧)を変更することになり、影響範囲(`_ALLOWED_TRANSITIONS`・
  永続化済みレコードとの互換性・M3-6/M3-7の実装)が広がる。「リトライ上限に達した`FAILED`」は
  `FAILED`の一種であり、新しい状態というより`FAILED`に付随するメタデータ(いつ・なぜ
  デッドレター化したか)と捉える方が[ADR-0016](0016-job-abstraction.md)の決定を尊重できるため、
  `dead_letter_at`カラムで表現することにした
- **`SELECT`で候補を選んでからアプリケーション側で`UPDATE`する2段階方式**: 2つの文の間に
  他コネクションが割り込むTOCTOU競合を防ぐには、明示的なトランザクション制御
  (`BEGIN IMMEDIATE`等)かリトライループが必要になり実装が複雑になる。1つのUPDATE文に
  サブクエリで候補選択を埋め込む方式なら、SQLiteの文単位のアトミック性だけで安全性を得られる
- **`RETURNING`句(SQLite 3.35+)で`UPDATE`の結果を直接受け取る**: 実行環境(Windows/Linux、
  Pythonにバンドルされる`libsqlite3`のバージョン)によっては使えない可能性があり、ADR-0001の
  「広く確実に動くものを優先する」方針に反する。`lease_token`を使った追加の`SELECT`で代替した
  (コストは1回の追加読み取りのみで許容範囲)
- **WALモード(`PRAGMA journal_mode=WAL`)を有効にする**: 複数コネクションの並行読み書き性能は
  上がるが、`-wal`/`-shm`という追加ファイルが増え、ネットワークドライブ・一部のWindows環境での
  挙動に懸念がある(このプロジェクトはWindows上での運用も前提、`docs/architecture.md`
  「Windows/Linuxの分担」)。M3規模のスループットでは通常のジャーナルモード+`busy_timeout`で
  十分と判断し、WAL化はより高いスループットが必要になった時点(M3-5のPostgreSQL移行等)で
  再検討する
- **リース検証にフェンシングトークンを`Job`に公開し、呼び出し側に完全なトークン照合を要求する**:
  `worker_id`一致チェックより厳密(同一`worker_id`を使う複数スレッド間の取り違えも防げる)だが、
  現状の同時実行規模(`max_parallel`既定5、ADR-0015)では「Runnerプロセス1つ=`worker_id`1つ」
  という運用で十分であり、呼び出し側APIを複雑にするコストに見合わないと判断した。将来Runnerが
  1プロセス内で複数スレッドから同一`worker_id`を使うようになった場合は、`lease_token`を
  呼び出し側にも公開する形に拡張する
- **可視性タイムアウトの回収を専用のバックグラウンドスレッド/定期実行で行う**: 「決定」節の
  理由により、`claim`実行時に回収する遅延評価方式で十分と判断し、常駐スレッドを増やす複雑さを
  避けた

## 影響

- `src/gitlab_ai_platform/job/protocol.py`・`sqlite.py`・`errors.py`・`__init__.py`を変更した。
  `cli/single_run.py`の`execute_review_job`・`cli/watch.py`は無改修(既存の5メソッドのみを使う
  経路は変わらない)
- `docs/specs/job-model.md`を本ADRの決定に合わせて更新した
- M3-3(Runnerのプロセス分離、[#93](https://github.com/AtsushiNi/gitlab-ai-platform/issues/93))は
  本ADRの`claim`/`heartbeat`/`complete`/`fail`を使ってRunner Dispatcherを実装する形で着手できる
- M3-7(HTTP API層、[#97](https://github.com/AtsushiNi/gitlab-ai-platform/issues/97))は
  `list_dead_letters`を使ってデッドレターJobの一覧・再投入APIを提供できる見込み
- M3-6(Webhook対応、[#96](https://github.com/AtsushiNi/gitlab-ai-platform/issues/96))が依存する
  `enqueue`のシグネチャは(キーワード専用引数の追加のみで)後方互換を保っているため、影響しない
