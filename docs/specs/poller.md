# MR Poller

- 実装場所: `src/gitlab_ai_platform/poller/`
- 対応Issue: [#33](https://github.com/AtsushiNi/gitlab-ai-platform/issues/33) (M1-5)
- 関連ADR: [ADR-0007](../adr/0007-mr-poller-design.md)
- ステータス: 実装済み

## 責務

対象プロジェクトを定期的に(30〜60秒間隔、間隔は呼び出し側から指定)走査し、
`レビュー待ち`ラベルが付いたMRを抽出する。State Storeと突き合わせて
`(project, mr_iid, commit_sha)`が未処理なものを検出し、State Storeにレコードを
作成することで「起票」する。

## 前提と非対象

- 前提:
  - GitLab Adapter(M1-1/M1-2、`gitlab_adapter.protocol.GitLabReader`)とState Store
    (M1-4、`store.protocol.StateStore`)がどちらもProtocol型として利用可能であること。
    Pollerはこの2つのProtocol型にのみ依存し、具象実装(`GitLabRestAdapter`/
    `SqliteStateStore`)には直接依存しない
  - 呼び出し側(CLI, M1-10/11)が対象プロジェクト一覧・レビュー待ちラベル名・
    ポーリング間隔を`config`(`gitlab_ai_platform.config.Config`)から組み立てて渡すこと
- 非対象:
  - GitLabへの書き込みは一切しない。コンストラクタの型ヒントも読み取り専用の
    `GitLabReader`のみを受け付け、書き込み操作(`GitLabWriter`)は登場しない
  - レビューの実行(Workspace Manager準備→Claude Code Runner起動→Review解析)はしない。
    「起票する」とはState Storeにレコードを作成することのみを指す。実行のトリガーは
    M1-12(E2E結線)で後続処理として追加される
  - Webhookは当面採用しない(MVP時点)。走査対象プロジェクトはPoller構築時に渡された
    一覧固定で、実行中の動的な追加/削除は扱わない
  - プロセスのgraceful shutdown(シグナルハンドリング)・多重起動防止はしない。
    `run`は`stop_event`(`threading.Event`)を受け取って停止するだけで、シグナルの
    登録自体はCLI(M1-10/11)の責務([ADR-0007](../adr/0007-mr-poller-design.md))
  - 検出した`DetectedReview`に対して実際にレビューを実行する(Workspace Manager準備→
    Claude Code Runner起動→Review解析)処理自体は持たない。`run`の`on_detected`
    コールバックはあくまで「検出をどう伝えるか」のフックであり、レビュー実行の結線・
    エラー処理はCLI watchモード(M1-11、`cli/watch.py`)の責務
    ([ADR-0009](../adr/0009-cli-watch-design.md))

## 公開インターフェース

実装場所: `src/gitlab_ai_platform/poller/poller.py`。`typing.Protocol`は使わず、
`GitLabReader`/`StateStore`という既存の2つのProtocolを組み合わせる具象クラス
([ADR-0007](../adr/0007-mr-poller-design.md)「Poller自身のための`protocol.py`は作らない」)。

```python
import threading
from collections.abc import Callable, Sequence

from gitlab_ai_platform.gitlab_adapter import GitLabReader
from gitlab_ai_platform.poller import DetectedReview
from gitlab_ai_platform.store import StateStore


class MrPoller:
    def __init__(
        self,
        adapter: GitLabReader,
        store: StateStore,
        projects: Sequence[str],
        *,
        review_label: str,
    ) -> None:
        """走査対象プロジェクト一覧とレビュー待ちラベル名を固定して構築する。"""

    def poll_once(self) -> "PollResult":
        """全対象プロジェクトを1回走査し、新規起票と継続可能なエラーをまとめて返す。"""

    def run(
        self,
        *,
        interval_seconds: int,
        stop_event: threading.Event | None = None,
        on_detected: Callable[[DetectedReview], None] | None = None,
    ) -> None:
        """`interval_seconds`間隔で`poll_once`を繰り返す。`stop_event`がセットされると、
        実行中のサイクル完了後に停止する。`on_detected`を渡すと、そのサイクルで新たに
        起票された`DetectedReview`ごと(`result.created`の順)に呼ぶ(M1-11、
        `ADR-0009`)。`on_detected`が送出する例外は`run`の外へそのまま伝播する。"""
```

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/poller/types.py`。

| 型 | フィールド | 補足 |
|---|---|---|
| `DetectedReview` (frozen dataclass) | `project: str`, `mr_iid: int`, `commit_sha: str` | 1回のポーリングで新たにState Storeへ起票されたレビュー |
| `PollError` (frozen dataclass) | `project: str`, `mr_iid: int \| None`, `message: str` | 走査を継続可能なエラー。`mr_iid`はプロジェクト単位の走査自体が失敗した場合に`None` |
| `PollResult` (frozen dataclass) | `created: tuple[DetectedReview, ...]`, `errors: tuple[PollError, ...]` | `poll_once`1回分の結果 |

MRやレビュー記録そのものの型は、GitLab Adapter(`gitlab_adapter.types.MergeRequest`)・
State Store(`store.types.ReviewRecord`)の型をそのまま再利用し、Poller独自の型を
作り直さない。

## 処理の流れ

1. `projects`に含まれる各プロジェクトについて、`adapter.list_merge_requests(project,
   labels=(review_label,))`で`レビュー待ち`ラベル付きMRを取得する
   - 取得が`GitLabAdapterError`で失敗した場合、そのプロジェクトの走査を打ち切り
     `PollError(project, mr_iid=None, message=...)`を記録して次のプロジェクトへ進む
2. 取得できた各MRについて、`store.find(mr.project, mr.iid, mr.sha)`で
   `(project, mr_iid, commit_sha)`が既に記録済みかを確認する
   - 記録済みなら何もしない(既に起票済み、または過去に処理済みのcommit)
   - 未記録なら`store.create(mr.project, mr.iid, mr.sha)`でレコードを作成し、
     `DetectedReview`として`PollResult.created`に含める
3. `create`が`DuplicateReviewError`を送出した場合(`find`から`create`までの間の競合)は
   無視する。それ以外の`StateStoreError`が発生した場合は、そのMRの処理を諦め
   `PollError(project, mr_iid, message=...)`を記録して次のMRへ進む

## エラー時の振る舞い

Pollerは独自の例外型を持たない。GitLab Adapter(`gitlab_adapter.errors`)・
State Store(`store.errors`)の例外型を握り、継続可能なものは`PollResult.errors`に
集約してログ(`poller.project_scan_failed` / `poller.ticket_creation_failed`)に残し、
呼び出し側には例外として伝播させない。

| 例外 | 発生元 | Pollerの扱い |
|---|---|---|
| `GitLabAdapterError`(`GitLabApiError`等) | `adapter.list_merge_requests` | そのプロジェクトの走査を打ち切り、`PollError(mr_iid=None)`として記録して継続 |
| `DuplicateReviewError` | `store.create` | 無視する(既に起票済みとして扱う。ログには`debug`レベルで記録) |
| `DuplicateReviewError`以外の`StateStoreError` | `store.find` / `store.create` | そのMRの処理を打ち切り、`PollError(mr_iid=...)`として記録して継続 |

`run`自体は例外を送出しない前提(`poll_once`が内部で継続可能なエラーを吸収するため)。
`store`/`adapter`の初期化失敗など、継続不能な設定不備は呼び出し側(CLI)が
Poller構築前に検知する。

## テスト方針

実装場所: `tests/gitlab_ai_platform/poller/`(`src/`をミラー、
[ADR-0001](../adr/0001-repository-structure.md))。

- `test_types.py`: `DetectedReview`/`PollResult`のイミュータブル性(`frozen=True`)、
  `PollError.mr_iid`がプロジェクト単位の失敗で`None`になりうることを検証する
- `test_poller.py`: `GitLabReader`/`StateStore`を満たす手書きフェイク(`unittest.mock`は
  使わない。既存モジュールのテストと同じ方針)に対して`MrPoller`を実行し、実サービスへは
  繋がない。以下を検証する:
  - 複数プロジェクトを走査し、未処理commitがそれぞれ起票されること
  - `list_merge_requests`に`labels=(review_label,)`が渡ること
  - State Storeに既に記録済みの`(project, mr_iid, commit_sha)`はスキップされること
  - 同一MRでも新しい`commit_sha`(再push)は別レコードとして起票されること
  - 1プロジェクトの走査失敗が他プロジェクトの走査を止めないこと、失敗が`PollResult.errors`に
    記録されること
  - `create`が`DuplicateReviewError`を送出した場合、`created`にも`errors`にも計上せず
    無視すること
  - `create`が(`DuplicateReviewError`以外の)`StateStoreError`を送出した場合、そのMRだけ
    `errors`に記録し、他のMRの処理は継続すること
  - `run`が`stop_event`のセットで停止し、少なくとも1回`poll_once`相当の処理を行うこと
  - `on_detected`を渡すと、そのサイクルで新たに起票された`DetectedReview`ごとに
    呼ばれること。省略時は呼び出されないだけで例外にならないこと。`on_detected`が
    送出した例外が`run`の外へそのまま伝播すること

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表のMR Poller行
- [ADR-0007: MR Poller の設計](../adr/0007-mr-poller-design.md)
- [ADR-0009: CLI 常駐(watch)モードの設計](../adr/0009-cli-watch-design.md) — `on_detected`
  を使ってレビュー実行パイプラインを結線するCLI側の設計
- [cli.md](cli.md) — `on_detected`経由でこのPollerを結線するCLI(`watch`サブコマンド)の仕様
- ソースコード: `src/gitlab_ai_platform/poller/`(`poller.py` / `types.py` / `__init__.py`)
