# ADR-0022: Runner のプロセス分離(Runner Dispatcher)の設計

- Issue: [#93](https://github.com/AtsushiNi/gitlab-ai-platform/issues/93) (M3-3)
- 状態: 決定

## 背景・制約

- [ADR-0016](0016-job-abstraction.md)(M3-1)は`JobRepository`の基本5メソッド
  (`enqueue`/`get`/`update_status`/`list_by_status`/`close`)を確定させ、既存レビュー処理を
  `execute_review_job`(`cli/single_run.py`)として「起票直後に同一プロセス内で同期処理する」
  経路に再構成した。[ADR-0017](0017-job-queue.md)(M3-2)はこの経路を変更せず、`claim`/
  `heartbeat`/`complete`/`fail`/`list_dead_letters`を**Job Repositoryのメソッドとしてのみ**
  追加し、「Runner Dispatcher側の実配線はM3-3のスコープとする」と明記していた
- `docs/architecture.md`の「MVP → AI Platformへの成長パス」表は、Claude Code Runnerについて
  「Jobとして扱われ(M3-1)、別プロセス/別ホストに分離される(M3-3)」としており、Job Queueの
  上に「別プロセス/別ホストで動く実行体(Runner Dispatcher)」を新設することが本Issueの要件
- `references/タスク整理.md`M3-3は「Job受け渡しプロトコルを定義し、Runner を別プロセス/
  別ホストで動かせるようにする」を求めている。「別プロセス/別ホスト」というだけあって、
  同一`job.db`に対して複数のRunner Dispatcherプロセス(将来は複数ホスト)が同時に`claim`を
  呼び合う状況が前提になる
- 既存の`execute_review_job`(`enqueue`直後に同期処理する経路)は`cli/single_run.py`の
  `run_single_review`(`review`サブコマンド)と`cli/watch.py`の`build_on_detected`(`watch`
  サブコマンド)から呼ばれ続けており、`references/タスク整理.md`M1-12のE2E確認等がこの経路に
  依存している。本Issueはこの経路を変更せず、**別の新しい経路(Runner Dispatcher)を追加する**
  ([ADR-0017](0017-job-queue.md)が既に明記した方針)
- ADR-0001の依存最小方針により外部依存は最小限(`requests`/`pytest`/`mcp`のみ)。新規の
  メッセージキュー・RPCフレームワークは導入しない(`claim`によるDBベースの取得で足りる)

## 決定

### 新しいCLIサブコマンド`worker`として、`RunnerDispatcher`(claim実行ループ)を追加する

`review`/`watch`/`decompose`と同じ`cli/main.py`のサブコマンドとして`worker`を追加する。
実装場所は`cli/watch.py`と対になる`cli/dispatcher.py`(パイプライン本体+合成ルートを同じ
ファイルに置く、[ADR-0008](0008-cli-single-run-design.md)以来のこのリポジトリの配置パターンを
踏襲。`execute_review`/`execute_review_job`も`cli/single_run.py`に同居している)。

```text
gitlab-ai-platform worker \
    [--worker-id ID] [--job-types TYPE [TYPE ...]] \
    [--poll-interval SECONDS] [--heartbeat-interval SECONDS] \
    [--visibility-timeout SECONDS] [--once]
```

`watch`が「MR Pollerが検出したMRを処理する」常駐プロセスであるのに対し、`worker`は
「Job Repositoryから`claim`したJobを処理する」常駐プロセスであり、検出(Poller/Webhook)と
実行(Runner)の間にJob Queueが挟まる、という`docs/architecture.md`の成長パスをそのまま
実プロセス構成に反映する。1台のホストで複数`worker`プロセスを起動する、複数ホストでそれぞれ
`worker`を起動する、のいずれも同じコマンドで実現できる(後述の「Job受け渡しプロトコル」が
プロセス/ホストを問わず同じJob DBに対して安全に競合できる設計のため)。

### Job受け渡しプロトコルは「`JobType` → `JobHandler`のディスパッチテーブル」とする

```python
JobHandler = Callable[[Job], dict[str, Any] | None]


def build_job_handlers(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
) -> dict[JobType, JobHandler]:
    """JobType → JobHandlerのディスパッチテーブルを組み立てる。

    現時点ではreviewのみ実装済み。issue-analysis/design/implement(M4)は、対応する
    handlerをこの辞書に追加するだけでRunnerDispatcher側の変更なしに配線できる。
    """
    return {
        REVIEW_JOB_TYPE: build_review_handler(
            adapter, workspace, runner, store, config
        ),
    }
```

`JobHandler`は`Job`(`job_type`/`payload`)を受け取り、`complete`にそのまま渡せる`result`
(`dict[str, Any] | None`)を返す、という最小の契約にする。`review`種別の`JobHandler`
(`build_review_handler`)は、`review_job_payload_to_args(job.payload)`で`(project, mr_iid, sha)`を
取り出し、`execute_review`(**変更しない**、GitLab Adapter→Workspace Manager→Claude Code
Runner→Review→State Storeの結線本体)を呼び出し、`build_review_job_result(...)`で`result`を
組み立てて返す。`execute_review_job`(`cli/single_run.py`)がやっていた「payloadの分解→
`execute_review`呼び出し→resultの組み立て」を、Job Repositoryの状態遷移の呼び方だけ
`update_status`から`complete`/`fail`に差し替えて再利用する形になる。

`RunnerDispatcher`(パイプライン本体、Protocol型のみに依存)は`JobRepository`と
`Mapping[JobType, JobHandler]`だけを知っていればよく、`review`固有のロジック(`payload`の
構造等)を一切知らない。M4で`issue-analysis`/`design`/`implement`用の`JobHandler`を追加する際、
`RunnerDispatcher`自体は無改修のまま`build_job_handlers`の辞書にエントリを足すだけで済む
(却下した選択肢「JobType値ごとのif/elif分岐」を参照)。

`job_types`(claim対象の種別)を明示指定しない場合、**`handlers`に登録済みの種別のみ**を
対象にする(`tuple(handlers.keys())`)。理由: 未実装の種別(`issue-analysis`等)をうっかり
claimしてしまうと、`_process`が[ADR-0016](0016-job-abstraction.md)の契約通り
`NotImplementedError`を送出し、即座に`fail(..., retry=False)`でデッドレター化してしまう。
これは「Runnerが対応していないJobTypeをキューに積んでおいて、対応するRunnerが後から
デプロイされたら処理される」というM4以降の運用(異なるJobType専用のRunnerを別々にデプロイする
運用、`docs/architecture.md`の「Runners群」が複数形である理由)を壊す。`--job-types`で
明示指定すれば、`NotImplementedError`の経路(未実装種別を意図的にclaimさせて確認する)も
テスト・デバッグできる。

### 依存の構成(GitLab PAT・Workspace・State Store等)は`run_single_review`と同じ合成ルートパターンを再利用する

`run_dispatcher(config, ...)`(合成ルート)は`run_single_review`/`run_watch`と全く同じ流儀で
`config`から具象実装を組み立てる: `GitLabRestAdapter` / `build_workspace_manager(config)`
(GitLab PATのcredential helper配線を含む、`cli/single_run.py`に既存)/
`SubprocessClaudeCodeRunner` / `SqliteStateStore` / `SqliteJobRepository`。新しい認証・設定の
概念は導入しない(GitLab PAT・Workspaceルート・State Store/Job DBパスはすべて既存の`Config`
フィールドをそのまま使う)。これにより「別ホストで動かす」場合も、そのホスト上に`config.toml`/
`.env`(GitLab PAT)・GitLab到達性・`workspace_root`用のディスク・`state_db_path`/`job_db_path`
(将来M3-5でPostgreSQLに移行してもAPIは不変、[ADR-0017](0017-job-queue.md))が揃っていれば
`worker`プロセスをそのまま起動できる(Dockerイメージ化自体はM3-4のスコープ)。

`worker`固有のポーリング間隔・heartbeat間隔・可視性タイムアウトは`Config`に追加せず、
`review`サブコマンドの`--timeout`と同じ「CLIオプションで上書き可能な、コード内蔵の既定値」
とする(却下した選択肢「`config.toml`に`[dispatcher]`セクションを追加する」を参照)。

### 排他は`ProcessLock`ではなく`JobRepository.claim`のアトミック性に委ねる(多重起動防止を意図的に行わない)

`watch`(`cli/watch.py`)は`ProcessLock`(`cli/lock.py`)で同一`state_db_path`に対する
**多重起動を防止**する。`worker`はこれと正反対で、**同一`job_db_path`に対する複数プロセス
(将来は複数ホスト)の同時起動を前提とし、意図的に多重起動を許可する**。これは本Issueの
「Runner を別プロセス/別ホストで動かせるようにする」という要件そのものであり、
[ADR-0017](0017-job-queue.md)の`claim`(「対象を選ぶ→更新する」を1つのUPDATE文で行う
アトミックな排他取得)がまさにこの複数worker前提のために設計されている。`worker`が
`ProcessLock`を取得してしまうと、2台目以降の`worker`プロセスが起動できなくなり、
Job Queueを導入した意味(スケールアウト)が失われる。

### `heartbeat`はJobを処理する間、専用スレッドで一定間隔ごとに呼ぶ

`RunnerDispatcher._process(job)`は、`handler(job)`(`execute_review`呼び出しを含み、
Claude Codeのheadless実行で数分〜数十分かかりうる、[ADR-0017](0017-job-queue.md)の
`DEFAULT_VISIBILITY_TIMEOUT_SECONDS=600`のコメント参照)を呼ぶ前にheartbeat専用の
`daemon`スレッドを起動し、`handler`の呼び出しが完了(成功/失敗いずれか)したら
`stop_event.set()`して`join`する。ハンドラ本体は同期呼び出しのままにする(`execute_review`の
シグネチャを変えない)。

heartbeat間隔の既定値は120秒(`DEFAULT_HEARTBEAT_INTERVAL_SECONDS`)とし、可視性タイムアウトの
既定値600秒([ADR-0017](0017-job-queue.md)の`DEFAULT_VISIBILITY_TIMEOUT_SECONDS`)の約1/5に
しておくことで、1回のheartbeat送信が失敗しても次の送信までに余裕を持たせる。heartbeatが
`LeaseLostError`を送出した場合(可視性タイムアウト超過で既に別workerに再取得された)は、
それ以上heartbeatを続けても意味がないためスレッドをそのまま終了させる(warningログのみ、
`handler`本体は最後まで実行させる。中断すると`execute_review`が起票済みのState Store
レコードを`RUNNING`のまま放置してしまうため)。

### `complete`/`fail`の呼び分けは、`handler`が送出した例外の型で決める

```python
try:
    if handler is None:
        raise NotImplementedError(f"未対応のJobTypeです: {job.job_type.value}")
    result = handler(job)
except NotImplementedError as exc:
    # 未実装種別はリトライしても状況が変わらないため即座にデッドレター化する
    self._job_repo.fail(job.id, self._worker_id, str(exc), retry=False)
except Exception as exc:
    # execute_reviewが送出する5種類のパイプライン例外を含む、handler内の失敗はすべて
    # リトライ対象として扱う(attempts/max_attemptsの判断はJob Repository側に委ねる)
    self._job_repo.fail(job.id, self._worker_id, str(exc), retry=True)
else:
    self._job_repo.complete(job.id, self._worker_id, result=result)
```

- `handler`が正常終了(`result`を返す) → `complete`
- `handler`が`NotImplementedError`を送出([ADR-0016](0016-job-abstraction.md)の契約通り、
  `handlers`に対応するJobTypeが登録されていない場合に`RunnerDispatcher`自身が送出する) →
  `fail(..., retry=False)`。リトライしても同じ結果にしかならない「恒久的な失敗」のため、
  即座にデッドレター化する(`fail`のdocstring・[ADR-0017](0017-job-queue.md)が定義する
  `retry=False`の使いどころ)
- それ以外の例外(`execute_review`が送出する`GitLabAdapterError`/`WorkspaceError`/
  `RunnerError`/`ReviewError`/`StateStoreError`を含む) → `fail(..., retry=True)`。
  一過性かもしれない失敗として`JobRepository`の`attempts`/`max_attempts`判定に委ね、
  上限に達していれば`fail`自身がデッドレター化する([ADR-0017](0017-job-queue.md)が
  既に確定した挙動、`RunnerDispatcher`側で上限判定を再実装しない)

1件のJobの失敗は他のJobの処理を止めない(`_process`は例外を再送出せず、ログに記録して
`run_once`は`True`を返す=「1件処理した」ことにする)。これは`watch`の`ReviewWorkerPool`
([ADR-0015](0015-parallel-review-execution.md))とは異なる方針である点に注意
(「却下した選択肢」参照)。

### `claim`が空振りした場合のみ`poll_interval_seconds`だけ待つ(専用のイベント通知機構は導入しない)

`RunnerDispatcher.run_forever(stop_event)`は`stop_event`がセットされるまで
`claim`→(取得できれば)処理、を繰り返す。`claim`が`None`を返した(処理対象が無かった)場合のみ
`stop_event.wait(poll_interval_seconds)`(既定5秒)でポーリング間隔を空ける。Jobが有る間は
即座に次の`claim`を試みるため、複数`worker`が同時に稼働していてもキューが詰まっている限り
待ち時間なく捌ける。専用のイベント通知(DBのLISTEN/NOTIFY等)は導入しない
(ADR-0001の依存最小方針、[ADR-0017](0017-job-queue.md)が可視性タイムアウトの回収にも
同じ「都度確認」方式を採用した判断を踏襲)。

## 却下した選択肢

- **`JobType`値ごとの`if`/`elif`分岐で処理を振り分ける**: `RunnerDispatcher`本体がJobType
  追加のたびに変更対象になり、[ADR-0016](0016-job-abstraction.md)「Job抽象はJobType追加時に
  無改修でいられる設計にする」という基本方針に反する。ディスパッチテーブル(`dict[JobType,
  JobHandler]`)なら、M4で`issue-analysis`/`design`/`implement`用のhandlerを追加する際も
  `RunnerDispatcher`自体を変更せずに済む
- **`config.toml`に`[dispatcher]`セクションを追加し、ポーリング/heartbeat間隔等を設定ファイル化する**:
  技術的には可能だが、(1)これらは「1プロセスの実行チューニング値」であり複数`worker`プロセスが
  同じ`config.toml`を共有しても値を変えたい場面がある(例: ホストごとにポーリング間隔を変える)、
  (2)`Config`(`config/models.py`)は他の並行Issue(M3-4/M3-5/M3-7/M3-8)からも参照される
  共有ファイルであり、本Issueの都合だけで不要にフィールドを増やすと差分競合のリスクが増す。
  `review`サブコマンドの`--timeout`と同じ「コード内蔵の既定値+CLIオプションでの上書き」に
  とどめ、真に恒久設定が必要になった時点(実運用で要求が具体化した時点)で改めて検討する
- **`ProcessLock`を`worker`にも適用し、同一`job_db_path`への多重起動を防ぐ**: 「決定」節の
  通り、`worker`は複数プロセス/複数ホストからの同時稼働を前提とする設計そのものであり、
  `ProcessLock`を適用するとスケールアウトができなくなる。排他は`claim`のアトミックな
  UPDATE文([ADR-0017](0017-job-queue.md))に委ねる
- **`handler`内の想定外の例外を`watch`と同じく「ログに記録して伝播させ、プロセスを落とす」
  ([ADR-0009](0009-cli-watch-design.md)の方針)にする**: `watch`はWindows上で人間が近くにいる
  端末での運用を前提とし、想定外のバグを目に見える形で落とす方を優先した
  ([ADR-0009](0009-cli-watch-design.md))。`worker`は本Issueの要件そのものが「別プロセス/
  別ホスト」=無人・ヘッドレスな運用であり、1件のJobの想定外の失敗のたびにプロセス全体が
  落ちると、他の正常なJobまで処理できなくなり運用上の不利益が大きい。`fail(..., retry=True)`
  によって`attempts`/`max_attempts`([ADR-0017](0017-job-queue.md))が上限到達を検知し
  デッドレター化する仕組みが既にあるため、個々のJobの失敗はその仕組みに委ね、`worker`
  プロセス自体は継続する方針にした。この判断は`watch`の哲学を覆すものではなく、運用形態
  (人間が近くにいる端末 vs 無人ホスト)の違いに由来する意図的な差である
- **1回の`worker`起動で1件だけJobを処理して終了する設計にする(常駐プロセスにしない)**:
  cron等の外部スケジューラに委ねる案。`--once`オプションとして残し(デバッグ・単発実行用途)
  つつ、既定は常駐ループ(`run_forever`)にした。理由: Claude Codeのheadless実行は
  プロセス起動コスト(Bedrock認証・Workspace準備等)が無視できず、Job 1件ごとにプロセスを
  起動し直すと、単一のプロセスがキューを継続的に捌く場合に比べてオーバーヘッドが積み上がる。
  常駐が既定でも、複数`worker`プロセス/ホストでの水平スケールは`claim`の排他が担保するため
  安全である

## 影響

- `src/gitlab_ai_platform/cli/dispatcher.py`(新規、`RunnerDispatcher`/`build_job_handlers`/
  `build_review_handler`/`run_dispatcher`)、`src/gitlab_ai_platform/cli/main.py`(`worker`
  サブコマンド追加)、`src/gitlab_ai_platform/cli/exit_codes.py`(`EXIT_JOB_ERROR`追加)を変更した
- `job/`パッケージ(`protocol.py`/`sqlite.py`/`errors.py`)は無改修。[ADR-0017](0017-job-queue.md)
  が確定した`claim`/`heartbeat`/`complete`/`fail`/`list_dead_letters`をそのまま呼び出すだけで、
  M3-4(#94)/M3-5(#95)/M3-7(#97)/M3-8(#98)と並行して進めても`job/`のシグネチャ競合は起きない
- `execute_review`/`execute_review_job`(`cli/single_run.py`)は無改修。`review`/`watch`
  サブコマンドの既存の「起票直後に同期処理する」経路は変更しない
  (`references/タスク整理.md`M1-12のE2E確認等が引き続き有効)
- `docs/specs/job-model.md`「非対象」節の「`claim`/`complete`/`fail`をRunnerの実行経路に
  実配線することはM3-3のスコープ」が本Issueで実現された。同ファイルを更新した
- `docs/specs/cli.md`に`worker`サブコマンドの節を追加した
- `docs/operations/configuration.md`は変更なし(`Config`への変更が無いため)
- M4(`issue-analysis`/`design`/`implement`)は、`build_job_handlers`に対応するhandlerを
  追加するだけで`worker`から実行できるようになる見込み(`RunnerDispatcher`自体は無改修)
- M3-4(Docker実行環境)は、この`worker`サブコマンドをコンテナのエントリポイントにする形で
  着手できる見込み
