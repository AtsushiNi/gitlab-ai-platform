# ADR-0022: Runner のプロセス分離(Runner Dispatcher)の設計

- Issue: [#93](https://github.com/AtsushiNi/gitlab-ai-platform/issues/93) (M3-3)
- 状態: 決定

## 背景・制約

- [ADR-0016](0016-job-abstraction.md)(M3-1)は`JobRepository`の基本5メソッドを確定させ、既存レビュー処理を「起票直後に同一プロセス内で同期処理する」経路に再構成した。[ADR-0017](0017-job-queue.md)(M3-2)は`claim`/`heartbeat`/`complete`/`fail`/`list_dead_letters`を追加したが、実配線(Runner Dispatcher)は本Issueのスコープとした
- 「別プロセス/別ホストで動く実行体」を新設することが要件。同一Job DBに対して複数のRunner Dispatcherプロセスが同時に`claim`を呼び合う状況が前提になる
- 既存の`execute_review_job`(同期処理経路)は変更せず、**別の新しい経路(Runner Dispatcher)を追加する**

## 決定

### 新しいCLIサブコマンド`worker`として、`RunnerDispatcher`(claim実行ループ)を追加する

`review`/`watch`と同じ`cli/main.py`のサブコマンドとして`worker`を追加する(`cli/dispatcher.py`に実装)。1台のホストで複数`worker`プロセスを起動する、複数ホストでそれぞれ起動する、のいずれも同じコマンドで実現できる。

### Job受け渡しプロトコルは「`JobType` → `JobHandler`のディスパッチテーブル」とする

```python
JobHandler = Callable[[Job], dict[str, Any] | None]


def build_job_handlers(
    adapter, workspace, runner, store, config
) -> dict[JobType, JobHandler]: ...
```

`RunnerDispatcher`は`JobRepository`と`Mapping[JobType, JobHandler]`だけを知っていればよく、種別固有のロジックを一切知らない。M4で新しいJobHandlerを追加する際、`RunnerDispatcher`自体は無改修のまま辞書にエントリを足すだけで済む。`job_types`を明示指定しない場合、`handlers`に登録済みの種別のみを対象にする(未実装種別のうっかりclaimを避ける)。

### 依存の構成は`run_single_review`と同じ合成ルートパターンを再利用する

新しい認証・設定の概念は導入しない。GitLab PAT・Workspaceルート・DBパスは既存の`Config`フィールドをそのまま使う。

### 排他は`ProcessLock`ではなく`JobRepository.claim`のアトミック性に委ねる(多重起動防止を意図的に行わない)

`watch`は`ProcessLock`で多重起動を防止するが、`worker`は正反対で**複数プロセス/複数ホストの同時起動を前提とし、意図的に多重起動を許可する**。排他は`claim`のアトミックなUPDATE文([ADR-0017](0017-job-queue.md))に委ねる。

### `heartbeat`はJobを処理する間、専用スレッドで一定間隔ごとに呼ぶ

既定120秒(可視性タイムアウト既定600秒の約1/5)。`LeaseLostError`を検知した場合はheartbeatスレッドのみ終了し、ハンドラ本体は最後まで実行させる。

### `complete`/`fail`の呼び分けは、`handler`が送出した例外の型で決める

- 正常終了 → `complete`
- `NotImplementedError`(未対応JobType) → `fail(..., retry=False)`(即デッドレター化)
- その他の例外 → `fail(..., retry=True)`(リトライ判定はJob Repositoryに委ねる)

1件のJobの失敗は他のJobの処理を止めない。

### `claim`が空振りした場合のみ`poll_interval_seconds`だけ待つ

専用のイベント通知機構(DBのLISTEN/NOTIFY等)は導入しない。

## 却下した選択肢

- **`JobType`値ごとの`if`/`elif`分岐**: `RunnerDispatcher`本体がJobType追加のたびに変更対象になり、ADR-0016の基本方針に反する
- **`config.toml`に`[dispatcher]`セクションを追加する**: 実行チューニング値であり、真に恒久設定が必要になった時点で改めて検討する
- **`ProcessLock`を`worker`にも適用する**: スケールアウトができなくなる
- **想定外の例外を`watch`と同じくプロセスを落とす方針にする**: `worker`は無人・ヘッドレス運用が前提であり、1件の失敗のたびに全体を落とすと不利益が大きい。リトライ/デッドレター機構に委ねる方針にした
- **1回の起動で1件だけ処理して終了する設計**: `--once`オプションとして残しつつ、既定は常駐ループとした(プロセス起動コストの積み上がりを避けるため)

## 影響

- `cli/dispatcher.py`(新規)・`cli/main.py`(`worker`サブコマンド追加)を変更した
- `job/`パッケージは無改修。M3-4/M3-5/M3-7/M3-8と並行して進めてもシグネチャ競合は起きない
- 既存の`review`/`watch`サブコマンドの経路は変更しない
- M4(`issue-analysis`/`design`/`implement`)は、`build_job_handlers`に対応するhandlerを追加するだけで`worker`から実行できる
- M3-4(Docker実行環境)は、この`worker`サブコマンドをコンテナのエントリポイントにする形で着手できる
