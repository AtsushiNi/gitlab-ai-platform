# ADR-0009: CLI 常駐(watch)モードの設計

- Issue: [#39](https://github.com/AtsushiNi/gitlab-ai-platform/issues/39) (M1-11)
- 状態: 決定

## 背景・制約

- `docs/architecture.md`により、CLIの責務は「単発レビュー実行(デバッグ・プロンプト改善用)と、
  常駐(watch)モードの入口。graceful shutdown・多重起動防止」。「オーケストレーション
  (Job間の遷移)はしない」ことが境界。
- M1-5(MR Poller)・M1-10(CLI単発実行)により、`MrPoller`(未処理commitの検出・起票)と
  `execute_review`(1件のレビュー実行パイプライン)がそれぞれ独立に実装・テスト済み。
  watchモードはこの2つを「定期的に検出→都度実行」という形で結線するだけの薄い層になる想定
  ([ADR-0008](0008-cli-single-run-design.md)「影響」節に予告あり)。
- `MrPoller.run(interval_seconds, stop_event)`(M1-5時点)は`poll_once`を繰り返すだけで、
  検出した`DetectedReview`ごとに何かを実行するフックを持っていなかった(意図的に
  Pollerは`GitLabReader`/`StateStore`以外の型を知らない設計だったため)。

## 決定

### `MrPoller.run`に`on_detected`コールバックを追加する

`poller/poller.py`の`run`に`on_detected: Callable[[DetectedReview], None] | None = None`を
追加した。各サイクルで新たに起票された`DetectedReview`ごと(`result.created`の順)に呼ぶ。
ポーリング間隔・`stop_event`によるループ制御という既存の責務はPollerに残したまま、
「検出後に何をするか」だけを呼び出し側(CLI watchモード)に委譲する。これにより:

- Pollerは引き続き`GitLabReader`/`StateStore`以外の型(Runner/Workspace Managerの例外型等)
  を一切知らなくてよい(`docs/architecture.md`の依存境界を維持)
- `on_detected`が送出する例外は`run`が握りつぶさずそのまま外へ伝播する。1件のレビュー失敗を
  継続可能なエラーとして扱うかどうかの判断はCLI側(下記)に委ねる

### `execute_review`と同じ「パイプライン本体/合成ルート分離」パターンをwatchモードにも適用する

`cli/watch.py`に3つの関数を用意した([ADR-0008](0008-cli-single-run-design.md)の
`execute_review`/`run_single_review`分離をそのまま踏襲):

- `build_on_detected(adapter, workspace, runner, store, config)`: `DetectedReview`を受け取り
  `execute_review`(M1-10)を呼ぶコールバックを組み立てる。既知のパイプライン例外
  (`GitLabAdapterError`/`WorkspaceError`/`RunnerError`/`ReviewError`/`StateStoreError`)は
  ログに記録して握りつぶし、それ以外の想定外の例外は再送出する(下記「1件のレビュー失敗は
  ログに記録して継続する」参照)
- `run_watch_loop(adapter, workspace, runner, store, config, stop_event=...)`: `MrPoller`と
  `build_on_detected`を結線するパイプライン本体。4つの依存先はすべてProtocol型
  (`GitLabReader`/`WorkspaceManager`/`ClaudeCodeRunner`/`StateStore`)の引数として受け取り、
  テストは手書きフェイクを注入して行う(実GitLab・実git・実Claude Code subprocessには
  一切繋がない、CLAUDE.mdのテスト方針)
- `run_watch(config, stop_event=...)`: `config`から具象実装(REST/git/subprocess/SQLite)を
  組み立て、`ProcessLock`を取得してから`run_watch_loop`に委譲する合成ルート。CLI(`cli.main`)
  はこちらを呼ぶ

### 1件のレビュー失敗はログに記録して継続する。想定外の例外はプロセスを落とす

`build_on_detected`は既知のパイプライン例外5種類だけを`except`で捕まえてログに記録し、
次のMR・次のサイクルの処理を続ける。State Store側は`execute_review`が既に`FAILED`へ
更新済みのため、同じレコードを自動リトライすることはない(MR Pollerが既存レコードを
「処理済み」として無視する、という既存の挙動のまま)。

一方、上記5種類に属さない想定外の例外(バグ)は握りつぶさず`run_watch_loop`の外へ伝播させ、
プロセスを終了させる。「常駐モードなのに1つのバグで全体が落ちるのは過剰では」という
懸念は検討したが、以下の理由で採用しなかった:

- このCLIは`docs/architecture.md`の「Windows/Linuxの分担」により、Windows上で人間が
  近くにいる運用が前提(M3以降のLinux/Docker上の無人実行とは異なる)。プロセスが
  target外の例外で終了すればターミナル上で即座に気づける
- リポジトリ全体でも「予期しない例外は握りつぶさず伝播させる」方針が一貫している
  (`cli/single_run.py`の`except Exception`も、ログ・状態更新後に必ず再送出する形でのみ
  使われており、真に無視する`except Exception: pass`は存在しない)
- 自動リトライ・監視・再起動の仕組み(systemd等)を整えるのはM3以降のLinux/Docker移行後の
  スコープであり、MVPで先回りして「バグを握りつぶして動き続ける」仕組みを作ると、
  むしろ異常に気づきにくくなる

### 多重起動防止はOSのアドバイザリロック(`ProcessLock`)で行う

`cli/lock.py`に`ProcessLock`を新設した。POSIXでは`fcntl.flock`、Windowsでは
`msvcrt.locking`をロックファイルに対して使う。`state_db_path`と同じディレクトリに
`<db名>.lock`として配置し(ロック専用の設定項目は増やさない)、`run_watch`が`with`文で
取得・解放する。

ロックはファイルディスクリプタに紐づくOSレベルの機構のため、プロセスが異常終了しても
OSがプロセス終了時に自動的に解放する。これにより「前回異常終了時のロックファイルが
残ったまま次回起動できなくなる」というデッドロックが起きない。取得できなければ
`AlreadyRunningError`を送出し、`cli.main`が`EXIT_ALREADY_RUNNING`(16)へ変換する。

### SIGINT/SIGTERMハンドラの登録は`cli/main.py`が担い、`stop_event`経由で伝える

`_install_shutdown_handler`(`cli/main.py`)が`signal.signal`でSIGINT/SIGTERM両方に
ハンドラを登録し、シグナル受信時に`threading.Event`をセットするだけの薄いハンドラにする
(重い処理をシグナルハンドラ内でしない、というPythonの一般的な作法)。実行中のサイクルの
完了を待ってから止める判断自体は`MrPoller.run`(ループ本体)側に委ねる。ハンドラは
`_run_watch_command`の`finally`で必ず元へ戻す(プロセス終了直前とはいえ、テスト内で
`main()`を繰り返し呼ぶ際にハンドラがグローバルに残り続けるのを防ぐため)。

### `execute_review`の依存構築ロジック(`_build_workspace_manager`)を公開する

`cli/single_run.py`の`_build_workspace_manager`(GitLab認証込みの`GitWorkspaceManager`
組み立て)を`build_workspace_manager`として公開した。`run_single_review`と`run_watch`の
両方が同じcredential helper設定を再現する必要があり、認証まわりのロジックを2箇所に
複製するとPAT漏洩のリスクに直結するため、既存の実装をそのまま再利用する形にした。

## 却下した選択肢

- **PIDファイル+存在チェック方式の多重起動防止**: 前回異常終了時のPIDファイルが残った
  まま次回起動できなくなる、PIDが再利用されると別プロセスを誤って「実行中」と判定する、
  といった問題が起きやすい。OSのアドバイザリロック(ファイルディスクリプタに紐づき、
  プロセス終了時に自動解放される)を採用した。
- **State Store(SQLite)側の排他制御だけで多重起動を防ぐ**: `store.create`の一意制約は
  同一commitの二重レビューは防げるが、2プロセスが同時に起動すること自体は防げず、
  GitLab APIへの重複ポーリングや無駄なClaude Code起動が発生する。プロセスレベルの
  ロックを別途設けた。
- **`MrPoller.run`自体に`execute_review`相当の呼び出しを直接書く**: Pollerが
  Runner/Workspace Managerの例外型を知ることになり、「PollerはGitLabReader/StateStore
  以外に依存しない」という既存の境界(ADR-0007)が崩れる。`on_detected`コールバックとして
  外側から注入する形にした。
- **1件のレビュー失敗も含めすべての例外を握りつぶして継続する**: 「1件のバグでwatch
  プロセス全体が止まるのは大袈裟」という誘惑はあったが、上記の通りこのCLIの運用前提
  (人間が近くにいる)と、想定外の例外を握りつぶさないというリポジトリ全体の方針を優先した。

## 影響

- `poller/poller.py`の`run`のログ呼び出し(`poller.cycle_completed`)で、`extra`の
  キーに`"created"`を使っていたのを`"created_count"`へ変更した(`"errors"`も
  `"error_count"`へ)。`"created"`は`logging.LogRecord`の予約属性(タイムスタンプ)と
  衝突しており、ルートロガーがINFO以上で有効な状態(`cli.main`の既定)でこの行が
  実行されると必ず`KeyError`で例外になる潜在バグだった。M1-11でwatchモードを実際に
  動かすテストを書いて初めて顕在化したため、本Issueの一部として修正した。
- M1-12(MVPのE2E動作確認)は、`watch`サブコマンドを実際の社内GitLabに対して起動し、
  この一連の結線(検出→レビュー実行→保存)を通しで確認することになる。
- 将来Job層(M3以降)が挿入される際、`build_on_detected`相当の「検出→実行」の結線ロジックは
  Job Queueへの投入に置き換わる想定(`docs/architecture.md`「MVP → AI Platformへの成長パス」)。
