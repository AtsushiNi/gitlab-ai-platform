# ADR-0003: State Store のインターフェースとスキーマ設計

- Issue: [#32](https://github.com/AtsushiNi/gitlab-ai-platform/issues/32) (M1-4)
- 状態: 決定

## 背景・制約

- `docs/architecture.md` の設計方針により、State Storeは「`(project, mr_iid, commit_sha)`
  単位でレビュー状態(`status`/`reviewed_at`/結果パス)を記録し、二重レビューを防ぐ」ことのみを
  責務とする。ビジネスロジック(レビューするか否かの判断)は持たない
- MVP(M1)ではSQLiteを使うが、`docs/architecture.md`の「MVP → AI Platformへの成長パス」表で
  「バックエンドがPostgreSQLに移行(M3-5)。リポジトリ層抽象化のおかげでAPIは不変」と
  明記されている。M1-1(GitLab Adapter)がProtocolで実装を差し替え可能にした前例があり、
  State Storeも同じ方針を踏襲する

## 決定

### インターフェースは`typing.Protocol`を使う(ADR-0002と同じ方針)

`GitLabAdapter`(ADR-0002)と同様、`abc.ABC`ではなく`typing.Protocol`(構造的部分型)を採用する。
将来のPostgreSQL実装(M3-5)が本パッケージへの継承を必須にされず、同じメソッド形状を満たすだけで
差し替えられるようにするため。`@runtime_checkable`を付け、テスト・呼び出し側の防御的チェックに
`isinstance`を使えるようにする。

`StateStore` Protocol(`src/gitlab_ai_platform/store/protocol.py`)は以下の4メソッドのみを持つ:
`find` / `create` / `update_status` / `close`。GitLab Adapterのような読み取り/書き込みの分離は
行わない(State Storeの操作はどれも「状態の記録・照会」という単一の関心事であり、呼び出し側を
権限単位で分ける理由がないため)。

### 二重レビュー防止は「一意制約違反を`DuplicateReviewError`に変換する」という契約で表現する

`create`メソッドは、同一の`(project, mr_iid, commit_sha)`に対して既にレコードが存在する場合、
実装が持つDB側の一意制約(SQLite実装ではPRIMARY KEY)を頼りに`DuplicateReviewError`を送出する。
呼び出し側(MR Poller, M1-5)は「未処理commitかどうか」を`find`で事前確認できるが、複数プロセス・
複数スレッドからの同時実行下でも二重起票が起きないよう、最終的な防止線はDBの一意制約に置く
(アプリケーション側の`find`→`create`の間のTOCTOUを、DB制約で確実に塞ぐ)。

### スキーマ: `(project, mr_iid, commit_sha)`をPRIMARY KEYとする

SQLite実装(`src/gitlab_ai_platform/store/sqlite.py`)は以下のテーブルを持つ:

```sql
CREATE TABLE review_records (
    project TEXT NOT NULL,
    mr_iid INTEGER NOT NULL,
    commit_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    reviewed_at TEXT,
    result_path TEXT,
    PRIMARY KEY (project, mr_iid, commit_sha)
)
```

複合PRIMARY KEYを使い、`(project, mr_iid, commit_sha)`の一意性をSQLite自体に保証させる
(アプリケーションコード側で別途一意性チェックを書かない)。ANSI標準のDDLに近い形にとどめ、
SQLite固有の型システム(動的型付け)に依存する記述は避けているため、将来PostgreSQLへ
移行する際もほぼそのまま使える見込み(実際の移行はM3-5で検証する)。

### `status`は`PENDING` / `RUNNING` / `DONE` / `FAILED`の4値とする

`docs/architecture.md`の「新規に追加されるレイヤー」節にあるJob状態機械(M3-1: `PENDING`
`RUNNING` `WAITING_HUMAN` `DONE` `FAILED`)とは別物。State StoreはJob抽象そのものではなく、
MVPのレビュー1回分の状態のみを扱うため、人間の介在待ちを表す`WAITING_HUMAN`はM1時点では
持たない(MVPのデータフローに人間待ち状態が存在しないため)。M3-1でJob抽象を導入する際に
State Storeとの関係を再整理する。

### `reviewed_at`は`datetime`型で公開し、DBにはISO 8601文字列で保存する

SQLiteは`datetime`型を持たないため、実装内部ではISO 8601文字列(TEXT)として保存する。
呼び出し側に公開する`ReviewRecord.reviewed_at`は`datetime | None`とし、DBの保存形式という
実装詳細を呼び出し側から隠す(GitLab Adapterが`types.py`でREST APIのレスポンス構造を
透過させないのと同じ方針)。

### DBアクセスは標準ライブラリの`sqlite3`のみを使う

ADR-0001が許可する外部依存は`requests`/`pytest`のみ。SQLAlchemy等のORMは導入しない。
スキーマが単一テーブル・4メソッドのみと小さく、生SQLで十分見通せるため
(将来PostgreSQL実装(M3-5)を追加する際、ORM導入が必要かどうかはその時点で再検討する)。

## 却下した選択肢

- **`abc.ABC`による抽象基底クラス**: ADR-0002と同じ理由(構造的部分型の方が「同じ口に嵌める」
  要件に合う)で見送り。
- **読み取り/書き込みでProtocolを分ける(`GitLabReader`/`GitLabWriter`のように)**: GitLab
  Adapterは「PATスコープが異なる呼び出し元がありうる」という理由で分けたが、State Storeには
  そのような権限の非対称性がない。分けても呼び出し側の恩恵がないため単一のProtocolとした。
- **SQLAlchemy等のORMを導入**: 現時点のスキーマ規模では生SQLで十分であり、ADR-0001の
  「依存は最小限」方針を優先。
- **`create`と`update_status`を1つの`upsert`メソッドに統合**: 二重レビュー防止という
  State Storeの主要な責務(`docs/architecture.md`)を、`create`が一意制約違反を明示的な
  例外として表出させる形で型シグネチャ上に残したかったため、意図的に分けた。`upsert`だと
  「新規作成」と「更新」が呼び出し側から区別できなくなり、二重起票を防ぐ責務が呼び出し側の
  実装依存になってしまう。

## 影響

- MR Poller(M1-5)は`StateStore`(Protocol型)にのみ依存し、`find`で未処理commitを検出、
  `create`でレビュー起票、`update_status`で結果を記録する形で実装する。
- 将来のPostgreSQL実装(M3-5)は本ADRのProtocolとスキーマ方針(複合PRIMARY KEYによる一意制約)を
  満たす形で追加すれば、呼び出し側の変更なしに差し替えられる。
