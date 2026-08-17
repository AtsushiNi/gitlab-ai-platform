# ADR-0021: State Store の PostgreSQL 対応

- Issue: [#95](https://github.com/AtsushiNi/gitlab-ai-platform/issues/95) (M3-5)
- 状態: 決定

## 背景・制約

- `docs/architecture.md`の「MVP → AI Platformへの成長パス」表は、State Storeについて
  「SQLite → バックエンドがPostgreSQLに移行(M3-5)。リポジトリ層抽象化のおかげでAPIは不変」と
  明記している。[ADR-0003](0003-state-store-interface.md)は`StateStore`を`typing.Protocol`で
  抽象化し、スキーマも「ANSI標準のDDLに近い形にとどめている(実際の移行はM3-5で検証する)」と
  していた。本ADRはその検証と実装を行う。
- `docs/architecture.md`「Windows/Linuxの分担」により、PostgreSQL移行はM3以降の無人実行
  (Linux/Docker側)を見据えたものであり、Windows側(人間が張り付くM1〜M2運用)は引き続き
  SQLiteのままでよい。したがって**SQLiteとPostgreSQLの両対応**(設定で切り替え)が要件になる。
  Windows環境でPostgreSQLの依存(ドライバ)を強制的にインストールさせる理由はない。
- [ADR-0001](0001-repository-structure.md)は外部依存を最小限に絞る方針で、新規依存追加時は
  「Windowsのオフライン制約下(管理者権限なし・外部ダウンロード制限あり)で入手可能か」を
  確認することを求めている。
- [ADR-0015](0015-parallel-review-execution.md)は、`SqliteStateStore`が単一コネクション+
  `threading.RLock`で並行アクセスを直列化する設計を採用し、「コネクションプールは現状の
  `max_parallel`(既定5)程度の同時実行数では導入するほどの性能メリットが小さい」として
  見送っていた。PostgreSQL実装でもこの前例を踏まえて並行アクセス方針を決める必要がある。

## 決定

### PostgreSQLドライバは`psycopg`(psycopg3)を`binary`extra付きで使う

`psycopg[binary]`を採用する。理由:

- `binary`extraはコンパイル済みのバイナリwheel(`libpq`同梱)を配布しており、Windows・
  管理者権限なし環境でも`pip install`だけで導入できる(Cコンパイラ・PostgreSQL開発ヘッダの
  事前インストールが不要)。ADR-0001の「Windowsのオフライン制約下で入手可能か」という基準を
  満たす、数少ないPostgreSQLドライバの一つ
- psycopg2系(`psycopg2-binary`)も同様にバイナリwheelを配布するが、psycopg2は開発が
  メンテナンスモードに近く、新規プロジェクトでは後継のpsycopg3が推奨されている。型ヒント
  対応(`py.typed`)もpsycopg3の方が優れており、`mypy src`との相性が良い
- `asyncpg`は非同期専用であり、本リポジトリの他コンポーネント(Workspace Manager・Claude
  Code Runner等)がすべて同期APIで書かれている前提([ADR-0015](0015-parallel-review-execution.md)
  「却下した選択肢」の`asyncio`不採用)と整合しない

**依存の入れ方**: `pyproject.toml`の`[project.optional-dependencies]`に`postgres`extraとして
追加し、ベースの依存には含めない。SQLiteのみを使うWindows運用ではインストール不要のままにする
(ADR-0001の「依存は最小限」を維持)。CI(`dev`extra)には`psycopg[binary]`を含め、
`mypy src`が`store/postgres.py`を型チェックできるようにする(型スタブは`psycopg`本体に
同梱されているため追加の`types-*`パッケージは不要)。

```toml
[project.optional-dependencies]
postgres = ["psycopg[binary]>=3.1"]
dev = ["pytest>=8.0", "ruff>=0.16", "mypy>=1.10", "psycopg[binary]>=3.1"]
```

`store/postgres.py`は`psycopg`をモジュールトップレベルでimportする(遅延importにしない)。
`postgres`backendを選ばない限りこのモジュール自体がimportされない設計(後述の`factory.py`)に
しているため、SQLiteのみの利用者が誤って`psycopg`未インストールでクラッシュすることはない。

### スキーマはSQLite実装とほぼ同一のDDLをそのまま使う

[ADR-0003](0003-state-store-interface.md)の想定通り、SQLite版の`CREATE TABLE`文はPostgreSQLの
方言差をほぼ気にせず使えることを確認した:

```sql
CREATE TABLE IF NOT EXISTS review_records (
    project TEXT NOT NULL,
    mr_iid INTEGER NOT NULL,
    commit_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    reviewed_at TEXT,
    result_path TEXT,
    PRIMARY KEY (project, mr_iid, commit_sha)
)
```

`TEXT`/`INTEGER`/複合`PRIMARY KEY`/`CREATE TABLE IF NOT EXISTS`はいずれもPostgreSQLの標準機能で
そのまま動く。唯一の実質的な差異はプレースホルダ構文(SQLiteの`?` → PostgreSQLの`%s`)のみで、
スキーマDDL自体に変更は不要だった。

`reviewed_at`もSQLite実装と同じく`TEXT`(ISO 8601文字列)のまま保持する。`TIMESTAMPTZ`型に
変えることも検討したが、あえて見送った(「却下した選択肢」参照)。

### 接続設定: `config.toml`の`[store]`セクションに`backend`を追加し、Postgres接続情報は`[store.postgres]`に分ける。パスワードのみ`.env`/環境変数

GitLab PAT([ADR-0001](0001-repository-structure.md)、`config/loader.py`)・Webhook Secret Token
([ADR-0018](0018-webhook-receiver.md))と同じ方針で、シークレット(パスワード)のみ
`.env`/環境変数経由にし、それ以外の接続情報(ホスト・ポート・DB名・ユーザー名)は
`config.toml`に書く。

```toml
[store]
backend = "sqlite"       # "sqlite"(既定) | "postgresql"
db_path = "state.db"     # backend = "sqlite" の場合のみ使用

[store.postgres]          # backend = "postgresql" の場合のみ使用
host = "localhost"
port = 5432
dbname = "gitlab_ai_platform"
user = "gitlab_ai_platform"
```

```text
# .env
GITLAB_AI_PLATFORM_STORE_POSTGRES_PASSWORD=xxxxxxxx
```

パスワードを必須項目にはしない(`ConfigError`にしない)。ローカルDocker Compose環境では
`trust`認証(パスワード無し)で運用するケースもあり、GitLab PAT・Webhook Secretのように
「機能を有効化したら必ず要る」とは言い切れないため。空文字列のままpsycopgに渡し、
実際の認証失敗はPostgreSQL接続時のエラーとして表面化させる。

### SQLite実装との共存: `store/factory.py`の`build_state_store(config)`で切り替える

`Config.store_backend`(`"sqlite"` | `"postgresql"`)を見て、対応する具象実装を構築する
ファクトリ関数を新設する。

```python
def build_state_store(config: Config) -> StateStore:
    if config.store_backend == "postgresql":
        from .postgres import PostgresStateStore  # 遅延import(後述)

        return PostgresStateStore(...)
    return SqliteStateStore(config.state_db_path)
```

`store/postgres.py`のimportを関数内に留める(モジュールレベルではimportしない)ことで、
`backend = "sqlite"`(Windows運用の既定)の場合に`psycopg`が未インストールでも
`gitlab_ai_platform.store`パッケージ全体のimportが失敗しないようにする。呼び出し側
(`cli/single_run.py`・`cli/watch.py`)は`SqliteStateStore(...)`を直接構築していた箇所を
`build_state_store(config)`に置き換える。これにより呼び出し側は`StateStore`(Protocol型)しか
知らず、具象クラスの選択はconfigとfactoryに閉じる。

### 並行アクセス: 単一コネクション + `threading.RLock`のまま(コネクションプールは導入しない)

[ADR-0015](0015-parallel-review-execution.md)の`SqliteStateStore`と同じ形にする。理由:

- 現時点の並行度は`max_parallel`(既定5)程度であり、PostgreSQL側でもこの規模では
  コネクションプール(`psycopg_pool`等)を導入するほどの性能上のメリットが小さい
  ([ADR-0015](0015-parallel-review-execution.md)がSQLiteについて下した判断と同じ理由)
- psycopg3のコネクションオブジェクトはスレッドセーフではない(複数スレッドから同時に同じ
  コネクションのカーソルを操作すると未定義動作になりうる)ため、SQLite実装と同様に
  `threading.RLock`で`find`/`create`/`update_status`/`close`の本体を直列化する
  (`update_status`が内部で`find`を呼ぶ再入のため`RLock`)
- 新規依存(`psycopg_pool`)を増やさずに済む(ADR-0001の依存最小化方針)

コネクションプールを追加する動機が生まれるのは、M3-2(Job Queue)・M3-3(Runnerのプロセス分離)で
実行主体がスレッドからプロセス/コンテナに変わり、単一プロセス内の直列化では並行度が
不十分になった時点だと想定する。その際は本ADRを見直す。

### エラー変換: `psycopg.errors.UniqueViolation`を`DuplicateReviewError`に変換する

SQLite実装が`sqlite3.IntegrityError`(かつメッセージに`UNIQUE constraint failed`を含む場合)を
`DuplicateReviewError`に変換しているのと同じ契約を、PostgreSQL実装でも
`psycopg.errors.UniqueViolation`(`IntegrityError`のサブクラスで、一意制約違反時にのみ送出される)を
捕まえて実現する。SQLiteのようなメッセージ文字列の部分一致判定は不要で、例外クラスによる
判定だけで足りる(psycopg3は制約違反の種別を専用の例外クラスで表現するため)。

### テスト方針: 実PostgreSQL接続が必要なテストは`pytest`マーカーでスキップ可能にする

CLAUDE.mdの「外部依存に触れるテストはモック/フィクスチャを使い、実サービスへは繋がない」方針を
踏まえ、以下の2段構えにする:

1. **契約テスト(`tests/gitlab_ai_platform/store/test_postgres.py`)**: 実PostgreSQLサーバーへの
   接続を必要とする。`psycopg`が未インストール、または接続先(環境変数
   `GITLAB_AI_PLATFORM_TEST_POSTGRES_DSN`)が設定されていない/接続できない場合は
   `pytest.skip`する。CI(GitHub Actions、PostgreSQLサービスコンテナ無し)では常にスキップされ、
   `pytest`全体の成功/失敗には影響しない。ローカル開発やPostgreSQLサービスを用意できる環境
   (開発者手元のDocker等)では実際にテーブル作成・CRUD・一意制約違反を検証する
   (`test_sqlite.py`と同等のシナリオを踏襲)。本ADRの実装時、開発者手元のDocker PostgreSQLに
   対して実行し、全シナリオが通ることを確認済み
2. **エラー変換の単体テスト**: 実DBに繋がずに`psycopg.errors.UniqueViolation`を
   `DuplicateReviewError`に変換するロジックだけを検証したい場合、実DB無しでは
   `UniqueViolation`を意図的に発生させる手段がない(psycopg3のexceptionは接続なしに
   構築しづらい)。このロジックは契約テスト(1)の中で実DBに対して一意制約違反を実際に
   起こすことでカバーし、別立てのモックテストは追加しない(モックで`psycopg`の内部例外構造を
   模倣すると、実際のpsycopgの挙動と乖離するリスクの方が大きいと判断)
3. **`build_state_store`(factory)の単体テスト**: `config.store_backend`の値によって
   `SqliteStateStore`/`PostgresStateStore`のどちらが構築されるかは、`psycopg`が未インストールの
   環境でも検証できる必要がある。`backend = "postgresql"`のケースは、実際に接続を試みる前に
   `PostgresStateStore.__init__`が呼ばれた時点で失敗しうるため、`unittest.mock.patch`で
   `PostgresStateStore`をモックし、正しい引数で呼ばれたことだけを検証する(実接続はしない)

## 却下した選択肢

- **`asyncpg`**: 非同期専用ドライバであり、リポジトリ全体が同期APIである前提と合わない
  (前述)。将来的にリポジトリ全体を`asyncio`化する場合は再検討する
- **`psycopg2-binary`**: 動作はするが、psycopg3が後継として推奨されておりメンテナンス面・
  型ヒント対応で劣る。新規追加する依存としてはpsycopg3を優先した
- **SQLAlchemy等のORMを導入**: [ADR-0003](0003-state-store-interface.md)がSQLite実装時に
  却下した理由(スキーマが単一テーブル・4メソッドのみで生SQLで十分見通せる)がそのまま
  PostgreSQL実装にも当てはまる。SQLite/PostgreSQL両対応を理由にORM化する誘惑はあったが、
  差分がプレースホルダ構文と例外クラスのみと小さく、ORMの学習・依存コストに見合わないと判断した
- **`reviewed_at`をPostgreSQLでは`TIMESTAMPTZ`型にする**: PostgreSQL固有の型を使えば
  DB側の日時演算・型安全性は上がるが、(a) SQLiteとスキーマDDLが分岐し「ほぼそのまま使える」
  というADR-0003の前提を弱める、(b) `TIMESTAMPTZ`はタイムゾーン付きで保存されるため、
  `datetime.isoformat()`が返すnaive/aware datetimeの扱いをSQLite実装と揃える追加の変換ロジックが
  必要になる。両実装で完全に同じ入出力契約(ISO 8601文字列としてTEXT保存、呼び出し側には
  `datetime`で返す)を保つ方を優先し、`TEXT`のまま統一した
- **コネクションプール(`psycopg_pool.ConnectionPool`)の導入**: 前述の通り、現在の並行度
  (`max_parallel`既定5)では導入コストに見合わないため見送った。将来Job Queue
  (M3-2)・Runnerのプロセス分離(M3-3)で並行度モデルが変わった時点で再検討する
- **CIにPostgreSQLサービスコンテナを追加してCI上で契約テストを常時実行する**: 技術的には
  GitHub Actionsの`services:`で可能だが、本Issueのスコープ(SQLite/PostgreSQL両対応の
  リポジトリ層実装)を超えてCI構成(`.github/workflows/ci.yml`)を変更することになり、
  他の並行Issue(M3-3/M3-4/M3-7/M3-8)のCI変更と衝突するリスクがある。CI常時実行の価値は
  認めつつ、本Issueでは見送り、必要なら別Issueで検討する

## 影響

- 新規ファイル: `src/gitlab_ai_platform/store/postgres.py`(`PostgresStateStore`)、
  `src/gitlab_ai_platform/store/factory.py`(`build_state_store`)
- 変更: `src/gitlab_ai_platform/config/models.py`・`config/loader.py`
  (`store_backend`/`store_postgres_*`フィールド追加)、`src/gitlab_ai_platform/store/__init__.py`
  (`build_state_store`のexport)、`cli/single_run.py`・`cli/watch.py`
  (`SqliteStateStore(...)`直接構築を`build_state_store(config)`に置き換え)
- 無変更: `src/gitlab_ai_platform/store/protocol.py`・`sqlite.py`・`errors.py`・`types.py`
  ([ADR-0003](0003-state-store-interface.md)のProtocol・スキーマ・エラー契約は維持)
- `pyproject.toml`に`postgres`extraを追加、`dev`extraに`psycopg[binary]`を追加
- `docs/specs/state-store.md`・`docs/operations/configuration.md`を本ADRの内容に合わせて更新した
- Job Repository(`job/`、M3-1〜M3-2)は本Issueの対象外。State Storeと同じ「Protocol +
  複数バックエンド」の形を将来Job Repositoryにも適用するかどうかは、その時点で別途判断する
