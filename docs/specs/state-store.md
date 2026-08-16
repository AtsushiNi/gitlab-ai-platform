# State Store

- 実装場所: `src/gitlab_ai_platform/store/`
- 対応Issue: [#32](https://github.com/AtsushiNi/gitlab-ai-platform/issues/32) (M1-4、スキーマ設計・SQLite実装)、
  [#80](https://github.com/AtsushiNi/gitlab-ai-platform/issues/80) (M2-1、並行アクセスの安全性)
- 関連ADR: [ADR-0003](../adr/0003-state-store-interface.md)、
  [ADR-0014](../adr/0014-parallel-review-execution.md)
- ステータス: 実装済み(Protocol定義 + SQLite実装)

## 責務

`(project, mr_iid, commit_sha)`単位でレビュー状態(`status` / `reviewed_at` / 結果パス)を
記録・照会し、二重レビューを防ぐ。実装(SQLite, M1-4。将来PostgreSQLへ移行, M3-5)を
`typing.Protocol`で抽象化し、呼び出し側(MR Poller等)を具象実装から切り離す。

## 前提と非対象

- 前提:
  - 呼び出し側(MR Poller, M1-5)は`StateStore`のProtocol型だけを見て実装し、具象クラス
    (`SqliteStateStore`)に直接依存しない
  - レビュー結果本体(JSON/Markdown)は`reviews/<project>/<mr_iid>/<sha>/`(`docs/architecture.md`)に
    別途保存される。State Storeはその保存先パス(`result_path`)のみを記録し、結果の中身は扱わない
  - M2-1(#80)以降、`SqliteStateStore`の同一インスタンスは複数のワーカースレッドから同時に
    呼ばれる(`ReviewWorkerPool`、`docs/specs/cli.md`)。`find`/`create`/`update_status`/`close`は
    すべて内部で`threading.RLock`により直列化されており、呼び出し側は追加の排他制御なしに
    同一インスタンスを複数スレッドで共有できる([ADR-0014](../adr/0014-parallel-review-execution.md)参照)
- 非対象:
  - ビジネスロジック(レビューするか否かの判断、`レビュー待ち`ラベルの走査等)は持たない。
    それらはMR Poller(M1-5)の責務
  - Job状態機械(M3-1、`PENDING`/`RUNNING`/`WAITING_HUMAN`/`DONE`/`FAILED`)そのものではない。
    MVPのレビュー1回分の状態のみを扱う([ADR-0003](../adr/0003-state-store-interface.md)参照)
  - PostgreSQL実装は本Issueの対象外(M3-5で追加する)

## 公開インターフェース

`StateStore`を`@runtime_checkable`な`typing.Protocol`として定義する。
実装場所: `src/gitlab_ai_platform/store/protocol.py`。

```python
from datetime import datetime
from typing import Protocol, runtime_checkable

from .types import ReviewRecord, ReviewStatus


@runtime_checkable
class StateStore(Protocol):
    def find(self, project: str, mr_iid: int, commit_sha: str) -> ReviewRecord | None:
        """指定commitのレビュー記録を返す。存在しなければ`None`(未処理commitとして扱える)。"""
        ...

    def create(
        self,
        project: str,
        mr_iid: int,
        commit_sha: str,
        *,
        status: ReviewStatus = ReviewStatus.PENDING,
    ) -> ReviewRecord:
        """新しいレビュー記録を作成する。既に存在する場合は`DuplicateReviewError`を送出する。"""
        ...

    def update_status(
        self,
        project: str,
        mr_iid: int,
        commit_sha: str,
        status: ReviewStatus,
        *,
        reviewed_at: datetime | None = None,
        result_path: str | None = None,
    ) -> ReviewRecord:
        """既存のレビュー記録の状態を更新する。存在しない場合は`RecordNotFoundError`を送出する。"""
        ...

    def close(self) -> None:
        """DB接続等の内部リソースを解放する。"""
        ...
```

SQLite実装: `src/gitlab_ai_platform/store/sqlite.py`の`SqliteStateStore`。
`SqliteStateStore(db_path: Path | str = ":memory:")`でDBファイルパス(またはインメモリ)を指定して構築する。

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/store/types.py`。

| 型 | フィールド | 補足 |
|---|---|---|
| `ReviewStatus` (Enum) | `PENDING` / `RUNNING` / `DONE` / `FAILED` | レビューの進行状態 |
| `ReviewRecord` (frozen dataclass) | `project: str`, `mr_iid: int`, `commit_sha: str`, `status: ReviewStatus`, `reviewed_at: datetime \| None = None`, `result_path: str \| None = None` | `(project, mr_iid, commit_sha)`が一意キー |

SQLiteスキーマ(`review_records`テーブル、複合PRIMARY KEY):

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

`reviewed_at`はDBにISO 8601文字列で保存し、呼び出し側には`datetime`として返す
([ADR-0003](../adr/0003-state-store-interface.md))。

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/store/errors.py`。

- `StateStoreError(Exception)` — State Store経由の操作が失敗したことを表す基底例外。
  呼び出し側はまずこの型でcatchすればState Store起因の失敗を一括して扱える
- `DuplicateReviewError(StateStoreError)` — `create`が同一の`(project, mr_iid, commit_sha)`に
  対して呼ばれ、二重レビュー防止の一意制約に違反したことを表す。呼び出し側(MR Poller)は
  これを「既にレビュー起票済み」として扱い、リトライせず無視してよい
- `RecordNotFoundError(StateStoreError)` — `update_status`が存在しないレコードに対して
  呼ばれたことを表す。呼び出し側は先に`create`を呼ぶべきだったことを意味し、通常は
  呼び出し側のバグとして扱う(リトライで解決しない)

## テスト方針

実装場所: `tests/gitlab_ai_platform/store/`(`src/`をミラー、[ADR-0001](../adr/0001-repository-structure.md))。

- `test_types.py`: `ReviewRecord`のデフォルト値・イミュータブル性(`frozen=True`)、
  `ReviewStatus`の値を検証する
- `test_errors.py`: `DuplicateReviewError`/`RecordNotFoundError`が`StateStoreError`の
  サブクラスであることを検証する
- `test_protocol.py`: `StateStore`の公開メソッド集合が`find`/`create`/`update_status`/`close`と
  完全一致することを検証する(将来メソッドが意図せず増減した場合にこのテストが落ちる)。
  Protocolを満たすダミー実装に対して`isinstance(impl, StateStore)`が`True`になることも検証する
- `test_sqlite.py`: `SqliteStateStore`をインメモリDB(`:memory:`)で実行し、実ファイル・
  実サービスへは繋がない(CLAUDE.mdのテスト方針)。以下を検証する:
  - `create`→`find`の往復でレコードが取得できること
  - 同一`(project, mr_iid, commit_sha)`への`create`が`DuplicateReviewError`になること
    (二重レビュー防止)
  - 同一MRでも異なる`commit_sha`なら別レコードとして作成できること(再レビューの検出)
  - `update_status`が`status`/`reviewed_at`/`result_path`を永続化すること
  - 存在しないレコードへの`update_status`が`RecordNotFoundError`になること
  - `(project, mr_iid, commit_sha)`の組み合わせが異なればレコードが独立していること
  - (M2-1) 多数のスレッドが同時に`create`/`update_status`を呼んでも例外を送出せず、
    全レコードが正しく記録されること(`threading.RLock`による直列化の回帰テスト)

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表のState Store行
- [ADR-0003: State Store のインターフェースとスキーマ設計](../adr/0003-state-store-interface.md)
- [ADR-0014: 並列レビュー実行の設計](../adr/0014-parallel-review-execution.md) —
  `threading.RLock`による直列化の設計判断
- ソースコード: `src/gitlab_ai_platform/store/`
  (`protocol.py` / `types.py` / `errors.py` / `sqlite.py` / `__init__.py`)
