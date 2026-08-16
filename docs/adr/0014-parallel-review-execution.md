# ADR-0014: 並列レビュー実行の設計

- Issue: [#80](https://github.com/AtsushiNi/gitlab-ai-platform/issues/80) (M2-1)
- 状態: 決定

## 背景・制約

- M1完了時点(`docs/architecture.md`「データフロー(MVP)」)で、レビュー待ちMRの検出から
  実行までの一連の処理(MR Poller → Workspace Manager → Claude Code Runner → Review保存 →
  State Store更新)は、常駐(watch)モード(`cli/watch.py`、[ADR-0009](0009-cli-watch-design.md))
  において1件のMRを最後まで処理してから次のMRに着手する**逐次実行**だった
  (`MrPoller.run`が1サイクルで検出した`DetectedReview`ごとに`on_detected`を同期的に呼ぶ形)。
  Claude Codeのヘッドレス実行(`runner.run`)は数分〜数十分かかりうるため、対象MRが複数ある
  場合の総待ち時間がMR数に比例して伸びる。
- Issue #80(M2-1)は、これを解消する「ワーカープール、同時実行数の設定、worktree/DBの競合回避、
  失敗時の隔離」を求めている(`references/タスク整理.md`)。
- `Config`(`config/models.py`)には`max_parallel`フィールドが既に存在していたが、
  `docs/operations/configuration.md`に「現時点でどのコードからも参照されていない予約フィールド」
  と明記されている通り未配線だった。本Issueで実際に使用する。
- Workspace Manager([ADR-0004](0004-workspace-manager-design.md))・State Store
  ([ADR-0003](0003-state-store-interface.md))はいずれも、この時点での並列アクセスの
  排他制御を「M2-1で必要になった時点で再検討する」として先送りしていた。

## 決定

### プロセス内のスレッドプールで並列化する(別プロセス/コンテナへの分離はしない)

`docs/architecture.md`「Windows/Linuxの分担」により、M1〜M2は人間の端末(Windows)上で完結する
運用が前提であり、プロセス分離・コンテナ隔離が必要になるのはM3以降のLinux/Docker移行後
(無人実行フェーズ)としている。M2-1の時点では`concurrent.futures.ThreadPoolExecutor`による
プロセス内スレッド並列で十分と判断した。`runner.run`(Claude Codeのsubprocess実行)・
`workspace.prepare`(git subprocess実行)はいずれもI/O待ちが支配的な処理であり、GILがあっても
並列度を得られる。

### `ReviewWorkerPool`(`cli/worker_pool.py`)を新設し、`run_watch_loop`がジョブ投入のみ行う

`build_on_detected`(1件のMRを同期的に処理するコールバックを組み立てる関数)自体は変更しない。
`run_watch_loop`(`cli/watch.py`)側で、`MrPoller.run`に渡す`on_detected`を「`ReviewWorkerPool`
への`submit`」に置き換えることで並列化する。これにより:

- `build_on_detected`の単体テスト(1件のMRを同期的に処理する契約)は無改修のまま維持できる
- `MrPoller`(`poller/poller.py`)自体は一切変更不要。「検出をどう伝えるか」のフック
  (`on_detected`)としての役割は変わらず、フックの中身(同期呼び出しか、プールへの投入か)を
  知らない([ADR-0009](0009-cli-watch-design.md)が確立した責務分離をそのまま維持)

`ReviewWorkerPool(max_workers, stop_event)`は`config.max_parallel`個までのワーカースレッドで
ジョブ(`Callable[[], None]`)を並行実行する。`submit`は即座に戻り、実行完了を待たない。

### 想定外の例外は`stop_event`の共有で合成ルートまで伝播させる

[ADR-0009](0009-cli-watch-design.md)は「1件のレビュー失敗(既知の5種類のパイプライン例外)は
ログに記録して継続する。それ以外の想定外の例外は握りつぶさずプロセスを終了させる」という方針を
既に確立している。並列化後もこの方針を維持する必要があるが、ワーカースレッド内の例外は
そのままではメインスレッド(`MrPoller.run`のループ)に伝播しない。

これを解決するため、`run_watch_loop`は`stop_event`(呼び出し側が省略した場合はここで生成する)を
`MrPoller.run`と`ReviewWorkerPool`の両方に**同じオブジェクトとして**渡す。ワーカースレッドが
既知の5種類に属さない例外を捕まえた場合、`ReviewWorkerPool`はその例外を保持しつつ
`stop_event.set()`する。これにより:

1. `MrPoller.run`のポーリングループが(実行中のサイクル完了後に)早期終了する
2. `run_watch_loop`の`finally`節で`pool.shutdown_and_reraise()`を呼び、投入済みジョブの完了を
   待ってから保持していた例外を再送出する
3. 例外は`run_watch`→`cli.main`とそのまま伝播し、[ADR-0009](0009-cli-watch-design.md)と同じ
   終了コード変換経路に乗る

`stop_event`は元々「SIGINT/SIGTERM受信時のgraceful shutdown」用に存在した合図であり、
「ワーカースレッドの致命的な失敗」という別のトリガーで同じ合図を再利用する設計とした
(専用のイベントを新設するより、既存の停止経路に素直に合流させる方がシンプルなため)。

### 失敗時の隔離: 1件のジョブの例外は他のジョブの実行を妨げない

`ReviewWorkerPool._run`は投入されたジョブを個別に`try/except`で囲み、1件の例外(既知・想定外を
問わず)が他の投入済みジョブの実行を止めないようにする。既知のパイプライン例外は
`build_on_detected`の中で既にログ記録済みで例外を外に出さないため、`ReviewWorkerPool`まで
届くのは想定外の例外のみ。`shutdown_and_reraise`は`ThreadPoolExecutor.shutdown(wait=True,
cancel_futures=True)`を使い、**既に実行が始まっているジョブは完了を待つ**(中断しない)。
未着手(まだキューに積まれているだけ)のジョブはキャンセルする。「バグを検知したら新規のジョブは
増やさないが、既に走っている他MRの処理は最後まで面倒を見る」という考え方。

### Workspace Manager: project(bare repo)単位のロックでgit操作を直列化する

`GitWorkspaceManager`(`workspace/git_workspace.py`)は、project名をキーにした
`dict[str, threading.RLock]`(`_project_locks`、生成自体は`_project_locks_guard`で保護)を持ち、
`prepare`/`discard`はそのprojectのロックを取得してから内部の実処理(`_prepare_locked`/
`_discard_worktree`)を行う。`Lock`ではなく`RLock`にしたのは、`prepare`内のディスク上限
チェック(`_ensure_disk_budget`→`collect_garbage`)が、同一project内の別MR(=同じロック)を
同一スレッド上で退避する正当なケースがあるため(単純な`Lock`だと自分自身のロック待ちで
デッドロックする。この不具合はテスト`test_prepare_evicts_oldest_worktree_when_disk_limit_reached`
で検出し、`RLock`化で修正した)。

- 同一project(=同一bare repo)への`clone`/`fetch`/`worktree prune`/`worktree add`/
  `reset --hard`が複数スレッドから同時に走らないことを保証する。bare repoの
  `.git/worktrees/`メタデータやref更新は、Gitコマンドレベルでは同時実行に対して
  必ずしも安全ではなく(実測での破損は確認していないが、公式に保証された動作でもない)、
  ロックで機構的に防ぐ方が安全側に倒せる
- 異なるprojectは別ロックのため、真に並行実行できる。projectをまたいだ全体直列化
  (単一の`threading.Lock`で`GitWorkspaceManager`全体を保護する案)は簡単だが、複数MRが
  別プロジェクトに分散している(社内でよくあるケース)場合の並列化効果を大きく損なうため
  却下した
- GC(`collect_garbage`)の非ブロッキング退避(後述)は、`RLock`であっても他スレッドからの
  `acquire(blocking=False)`は依然として失敗する(再入が許されるのはロックを保持している
  スレッド自身のみ)。デッドロック回避の設計はそのまま成り立つ
- ロックが保護するのは`prepare`/`discard`の**git操作本体のみ**。呼び出し側
  (`execute_review`)がその戻り値(`WorktreeHandle`)を使ってClaude Code Runnerを実行する
  部分(本来時間のかかる処理)はロックの外にあり、並列化の効果はここで確保される

### GC(`collect_garbage`)は退避対象のロックを非ブロッキングで試み、取れなければスキップする

`_ensure_disk_budget`/`collect_garbage`は、最終利用時刻が古い順に退避候補を並べ
(`_worktrees_sorted_by_age`)、各候補について`project_lock.acquire(blocking=False)`を試みる。
取得できなければ(他スレッドがそのprojectを操作中)その候補はスキップし、次点を試す。

**ブロッキング待ちを避けた理由(デッドロック回避)**: 仮に「取れるまで待つ」実装にすると、
以下の循環待ちが起こりうる。

```text
スレッドA: project Xのロックを保持してGC実行中 → project Yのロック待ち
スレッドB: project Yのロックを保持してprepare実行中 → (Bのprepareがディスク上限超過を検知し)
           GCを実行しproject Xの退避を試みる → project Xのロック待ち
```

A→Y、B→Xの循環待ちが揃うとデッドロックする。「取得できなければ諦めて次を試す」という
非ブロッキング方式にすることで、この種の循環待ちを構造的に起こさない設計にした。
トレードオフとして、GCがたまたま全候補を「操作中」と判定し続けた場合、実際には
すぐ空くはずのディスクを「まだ上限を超えている」と判断して`DiskLimitExceededError`を
送出しうる。MVP規模の同時実行数(既定値、後述)ではこの競合が起きる頻度は低いと判断し、
許容することにした。

### State Store: `threading.RLock`で全メソッド本体を直列化する

`SqliteStateStore`(`store/sqlite.py`)は元々1つの`sqlite3.Connection`
(`check_same_thread=False`)を複数スレッドで共有する前提だった([ADR-0003](0003-state-store-interface.md)、
実装コメント「複数プロセス・複数スレッドからの同時実行下でも二重起票が起きないことを前提に
している」)。しかし`check_same_thread=False`は「別スレッドから呼んでも例外にしない」ことしか
保証せず、SQLiteライブラリ自体が同時実行に対してどこまでスレッドセーフにビルドされているか
(serializedモードか否か)はプラットフォーム依存で、Python標準の`sqlite3`モジュールからは
確実に判定・制御できない。同時書き込みで`sqlite3.OperationalError: database is locked`の
ような非決定的な失敗が起きるリスクをSQLiteのビルド設定に依存させたくなかったため、
`threading.RLock`で`find`/`create`/`update_status`/`close`の本体を明示的に直列化した。

`Lock`ではなく`RLock`にしたのは、`update_status`が更新後の状態を返すために内部で
`find`を呼んでおり(同一スレッドからの再入)、単純な`Lock`だとデッドロックするため。

却下した代替案:

- **SQLiteのWAL(Write-Ahead Logging)モード + `busy_timeout`設定で自然な同時実行に任せる**:
  複数「コネクション」の同時アクセスには有効だが、本実装は単一コネクションを複数スレッドで
  共有する構成のままであり、コネクション自体を複数化する変更はStateStoreの実装をより
  大きく変えることになる。単一コネクション+アプリケーションロックの方が変更が小さく、
  かつプラットフォーム(Windows/macOS/Linux)によらず確実に安全と判断した
- **スレッドごとに別コネクションを持つ(コネクションプール)**: 同時実行数はそもそも
  `max_parallel`(既定値、後述)程度と小さく、コネクションプールを導入するほどの
  性能上のメリットが小さい。`(project, mr_iid, commit_sha)`の一意制約による二重起票防止
  ([ADR-0003](0003-state-store-interface.md))は単一コネクション・単一ロックのままの方が
  素直に維持できる

### Review保存(`review/index.py`)の索引追記も`threading.Lock`で直列化する

Issue本文が明示するのはworktree/DBの2つだが、並列実行後は`review.save_review`
(`review/storage.py`)経由の`append_entry`(`<reviews root>/index.jsonl`への追記)も
複数ワーカースレッドから同時に呼ばれる。OSの`O_APPEND`書き込みの原子性はプラットフォーム
依存(特にWindowsでは複数ハンドルからの同時追記で行が混ざりうる、[ADR-0004](0004-workspace-manager-design.md)の
Windows前提と同じ理由でここも無視できない)であり、モジュール内の`threading.Lock`で
プロセス内の書き込みを直列化した。複数プロセスからの同時書き込みは既存の`ProcessLock`
(`cli/lock.py`、[ADR-0009](0009-cli-watch-design.md))が別途防いでいるため、プロセス内の
排他だけで十分と判断した。

### `max_parallel`の既定値は`5`のまま変更しない

`config/loader.py`の`DEFAULT_MAX_PARALLEL = 5`は本Issue以前から存在していた値をそのまま
踏襲する。Windows上で人間の端末を使う運用規模(1人あたり同時に見るMRの数は多くても
数件程度)を踏まえ、Claude Codeのheadless実行(CPU・ネットワーク・Bedrock APIレート)を
過度に同時起動しない値として妥当と判断し、変更しなかった。

### Claude Code Runner(`SubprocessClaudeCodeRunner`)は無改修

`runner/subprocess_runner.py`はコンストラクタ引数(`log_dir`/`env`等)以外に可変の内部状態を
持たず、`run`呼び出しごとに独立したログパス(project×MR×sha×timestampで一意)へ書き込む。
複数スレッドから同時に`run`が呼ばれても状態の共有がないため、無改修で並列実行に対応できる。

## 却下した選択肢

- **別プロセス/コンテナへのジョブ分離**: `docs/architecture.md`「Windows/Linuxの分担」により、
  M1〜M2はWindows上の人間の端末で完結させる方針。プロセス分離・コンテナ隔離が要る無人実行は
  M3以降のLinux/Docker移行後のスコープ。今この複雑さを持ち込む理由がない
- **`asyncio`ベースの並行化**: 既存コード(Poller/Workspace Manager/Runner/Review)はすべて
  同期APIで書かれており、`asyncio`化は影響範囲が大きい。`subprocess`呼び出しが支配的な
  ワークロードでは、スレッドベースの並行化でも十分な効果が得られると判断した
- **`GitWorkspaceManager`全体を単一の`threading.Lock`で保護する(project単位ではなく)**:
  実装は単純だが、複数MRが別プロジェクトに分散している場合でも常に1件ずつしかgit操作が
  進まなくなり、並列化の効果がほぼ失われる。project単位のロックで異なるprojectの並列度を
  確保した
- **GCの退避候補ロック取得をブロッキング待ちにする**: 「決定」節で述べた通り、循環待ちに
  よるデッドロックのリスクがあるため非ブロッキング(取れなければスキップ)を採用した
- **`MrPoller`自体に並列実行の仕組みを組み込む**: `MrPoller`は`GitLabReader`/`StateStore`
  以外の型を一切知らないという既存の境界([ADR-0007](0007-mr-poller-design.md))を壊すことに
  なる。`on_detected`フックの実装(=CLI側)だけで並列化を完結させた

## 影響

- `cli/worker_pool.py`(新設)・`cli/watch.py`(`run_watch_loop`)・`workspace/git_workspace.py`・
  `store/sqlite.py`・`review/index.py`を変更した。`poller/poller.py`・`runner/subprocess_runner.py`・
  `cli/single_run.py`(`execute_review`本体)は無改修
- `docs/specs/workspace-manager.md`「非対象」節の「複数プロセスからの同時実行時の排他制御は
  本Issueの対象外」という記述を、本Issueで実装した内容に更新した
- `docs/specs/state-store.md`・`docs/specs/review-output.md`・`docs/specs/cli.md`に、
  それぞれのモジュールの並行アクセス安全性の記述を追加した
- `docs/operations/configuration.md`の`max_parallel`の説明を「未使用の予約フィールド」から
  実際の用途の説明に更新した
- 将来のJob層導入(M3-1〜M3-2、`docs/architecture.md`「新規に追加されるレイヤー」)では、
  `ReviewWorkerPool`相当の「同時実行数の制御」はJob Queueの並行処理能力に置き換わる想定。
  Workspace Manager/State Storeのロック方式自体は、実行主体がスレッドからプロセス/コンテナに
  変わっても「同時実行下での競合回避」という要件は変わらないため、そのまま踏襲するか、
  プロセス間ロック(ファイルロック等)へ置き換えるかをその時点で再検討する
