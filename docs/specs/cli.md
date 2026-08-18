# CLI

- 実装場所: `src/gitlab_ai_platform/cli/`
- 対応Issue: [#38](https://github.com/AtsushiNi/gitlab-ai-platform/issues/38) (M1-10)、
  [#39](https://github.com/AtsushiNi/gitlab-ai-platform/issues/39) (M1-11)、
  [#48](https://github.com/AtsushiNi/gitlab-ai-platform/issues/48) (M2-11)、
  [#80](https://github.com/AtsushiNi/gitlab-ai-platform/issues/80) (M2-1、`watch`の並列実行)、
  [#91](https://github.com/AtsushiNi/gitlab-ai-platform/issues/91) (M3-1、review Jobとしての再構成)、
  [#93](https://github.com/AtsushiNi/gitlab-ai-platform/issues/93) (M3-3、Runner Dispatcher
  `worker`サブコマンド)、
  [#97](https://github.com/AtsushiNi/gitlab-ai-platform/issues/97) (M3-7、HTTP API
  `api`サブコマンド)、
  [#111](https://github.com/AtsushiNi/gitlab-ai-platform/issues/111) (M4-5、`WAITING_HUMAN`後の
  回答取り込み `respond`サブコマンド)、
  [#112](https://github.com/AtsushiNi/gitlab-ai-platform/issues/112) (M4-6、`respond`の
  `design`種別Jobへの対応拡張)、
  [#113](https://github.com/AtsushiNi/gitlab-ai-platform/issues/113) (M4-7、`respond`の
  `plan`種別Jobへの対応拡張)
- 関連ADR: [ADR-0008](../adr/0008-cli-single-run-design.md)、
  [ADR-0009](../adr/0009-cli-watch-design.md)、
  [ADR-0012](../adr/0012-decompose-interactive-session.md)、
  [ADR-0015](../adr/0015-parallel-review-execution.md)、
  [ADR-0016](../adr/0016-job-abstraction.md)、
  [ADR-0022](../adr/0022-runner-process-separation.md)、
  [ADR-0023](../adr/0023-http-api.md)、
  [ADR-0028](../adr/0028-waiting-human-answer-integration.md)、
  [ADR-0029](../adr/0029-design-phase.md)、
  [ADR-0030](../adr/0030-implementation-plan-phase.md)
- ステータス: 実装済み(単発レビュー実行`review`サブコマンド、常駐`watch`サブコマンド、
  要件→Issue分解の対話型`decompose`サブコマンド、Runner Dispatcher常駐`worker`サブコマンド、
  HTTP APIサーバー常駐`api`サブコマンド、`WAITING_HUMAN`後の回答取り込み`respond`サブコマンド)

## 責務

6つのサブコマンドを提供する:

- `review`: 指定した1つのproject/MRに対し、GitLab Adapter → Workspace Manager →
  Review(プロンプト) → Claude Code Runner → Review(パース・保存) → State Storeという
  一連のパイプラインを1回だけ実行する。「デバッグとプロンプト改善の主要導線」
  (`docs/architecture.md`)として、結果の保存先パスと簡単なサマリを標準出力に表示する
- `watch`: MR Poller(M1-5)で対象プロジェクトを定期走査し、検出したMRごとに`review`と
  同じレビュー実行パイプラインを呼び出し続ける常駐モード。検出した複数MRのレビューは
  `config.max_parallel`個までのワーカースレッドで並行実行する(M2-1、[ADR-0015](../adr/0015-parallel-review-execution.md))。
  Ctrl+C(SIGINT)/SIGTERMでgraceful shutdownし、同一設定に対する多重起動を防ぐ。
  `config.webhook_enabled=true`(任意有効化、既定false)の場合、GitLab Merge Request Hookを
  受信するWebhookサーバーも背景スレッドで起動し、MR Pollerと同じ実行経路
  (`ReviewWorkerPool`)・二重起票防止ロジック(`poller.ticket_if_unprocessed`)を共有する
  (M3-6、[ADR-0018](../adr/0018-webhook-receiver.md)、[specs/webhook-receiver.md](webhook-receiver.md))
- `worker`: Job Repository(`job/`)から`claim`で取り出したJobを処理し続けるRunner
  Dispatcherの常駐モード(M3-3、[ADR-0022](../adr/0022-runner-process-separation.md))。
  `review`/`watch`が使う「起票直後に同一プロセス内で同期処理する」経路(`execute_review_job`)
  とは別の経路で、別プロセス/別ホストでの実行を想定する。同一`job_db_path`に対する複数
  `worker`プロセスの同時稼働を前提とする設計のため、`watch`と異なり多重起動防止は行わない
- `decompose`: 指定した1つのprojectに対し、GitLab Adapter MCP Server(M2-12、
  `adapter_mcp_server`)を`--mcp-config`で登録した**対話型**の`claude`セッションを起動する
  (M2-11、`docs/requirements.md` 3-C)。`review`/`watch`のheadless実行(`-p`付き、標準出力の
  JSONをパース)とは異なり、stdin/stdout/stderrをそのまま人間に継承させ、ターミナルで
  人間とClaude Codeが直接対話しながら新しい開発要件を複数のGitLab Issueへ分解・起票する
- `api`: Job Repository(`job/`)への最小限のHTTP API(投入・状態/結果参照・一覧取得)を
  提供する常駐モード(M3-7、[ADR-0023](../adr/0023-http-api.md)、
  [specs/http-api.md](http-api.md))。`worker`と同様、`review`/`watch`が使う経路とは別の
  独立したプロセスで、`watch`(Poller/Webhook)・`worker`(Runner Dispatcher)いずれの稼働状況
  にも依存しない。「将来のUIや他ツール連携の口」として、任意の`JobType`のJobを投入できる
- `respond`: `WAITING_HUMAN`状態のJob(M4-3、要求分析フェーズが`ASK`判定の不明点を持つ場合に
  遷移する。M4-6/M4-7で設計・実装計画フェーズも同様に遷移しうる)へ、人間の回答をターミナル
  入力(`input()`)で取り込み、`RUNNING`を経て`DONE`へ遷移させる(M4-5、
  [ADR-0028](../adr/0028-waiting-human-answer-integration.md)、
  [specs/issue-analysis.md](issue-analysis.md)「`WAITING_HUMAN`後の再開」)。`job_id`省略時は
  `WAITING_HUMAN`状態のJobを一覧表示するだけで状態は変更しない。`review`/`watch`と同じ
  非リース方式(`JobRepository.update_status`)の経路を使う(`WAITING_HUMAN`は`claim`対象外の
  状態のため、`worker`のリース方式では扱わない)。回答の統合先(`result`の組み立て方)は
  `job_type`ごとの辞書(`_RESULT_RESOLVERS`)から選ぶ。M4-7時点で`issue-analysis`/`design`/`plan`が
  対応済み、`implement`は未対応(M4-7、[ADR-0030](../adr/0030-implementation-plan-phase.md))

## 前提と非対象

- 前提:
  - `config.toml` + `.env`から読み込んだ`Config`(`config/models.py`。M1-10でWorkspace
    Manager/Claude Code Runner/Review/State Store向けのフィールドを追加済み)が利用可能
    であること(`decompose`もこの前提を共有するが、`Config`の値そのものは使わず、
    検証済みの`--config`/`--env`パスをそのまま`adapter_mcp_server`の起動コマンドへ
    引き継ぐだけであることに注意。`docs/adr/0012-decompose-interactive-session.md`)
  - `claude` CLIがPATH上で実行可能であること、Bedrock認証が環境変数経由で設定済みであること
    (Claude Code Runnerの前提, `docs/specs/claude-code-runner.md`と同じ)
  - 対象プロジェクトへのgit clone/fetchがネットワーク的に到達可能であること(`review`/`watch`/`worker`)
  - `decompose`は人間が対話するため、実行時にターミナル(TTY)を持つWindows端末上での利用を
    想定する(`docs/requirements.md` 3-C、`docs/architecture.md`「Windows/Linuxの分担」)。
    `review`/`watch`と異なり、対象プロジェクトのローカルclone/worktreeが存在することは
    前提にしない(要件がまだIssue化されていない段階から始まるため)
  - `worker`は`config.toml`/`.env`(GitLab PAT)・GitLab到達性・`workspace_root`用のディスク・
    `state_db_path`/`job_db_path`が揃っていれば、`review`/`watch`と同じホストでも別ホストでも
    起動できる(M3-3、[ADR-0022](../adr/0022-runner-process-separation.md)。コンテナ化自体は
    M3-4のスコープ)
  - `api`は`.env`の`GITLAB_AI_PLATFORM_API_TOKEN`(未設定なら`ConfigError`で起動しない)と
    `job_db_path`のみが前提で、GitLab到達性・`workspace_root`は不要(GitLab Adapter/Workspace
    Manager/Claude Code Runnerのいずれにも依存しないため、M3-7、
    [ADR-0023](../adr/0023-http-api.md))
  - `respond`は`job_db_path`のみが前提で、GitLab到達性・`workspace_root`は不要(`api`と同じ
    理由)。人間が対話するため実行時にターミナル(TTY)を持つ環境での利用を想定する
    (`decompose`と同じ、`docs/requirements.md` 3-C)
- 非対象:
  - オーケストレーション(Job間の遷移)はしない(`docs/architecture.md`のCLIの境界)
  - `review`はMR Pollerによる複数MR横断の走査はしない。`project`/`mr_iid`は呼び出し時に
    人間が指定する
  - GitLabへの自動コメント投稿はしない(Review, M1-9の境界を継承)
  - `watch`は失敗したレビューの自動リトライ・監視・プロセス再起動はしない
    (`docs/adr/0009-cli-watch-design.md`。M3以降のLinux/Docker移行後のスコープ)
  - `worker`は`review`種別以外のJobType(`issue-analysis`/`design`/`plan`/`implement`)の実際の
    処理を実装しない(M4のスコープ)。未実装種別を明示的に`--job-types`でclaim対象にした場合は
    `NotImplementedError`経由でデッドレター化する([ADR-0016](../adr/0016-job-abstraction.md)、
    [ADR-0022](../adr/0022-runner-process-separation.md))
  - `decompose`はIssue分解案の自動決定・無人起票はしない。粒度・優先度・依存関係の判断は
    常に人間が対話の中で下す(`docs/requirements.md` 3-Cの「Bとの違い」)。分解後の
    設計・実装・MR作成(B/M4のスコープ)には進まない
  - `api`はJobの実行(`worker`の責務)を行わない。`claim`/`heartbeat`/`complete`/`fail`
    (Runner Dispatcher専用の操作)は公開せず、Job Repositoryへの読み書き(投入・参照・一覧)
    のみを提供する(M3-7、[ADR-0023](../adr/0023-http-api.md))
  - `respond`は1回の呼び出しで複数Jobをまとめて処理しない(`job_id`省略時は一覧表示のみ)。
    GitLab Issueコメント経由での質問提示・回答収集も対象外(実際に必要になってから追加する、
    M4-5、[ADR-0028](../adr/0028-waiting-human-answer-integration.md)「却下した選択肢」)。
    `issue-analysis`/`design`/`plan`以外のJob種別(`implement`)への対応も対象外
    (M4-8以降で必要になった時点で`_RESULT_RESOLVERS`に追加する、
    [ADR-0030](../adr/0030-implementation-plan-phase.md))

## 公開インターフェース

### コマンド

```text
gitlab-ai-platform [--config PATH] [--env PATH] [--log-level LEVEL] [--log-dir DIR] \
    review <project> <mr_iid> \
    [--timeout SECONDS] \
    [--allowed-tools TOOL [TOOL ...]] \
    [--disallowed-tools TOOL [TOOL ...]] \
    [--permission-mode MODE]

gitlab-ai-platform [--config PATH] [--env PATH] [--log-level LEVEL] [--log-dir DIR] watch

gitlab-ai-platform [--config PATH] [--env PATH] [--log-level LEVEL] [--log-dir DIR] \
    worker \
    [--worker-id ID] \
    [--job-types TYPE [TYPE ...]] \
    [--poll-interval SECONDS] \
    [--heartbeat-interval SECONDS] \
    [--visibility-timeout SECONDS] \
    [--once]

gitlab-ai-platform [--config PATH] [--env PATH] [--log-level LEVEL] [--log-dir DIR] api

gitlab-ai-platform [--config PATH] [--env PATH] [--log-level LEVEL] [--log-dir DIR] \
    decompose <project> \
    [--permission-mode MODE]

gitlab-ai-platform [--config PATH] [--env PATH] [--log-level LEVEL] [--log-dir DIR] \
    respond [job_id]
```

`pip install -e .`後は`gitlab-ai-platform`(`[project.scripts]`)として、それ以外でも
`python -m gitlab_ai_platform.cli`として実行できる。

| 引数/オプション | 必須 | 既定値 | 説明 |
|---|---|---|---|
| `--config` | - | `config.toml` | 設定ファイルのパス(`config.load_config`にそのまま渡す) |
| `--env` | - | `.env` | シークレットファイルのパス |
| `--log-level` | - | `INFO` | ルートロガーのログレベル |
| `--log-dir` | - | なし(コンソールのみ) | 構造化ログ(JSON、日次ローテーション)の出力先。`decompose`ではさらに`adapter_mcp_server`起動コマンドの`--log-dir`にもそのまま引き継ぐ |
| `project`(review/decompose) | ✓ | - | GitLabのプロジェクトパス(`group/project`形式) |
| `mr_iid`(review) | ✓ | - | MRのIID |
| `--timeout`(review) | - | `config.toml`の`runner.timeout_seconds` | Claude Codeのタイムアウト秒数 |
| `--allowed-tools`(review) | - | 空 | `claude --allowedTools`に対応 |
| `--disallowed-tools`(review) | - | 空 | `claude --disallowedTools`に対応 |
| `--permission-mode`(review/decompose) | - | なし | `claude --permission-mode`に対応 |
| `--worker-id`(worker) | - | `hostname:pid`を自動生成 | このworkerプロセスのリース所有者ID(`claim`/`heartbeat`/`complete`/`fail`の`worker_id`) |
| `--job-types`(worker) | - | `handlers`に登録済みの種別のみ(M3-3時点では`review`のみ) | claim対象とする`JobType`の値(`review`/`issue-analysis`/`design`/`plan`/`implement`) |
| `--poll-interval`(worker) | - | `5`(秒) | `claim`が空振りした際の待機秒数 |
| `--heartbeat-interval`(worker) | - | `120`(秒) | Job処理中にリースを延長する間隔秒 |
| `--visibility-timeout`(worker) | - | `600`(秒、`job.protocol.DEFAULT_VISIBILITY_TIMEOUT_SECONDS`) | `claim`時に設定する可視性タイムアウト秒 |
| `--once`(worker) | - | 偽 | 1件だけJobをclaim・処理して終了する(デバッグ・単発実行用) |
| `job_id`(respond) | - | なし(省略時は一覧表示のみ) | 回答対象のJob ID。`WAITING_HUMAN`状態のJobである必要がある |

`watch`/`worker`は`project`/`mr_iid`のような対象指定の引数を持たない。走査対象プロジェクト・
ポーリング間隔・レビュー待ちラベル等はすべて`config.toml`(`Config`)から読む
(`--timeout`等をMR単位で都度変えるユースケースは想定していない。デバッグ用途は
`review`を使う)。`worker`固有のポーリング/heartbeat間隔・可視性タイムアウトは`Config`に
追加せず、コード内蔵の既定値をCLIオプションで上書きする方式にした
([ADR-0022](../adr/0022-runner-process-separation.md)「却下した選択肢」参照)。

`api`もCLI固有のオプションを持たない(`--host`/`--port`/`--token`のような上書きは提供しない)。
待受アドレス/ポートは`config.toml`の`[api]`セクション、トークンは`.env`の
`GITLAB_AI_PLATFORM_API_TOKEN`のみで設定する(`webhook`と同じ方針、
[ADR-0023](../adr/0023-http-api.md)「決定」)。

`decompose`は`project`(GitLabのプロジェクトパス)を人間が明示指定する。`review`と異なり
`mr_iid`に相当するものは存在しない(要件がまだIssue化されていない段階から始まるため)。
`--allowed-tools`/`--disallowed-tools`は公開しない(対話型セッションでは`claude`自身の
既定の権限確認フロー、または`--permission-mode`で人間がその場で制御する想定のため)。

`respond`は`job_id`(位置引数、省略可)のみを取り、`api`と同じくCLI固有のオプションは
持たない。`job_id`省略時は`WAITING_HUMAN`状態のJobの一覧をターミナルに表示するだけで、
質問提示・回答収集・状態遷移は行わない(M4-5、[ADR-0028](../adr/0028-waiting-human-answer-integration.md))。

### Python API

#### `review`(実装場所: `src/gitlab_ai_platform/cli/single_run.py`)

```python
from gitlab_ai_platform.config import Config
from gitlab_ai_platform.gitlab_adapter.protocol import GitLabReader
from gitlab_ai_platform.job.protocol import JobRepository
from gitlab_ai_platform.runner.protocol import ClaudeCodeRunner
from gitlab_ai_platform.store.protocol import StateStore
from gitlab_ai_platform.workspace.protocol import WorkspaceManager


def execute_review(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
    project: str,
    mr_iid: int,
    *,
    sha: str | None = None,
    timeout_seconds: int | None = None,
    allowed_tools: "Sequence[str]" = (),
    disallowed_tools: "Sequence[str]" = (),
    permission_mode: str | None = None,
) -> "SingleRunResult":
    """パイプライン本体。4つの依存先はすべてProtocol型で受け取る(具象実装に依存しない)。
    `sha`省略時(`review`サブコマンド)はMR取得時点の最新`merge_request.sha`を使う。
    `sha`指定時(`watch`サブコマンド、M1-11)は、それが呼び出し時点の最新commitより
    古くても指定commitに対してレビューを行う(呼び出し側が既にState Storeへ起票済みの
    commitを上書きしないため)。Job抽象(M3-1)からは直接呼ばれず、`execute_review_job`
    経由で呼ばれる。"""


def execute_review_job(
    job_repo: JobRepository,
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
    project: str,
    mr_iid: int,
    *,
    sha: str | None = None,
    timeout_seconds: int | None = None,
    allowed_tools: "Sequence[str]" = (),
    disallowed_tools: "Sequence[str]" = (),
    permission_mode: str | None = None,
) -> "SingleRunResult":
    """`execute_review`を`review`種別のJob(M3-1 [#91], ADR-0016)としてラップする。
    `job_repo.enqueue`で`PENDING`のJobを起票し、`RUNNING`へ更新してから`execute_review`を
    呼び出す。成功時はJobを`DONE`へ(`result`にproject/mr_iid/sha/結果保存先パスを記録)、
    `execute_review`が送出する例外発生時はJobを`FAILED`へ更新してから元の例外を
    そのまま再送出する。二重レビュー防止は引き続きState Store(`execute_review`内)が担う
    (詳細: [specs/job-model.md](job-model.md))。"""


def run_single_review(
    config: Config,
    project: str,
    mr_iid: int,
    *,
    timeout_seconds: int | None = None,
    allowed_tools: "Sequence[str]" = (),
    disallowed_tools: "Sequence[str]" = (),
    permission_mode: str | None = None,
) -> "SingleRunResult":
    """合成ルート。`config`からGitLabRestAdapter/GitWorkspaceManager/
    SubprocessClaudeCodeRunner/SqliteStateStore/SqliteJobRepositoryを組み立て、
    `execute_review_job`に委譲する。"""


def build_workspace_manager(config: Config) -> "GitWorkspaceManager":
    """GitLab認証(credential helper)込みの`GitWorkspaceManager`を組み立てる。
    `run_single_review`と`run_watch`の両方から再利用する(M1-11、ADR-0009)。"""
```

#### `watch`(実装場所: `src/gitlab_ai_platform/cli/watch.py`)

[ADR-0008](../adr/0008-cli-single-run-design.md)の`execute_review`/`run_single_review`
分離パターンをそのまま踏襲する([ADR-0009](../adr/0009-cli-watch-design.md))。M2-1
([ADR-0015](../adr/0015-parallel-review-execution.md))で検出済みMRの並列実行に対応した際も
`build_on_detected`自体は変更せず、`run_watch_loop`が`ReviewWorkerPool`(`cli/worker_pool.py`)への
投入に置き換える形で並列化した。

```python
import threading

from gitlab_ai_platform.config import Config
from gitlab_ai_platform.gitlab_adapter.protocol import GitLabReader
from gitlab_ai_platform.job.protocol import JobRepository
from gitlab_ai_platform.poller import DetectedReview
from gitlab_ai_platform.runner.protocol import ClaudeCodeRunner
from gitlab_ai_platform.store.protocol import StateStore
from gitlab_ai_platform.workspace.protocol import WorkspaceManager


class ReviewWorkerPool:
    """`max_workers`個までのスレッドでレビュージョブ(`Callable[[], None]`)を並行実行する
    (`cli/worker_pool.py`、M2-1)。"""

    def __init__(self, max_workers: int, stop_event: threading.Event) -> None: ...

    def submit(self, job: "Callable[[], None]") -> None:
        """`job`をプールに投入する(即座に戻り、実行完了を待たない)。"""

    def shutdown_and_reraise(self) -> None:
        """実行中のジョブの完了を待ってプールを終了し、ワーカースレッド内で発生した
        想定外の例外があれば再送出する。"""


def build_on_detected(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    job_repo: JobRepository,
    config: Config,
) -> "Callable[[DetectedReview], None]":
    """`DetectedReview`ごとに`execute_review_job`(M3-1)を呼ぶコールバックを組み立てる。
    既知のパイプライン例外はログに記録して握りつぶし、想定外の例外はそのまま伝播させる。"""


def run_watch_loop(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    job_repo: JobRepository,
    config: Config,
    *,
    stop_event: threading.Event | None = None,
) -> None:
    """パイプライン本体。`MrPoller`と`build_on_detected`を結線する。
    5つの依存先はすべてProtocol型で受け取る(具象実装に依存しない)。検出された各MRの
    処理は`ReviewWorkerPool(config.max_parallel, stop_event)`へ投入し、並行実行する
    (M2-1)。`stop_event`を省略した場合はここで生成し、`MrPoller.run`とプールの両方に
    同じオブジェクトを渡す(ワーカースレッドの想定外の例外がポーリングループの早期終了に
    反映されるようにするため)。`config.webhook_enabled`が真の場合(M3-6)、`WebhookServer`
    (`webhook/server.py`)も同じプールへの投入ラッパーを`on_detected`として起動し、
    `finally`節で停止する([ADR-0018](../adr/0018-webhook-receiver.md))。"""


def run_watch(config: Config, *, stop_event: threading.Event | None = None) -> None:
    """合成ルート。`config`から具象実装(SqliteJobRepository含む、M3-1)を組み立て、
    `ProcessLock`(多重起動防止、`cli/lock.py`)を取得してから`run_watch_loop`に委譲する。"""
```

#### `worker`(実装場所: `src/gitlab_ai_platform/cli/dispatcher.py`、M3-3)

`review`/`watch`と同じ「パイプライン本体/合成ルート」分離パターンを踏襲する
([ADR-0008](../adr/0008-cli-single-run-design.md)、[ADR-0022](../adr/0022-runner-process-separation.md))。
`JobType` → `JobHandler`のディスパッチテーブルでJob受け渡しプロトコルを表現し、
`RunnerDispatcher`本体は`review`固有のロジックを知らない。

```python
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from gitlab_ai_platform.config import Config
from gitlab_ai_platform.gitlab_adapter.protocol import GitLabReader
from gitlab_ai_platform.job.protocol import Job, JobRepository, JobType
from gitlab_ai_platform.runner.protocol import ClaudeCodeRunner
from gitlab_ai_platform.store.protocol import StateStore
from gitlab_ai_platform.workspace.protocol import WorkspaceManager

JobHandler = Callable[[Job], "dict[str, Any] | None"]


def build_review_handler(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
) -> JobHandler:
    """review種別の`JobHandler`を組み立てる。`execute_review`本体(変更しない)をそのまま
    呼び出し、`build_review_job_result`で`result`を組み立てて返す。"""


def build_job_handlers(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
) -> "dict[JobType, JobHandler]":
    """JobType → JobHandlerのディスパッチテーブルを組み立てる。M3-3時点では`review`のみ。"""


class RunnerDispatcher:
    """`JobRepository.claim`でJobを取り出し、対応する`JobHandler`で処理するループ本体。
    `job_repo`/`handlers`ともにProtocol型・関数のマッピングのみに依存する。"""

    def __init__(
        self,
        job_repo: JobRepository,
        handlers: "Mapping[JobType, JobHandler]",
        *,
        worker_id: str,
        job_types: "Sequence[JobType] | None" = None,
        poll_interval_seconds: float = 5.0,
        heartbeat_interval_seconds: float = 120.0,
        visibility_timeout_seconds: int = 600,
    ) -> None: ...

    def run_once(self) -> bool:
        """1件Jobをclaimして処理する。claimできるJobが無ければ`False`を返す。"""

    def run_forever(self, stop_event: threading.Event) -> None:
        """`stop_event`がセットされるまで、claim→処理(空振り時のみ`poll_interval_seconds`
        待機)を繰り返す。"""


def default_worker_id() -> str:
    """`--worker-id`省略時に使う既定のworker識別子(`hostname:pid`)を組み立てる。"""


def run_dispatcher(
    config: Config,
    *,
    stop_event: threading.Event | None = None,
    worker_id: str | None = None,
    job_types: "Sequence[JobType] | None" = None,
    poll_interval_seconds: float = 5.0,
    heartbeat_interval_seconds: float = 120.0,
    visibility_timeout_seconds: int = 600,
    run_once: bool = False,
) -> None:
    """合成ルート。`config`から具象実装(GitLabRestAdapter/build_workspace_manager/
    SubprocessClaudeCodeRunner/SqliteStateStore/SqliteJobRepository)を組み立て、
    `RunnerDispatcher`を起動する。`watch`と異なり`ProcessLock`は取得しない(複数`worker`
    プロセス/ホストの同時稼働を前提とする設計のため、ADR-0022)。"""
```

#### `api`(実装場所: `src/gitlab_ai_platform/cli/api_server.py`、M3-7)

`worker`と同じ「合成ルートを`cli/<name>.py`に置く」パターンを踏襲するが、`api`は常駐ループ
(claim/poll)を持たず、`ApiServer`(`api/server.py`、[specs/http-api.md](http-api.md))を
起動して`stop_event`を待つだけの薄い合成ルートになる([ADR-0023](../adr/0023-http-api.md))。

```python
import threading

from gitlab_ai_platform.config import Config


def run_api_server(
    config: Config, *, stop_event: threading.Event | None = None
) -> None:
    """合成ルート。`config`から`SqliteJobRepository`/`ApiServer`を組み立てて`start()`し、
    `stop_event`がセットされるまで待つ。`config.api_token`が空の場合は`ConfigError`を
    送出する(無認証での起動を防ぐ)。`worker`と同じ理由で`ProcessLock`は取得しない
    (複数`api`プロセスの同時稼働を妨げない、ADR-0022と同じ考え方)。"""
```

#### `decompose`(実装場所: `src/gitlab_ai_platform/cli/decompose.py`)

`review`/`watch`の「パイプライン本体/合成ルート」分離とは異なり、`decompose`にはProtocol型の
依存先(Adapter/Workspace/Runner/Store)が存在しない(対話型の`claude`セッションを起動する
だけのため)。代わりに、`--mcp-config`/システムプロンプト/起動コマンドの組み立てをそれぞれ
独立した純粋関数に分け、単体テストしやすくしている([ADR-0012](../adr/0012-decompose-interactive-session.md))。

```python
import subprocess
from pathlib import Path
from typing import Any


def build_mcp_config(
    config_path: Path,
    env_path: Path,
    *,
    python_executable: str = ...,  # 既定: sys.executable
    log_dir: Path | None = None,
) -> dict[str, Any]:
    """GitLab Adapter MCP Server(`adapter_mcp_server`)を登録した`--mcp-config`用のJSON構造を
    組み立てる。`config_path`/`env_path`はそのまま`adapter_mcp_server`の`--config`/`--env`へ
    引き継ぐだけで、GitLab PAT等の値そのものはここに一切含まれない。"""


def build_system_prompt(project: str) -> str:
    """`--append-system-prompt`用の文字列を組み立てる。対象プロジェクトの明示・人間の判断を
    仰ぐこと・起票時のproject明示をセッション全体に効く指示として持たせる。"""


def build_initial_prompt(project: str) -> str:
    """対話セッション開始時に自動送信される最初のユーザーメッセージを組み立てる。"""


def build_claude_command(
    claude_command: str,
    *,
    mcp_config: dict[str, Any],
    system_prompt: str,
    initial_prompt: str,
    permission_mode: str | None = None,
) -> list[str]:
    """対話型`claude`セッションを起動するコマンド列を組み立てる。`-p`(headless実行)は
    付けない。`--strict-mcp-config`で他のMCP設定を無効化する。"""


def run_decompose(
    project: str,
    *,
    config_path: Path,
    env_path: Path,
    log_dir: Path | None = None,
    claude_command: str = "claude",
    python_executable: str = ...,  # 既定: sys.executable
    permission_mode: str | None = None,
    popen=subprocess.Popen,
) -> int:
    """対話型のIssue分解セッションを起動する(合成ルート)。stdin/stdout/stderrを継承した
    対話型プロセスとして`claude`を起動し、`claude`プロセス終了時の終了コードをそのまま返す。
    `claude`コマンド自体が見つからない場合のみ`ClaudeCommandNotFoundError`を送出する。"""
```

#### `respond`(実装場所: `src/gitlab_ai_platform/cli/respond.py`、M4-5)

`review`/`watch`と同じ「パイプライン本体/合成ルート」分離パターンを踏襲する
([ADR-0008](../adr/0008-cli-single-run-design.md)、
[ADR-0028](../adr/0028-waiting-human-answer-integration.md))。`WAITING_HUMAN`は`claim`対象外の
状態のため、`worker`のリース方式ではなく`review`/`watch`と同じ非リース方式
(`JobRepository.update_status`)を使う。

```python
from collections.abc import Callable

from gitlab_ai_platform.config import Config
from gitlab_ai_platform.job import Job, JobRepository


def collect_answers(
    questions: list[dict],
    *,
    ask: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> list[str]:
    """`questions`(`Job.result["questions"]`)を1件ずつ提示し、人間の回答を集める。
    Job Repositoryの状態は一切変更しない。"""


def list_waiting_human_jobs(
    job_repo: JobRepository, *, output: Callable[[str], None] = print
) -> list[Job]:
    """`WAITING_HUMAN`状態のJobを一覧表示する(`job_id`省略時の確認用途)。"""


def respond_to_job(
    job_repo: JobRepository,
    job: Job,
    *,
    ask: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> Job:
    """`WAITING_HUMAN`のJobに回答を取り込み、`RUNNING`を経て`DONE`へ遷移させる。
    質問提示・回答収集(`collect_answers`)はJobの状態を一切変更しない。`RUNNING`遷移後に
    例外(`KeyboardInterrupt`含む)が発生した場合は`FAILED`へ更新してから元の例外を
    再送出する(ADR-0028)。"""


def run_respond(
    config: Config,
    *,
    job_id: str | None = None,
    ask: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> Job | None:
    """合成ルート。`config`から`SqliteJobRepository`を組み立てる。`job_id`省略時は
    `list_waiting_human_jobs`のみを呼び`None`を返す(状態変更なし)。指定時は対象Jobを取得し、
    `WAITING_HUMAN`状態かつ対応済み種別(`_RESULT_RESOLVERS`に登録済みのJobType。M4-7時点で
    `issue-analysis`/`design`/`plan`)であることを確認してから`respond_to_job`に委譲する。対象が
    存在しない場合は`JobNotFoundError`、条件を満たさない場合は`InvalidJobTransitionError`を
    送出する(いずれも`job.errors.JobError`のサブクラス)。"""
```

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/cli/single_run.py`。

| 型 | フィールド | 補足 |
|---|---|---|
| `SingleRunResult` (frozen dataclass) | `project: str`, `mr_iid: int`, `sha: str`, `worktree_path: Path`, `review_result: review.ReviewResult`, `review_paths: review.ReviewPaths`, `run_result: runner.RunResult` | 1回の単発レビュー実行の結果。CLIの標準出力サマリの元データ |

`Config`(`config/models.py`)は本Issueで以下のフィールドを追加した(すべて`config.toml`から
読み込む。詳細な既定値・バリデーションは`config/loader.py`/`config/models.py`参照):

| フィールド | `config.toml`上の位置 | 既定値 |
|---|---|---|
| `workspace_root` | `[workspace].root` | `"workspace"` |
| `workspace_max_disk_mb` | `[workspace].max_disk_mb` | `5000` |
| `runner_log_dir` | `[runner].log_dir` | `"logs/runner"` |
| `runner_timeout_seconds` | `[runner].timeout_seconds` | `1800` |
| `reviews_root` | `[reviews].root` | `"reviews"` |
| `state_db_path` | `[store].db_path` | `"state.db"` |
| `job_db_path` | `[job].db_path` | `"job.db"` (M3-1、[specs/job-model.md](job-model.md)) |

`decompose`は`SingleRunResult`に相当する構造化結果を持たない。対話型セッションのため成否は
人間が直接判断し、`run_decompose`は`claude`プロセスの終了コード(`int`)を返すだけである。

## 処理の流れ(`execute_review`)

`review`/`watch`いずれも実際の呼び出し口は`execute_review_job`(M3-1)であり、以下の手順の
前後にJobのライフサイクル管理が挟まる: `job_repo.enqueue(REVIEW_JOB_TYPE, ...)`で`PENDING`の
Jobを起票し、`RUNNING`へ更新してから手順1に入る。手順7が`DONE`に更新して`SingleRunResult`を
返した後、Jobも`DONE`(`result`に結果保存先パス等を記録)へ更新する。手順1〜7のいずれかで
例外が発生した場合はJobも`FAILED`へ更新してから元の例外を再送出する。以下の手順自体
(State Storeとのやり取り)は`execute_review`本体の責務のままで変更していない
(詳細: [specs/job-model.md](job-model.md))。

1. `adapter.get_merge_request` / `get_merge_request_diffs` / `list_merge_request_discussions`で
   対象MRの詳細・diff・コメントを取得する。ここで失敗した場合、State Storeにはまだ触れず
   例外をそのまま伝播させる
2. 起票・worktree準備・保存に使う`sha`を決める: 呼び出し側が`sha`を指定していればそれを、
   省略していれば`merge_request.sha`(取得時点の最新commit)を使う。`(project, mr_iid, sha)`を
   `StateStatus.RUNNING`として起票する。既存レコードがあれば(MR Pollerと異なり無視せず)
   `RUNNING`へ上書きする(単発実行は同一commitへの繰り返し実行が主要なユースケースであるため、
   [ADR-0008](../adr/0008-cli-single-run-design.md)参照)
3. `workspace.prepare(project, mr_iid, sha)`でworktreeを用意する
4. `review.build_review_instructions()`でinstructionsを組み立て、
   `runner.run(worktree.path, instructions, context, ...)`でClaude Codeをヘッドレス実行する
5. `review.parse_review_output(run_result)`で結果をパースする
6. `runner.build_prompt(instructions, context)`でRunnerに渡した完成後のプロンプト全文を
   再現し、`review.save_review(config.reviews_root, ...)`で結果・入力プロンプト・実行ログを
   `reviews/<project>/<mr_iid>/<sha>/`へ保存する
7. 手順3〜6のいずれかで例外が発生した場合は`(project, mr_iid, sha)`を`FAILED`に更新してから
   元の例外を再送出する。全て成功した場合は`DONE`に更新し(`reviewed_at`/`result_path`も
   記録)、`SingleRunResult`を返す

## 処理の流れ(`watch`: `run_watch_loop`/`build_on_detected`)

1. `stop_event`省略時は`threading.Event()`を生成する。`MrPoller(adapter, store,
   config.projects, review_label=config.review_label)`と`ReviewWorkerPool(config.max_parallel,
   stop_event)`(M2-1)を構築する
2. `poller.run(interval_seconds=config.poll_interval_seconds, stop_event=stop_event,
   on_detected=<pool.submitへ投入するラッパー>)`で`config.poll_interval_seconds`間隔の
   走査ループを開始する(ループ制御自体はMR Poller、`docs/specs/poller.md`の責務)
3. 各サイクルで新たに起票された`DetectedReview`ごとに、`build_on_detected(...)`が組み立てた
   コールバック(1件のMRを同期的に処理する関数)を`ReviewWorkerPool`へ投入する。プールは
   `config.max_parallel`個までのワーカースレッドで並行実行する(M2-1、
   [ADR-0015](../adr/0015-parallel-review-execution.md))。投入自体は即座に戻るため、
   `poller.run`のループはブロックされない
4. 投入されたコールバックは、`execution_id_scope()`で新しい実行IDを振ってから
   `execute_review_job(job_repo, adapter, workspace, runner, store, config,
   review.project, review.mr_iid, sha=review.commit_sha)`を呼ぶ(`review`サブコマンドと
   同じパイプライン本体(`execute_review`)をJob経由で再利用する、M3-1)。`sha`にMR Pollerが
   検出・起票した時点のcommitを明示的に渡すことで、`execute_review`が実行時点の最新commitを
   取得し直して別のcommitとして起票し直してしまい、Pollerが起票した元のレコードが
   `RUNNING`/`FAILED`/`DONE`に一度も遷移せず孤立する事態を防ぐ
5. 既知のパイプライン例外(`GitLabAdapterError`/`WorkspaceError`/`RunnerError`/
   `ReviewError`/`StateStoreError`)はログ(`watch.review_failed`)に記録して次のMRの
   処理を続ける。State Storeは`execute_review`が既に`FAILED`へ更新済みのため、
   このレコードは以降のサイクルで「処理済み」としてMR Pollerがスキップする
   (自動リトライはしない)。この処理は`ReviewWorkerPool`から見れば1件のジョブが正常に
   `return`しただけであり、他のMRの処理には一切影響しない(Issue #80の「失敗時の隔離」)
6. 上記5種類に属さない想定外の例外は`ReviewWorkerPool`が捕まえ、`stop_event`をセットして
   `run_watch_loop`の外(`run_watch`→`cli.main`)へそのまま伝播させ、プロセスを終了させる
   ([ADR-0009](../adr/0009-cli-watch-design.md)「1件のレビュー失敗はログに記録して継続する。
   想定外の例外はプロセスを落とす」を並列実行後も維持する設計、[ADR-0015](../adr/0015-parallel-review-execution.md)参照)。
   このとき、既に実行が始まっている他のMRの処理は中断されず完了まで実行される
7. `stop_event`がセットされると、`poller.run`は実行中のサイクル完了後にループを終了する。
   `run_watch_loop`は`finally`節で`pool.shutdown_and_reraise()`を呼び、投入済みジョブの
   完了を待ってから(未着手のジョブはキャンセルする)、手順6で保持していた例外があれば
   再送出する

## 処理の流れ(`worker`: `RunnerDispatcher`、M3-3)

`run_dispatcher`(合成ルート)は`config`から具象実装を組み立て、`RunnerDispatcher.run_forever`
(`--once`指定時は`run_once`を1回だけ)に委譲する([ADR-0022](../adr/0022-runner-process-separation.md))。

1. `stop_event`がセットされるまで、`job_repo.claim(worker_id, job_types=...,
   visibility_timeout_seconds=...)`を呼ぶ。`job_types`省略時は`build_job_handlers`が
   組み立てたディスパッチテーブルに登録済みの種別(M3-3時点では`review`のみ)に絞られる
2. `claim`が`None`を返した場合(処理対象のJobが無い)、`poll_interval_seconds`(既定5秒)
   だけ待ってから手順1へ戻る
3. Jobを取得できた場合、専用のdaemonスレッドを起動し`heartbeat_interval_seconds`
   (既定120秒)ごとに`job_repo.heartbeat(job.id, worker_id, ...)`を呼んでリース
   (可視性タイムアウトの期限)を延長し続ける
4. `job.job_type`に対応する`JobHandler`を`handlers`辞書から引く。無ければ
   `NotImplementedError`を送出する([ADR-0016](../adr/0016-job-abstraction.md)の契約)
5. `review`種別の`JobHandler`(`build_review_handler`)は、`job.payload`から
   `(project, mr_iid, sha)`を取り出し`execute_review`(変更しない)を呼び出し、結果から
   `result`(`dict`)を組み立てて返す
6. `handler`が正常終了した場合は`job_repo.complete(job.id, worker_id, result=result)`を
   呼ぶ。`NotImplementedError`が送出された場合は`job_repo.fail(job.id, worker_id, str(exc),
   retry=False)`(未実装種別はリトライしても結果が変わらないため即デッドレター化)。
   それ以外の例外が送出された場合は`job_repo.fail(job.id, worker_id, str(exc), retry=True)`
   (リトライ判定は`JobRepository`の`attempts`/`max_attempts`に委ねる、[ADR-0017](../adr/0017-job-queue.md))
7. 手順6の完了後、heartbeatスレッドを停止して`join`する。1件のJobの処理結果に関わらず
   例外を再送出せず、手順1へ戻る(1件の失敗が他のJobの処理を止めない。`watch`の
   「想定外の例外はプロセスを落とす」方針とは意図的に異なる、[ADR-0022](../adr/0022-runner-process-separation.md)「却下した選択肢」参照)
8. `stop_event`がセットされると、現在処理中のJob(あれば)の完了を待ってからループを終了する

## 処理の流れ(`api`: `run_api_server`、M3-7)

詳細は[specs/http-api.md](http-api.md)「処理の流れ」を参照。概略:

1. `config.api_token`が空なら`ConfigError`を送出して終了する
2. `SqliteJobRepository(config.job_db_path)`と`ApiServer`(`token`/`host`/`port`は
   `config.api_token`/`api_host`/`api_port`)を組み立て、`start()`する
   (`ThreadingHTTPServer.serve_forever`を背景スレッドで実行)
3. `stop_event`がセットされるまで待つ。セットされたら`ApiServer.stop()`→
   `job_repo.close()`の順で終了する

## 処理の流れ(`decompose`: `run_decompose`)

1. `build_mcp_config(config_path, env_path, ...)`で、GitLab Adapter MCP Serverを登録した
   `--mcp-config`用のJSON構造を組み立てる(`command`は既定で`sys.executable`)
2. `build_claude_command(...)`で、`--mcp-config`(手順1のJSONを`json.dumps`した文字列)・
   `--strict-mcp-config`・`--append-system-prompt`(`build_system_prompt(project)`)・
   任意で`--permission-mode`・末尾に初期プロンプト(`build_initial_prompt(project)`、位置引数)
   を並べたコマンド列を組み立てる。`-p`は付けない
3. `popen(command)`(既定は`subprocess.Popen`)で、stdout/stderrを`PIPE`に繋がずそのまま
   起動する。`FileNotFoundError`(`claude`コマンドが見つからない)の場合のみ
   `ClaudeCommandNotFoundError`を送出する
4. 起動後は人間が直接ターミナルで対話する。`proc.wait()`で`claude`プロセスの終了を待ち、
   その終了コードをそのまま呼び出し元(`cli.main`)へ返す

## 処理の流れ(`respond`: `run_respond`/`respond_to_job`、M4-5、M4-6で`design`対応拡張)

詳細な設計判断は[ADR-0028](../adr/0028-waiting-human-answer-integration.md)・
[ADR-0029](../adr/0029-design-phase.md)、`result`構造の詳細は
[specs/issue-analysis.md](issue-analysis.md)「`WAITING_HUMAN`後の再開」・
[specs/design-phase.md](design-phase.md)を参照。

1. `job_id`省略時: `list_waiting_human_jobs`で`JobRepository.list_by_status(WAITING_HUMAN)`の
   結果をターミナルに表示して終了する(状態変更なし)
2. `job_id`指定時: `job_repo.get(job_id)`で対象Jobを取得する。存在しなければ
   `JobNotFoundError`、`WAITING_HUMAN`状態でなければ`InvalidJobTransitionError`、
   `_RESULT_RESOLVERS`に未登録の`job_type`(M4-6時点では`implement`)であれば同じく
   `InvalidJobTransitionError`を送出する
3. `job.result["questions"]`を1件ずつ提示し、`input()`(既定)で回答を集める
   (`collect_answers`)。この間はJob Repositoryの状態を一切変更しない
4. 回答が揃ってから`update_status(job_id, RUNNING)`を呼ぶ(`WAITING_HUMAN → RUNNING`)
5. `job.job_type`に対応する統合関数(`_RESULT_RESOLVERS[job.job_type]`。`issue-analysis`なら
   `issue_analysis.build_resolved_issue_analysis_job_result`、`design`なら
   `design.build_resolved_design_job_result`)で、`questions`と回答を統合した新しい`result`を
   組み立てる
6. `update_status(job_id, DONE, result=統合後のresult)`でJobを完了させる
7. 手順4〜6のいずれかで例外(`KeyboardInterrupt`含む)が発生した場合は
   `update_status(job_id, FAILED, error=...)`を呼んでから元の例外を再送出する。手順3で
   中断された場合はJobが`WAITING_HUMAN`のまま変化しないため、`respond`をそのまま
   再実行すればよい

## エラー時の振る舞い(`cli/main.py`)

このモジュール自身は独自の例外型を持たない。パイプライン(`review`は
`execute_review`/`run_single_review`、`watch`は`run_watch`、`worker`は`run_dispatcher`、
`api`は`run_api_server`、`decompose`は`run_decompose`、`respond`は`run_respond`)が送出する
例外をそのまま受け取り、`cli/exit_codes.py`の終了コードとエラーメッセージ(標準エラー出力)に
変換する。

| 例外 | 終了コード | サブコマンド | 備考 |
|---|---|---|---|
| `config.ConfigError` | 10 | 全て | `load_config`失敗時。`api`は`config.api_token`が空の場合にも`run_api_server`が送出する。PATの値は含めない(`ConfigError`自体の契約) |
| `gitlab_adapter.errors.GitLabAdapterError` | 11 | review/watch/worker | |
| `workspace.errors.WorkspaceError` | 12 | review/watch/worker | |
| `runner.errors.RunnerError` | 13 | review/watch/worker | `log_path`属性があれば標準エラー出力にあわせて表示する |
| `review.errors.ReviewError` | 14 | review/watch/worker | Claude Codeの応答が結果スキーマを満たさなかった場合等 |
| `store.errors.StateStoreError` | 15 | review/watch/worker | |
| `cli.lock.AlreadyRunningError` | 16 | watch | 同一`state_db_path`に対する多重起動時(`ProcessLock`)。`worker`/`api`は多重起動防止を行わないため該当しない(ADR-0022/ADR-0023) |
| `decompose.ClaudeCommandNotFoundError` | 17 | decompose | 対話型`claude`プロセスの起動自体に失敗した場合(`FileNotFoundError`)。それ以外は`claude`プロセス自身の終了コードをそのまま返す(`docs/adr/0012-decompose-interactive-session.md`) |
| `job.errors.JobError` | 18 | worker/api/respond | `SqliteJobRepository`の構築失敗、または`claim`/`heartbeat`/`complete`/`fail`(worker)・`enqueue`/`get`/`list_by_status`/`list_dead_letters`(api)自体がDB接続不良等でJob Repository起因のエラーを送出した場合。`worker`は個々のJobの処理失敗を`RunnerDispatcher`が、`api`は個々のリクエストのエラーを`ApiServer`がそれぞれ握りつぶし続行するため、通常この経路には来ない(M3-3/[ADR-0022](../adr/0022-runner-process-separation.md)、M3-7/[ADR-0023](../adr/0023-http-api.md))。`respond`は`job_id`未存在(`JobNotFoundError`)・`WAITING_HUMAN`以外の状態や`_RESULT_RESOLVERS`未登録の`job_type`(M4-6時点で`implement`)を指定した場合(`InvalidJobTransitionError`)もここに含まれる(いずれも`JobError`のサブクラス、M4-5/[ADR-0028](../adr/0028-waiting-human-answer-integration.md)、M4-6/[ADR-0029](../adr/0029-design-phase.md)) |
| `KeyboardInterrupt` | 130 | 全て | `watch`/`worker`/`api`はCtrl+C自体を`stop_event`経由のgraceful shutdownに変換するため、通常この経路には来ない。`respond`は回答収集中(`input()`待ち)のCtrl+Cのみ`JobError`経由(`FAILED`更新後に再送出、ADR-0028)ではなくこの経路(130)に乗る |
| 上記以外の例外 | 1 | 全て | 想定外のバグとして扱う(捕捉せず伝播させ、Pythonの既定の終了コード1相当を返す) |

`watch`では、上記5種類のパイプライン例外(11〜15)のうち`run_watch_loop`のループ内
(`build_on_detected`が1件のレビュー実行を包む箇所)で発生したものは、前節の通り
1件のレビュー失敗としてログに記録されプロセスは継続するため、これらの終了コードでは
終了しない。一方、`run_watch`が具象実装(`GitLabRestAdapter`/`GitWorkspaceManager`/
`SqliteStateStore`等)を組み立てる構成段階(ループが始まる前)で同じ5種類の例外が
発生した場合は、`build_on_detected`にまだ捕捉されないため`cli.main`まで伝播し、
`review`と同じ終了コード・エラーメッセージに変換される(例: 不正な`state_db_path`で
`SqliteStateStore`の初期化自体が失敗する場合)。プロセスが継続せず終了するのは
Ctrl+C/SIGTERM(正常終了、終了コード0)、`AlreadyRunningError`(16)、構成段階での
上記5種類の例外(11〜15)、または想定外の例外(1)の場合。

`worker`も同様に、`RunnerDispatcher._process`のループ内(1件のJob処理を包む箇所)で発生した
パイプライン例外は`fail(..., retry=True)`に変換されログに記録されるため、これらの終了コード
では終了しない。`cli.main`まで伝播するのは`run_dispatcher`の構成段階(`GitLabRestAdapter`等の
組み立て)での失敗、またはJob Repository自体の異常(`JobError`、18)、Ctrl+C/SIGTERM
(正常終了、終了コード0)、想定外の例外(1)の場合のみ。

`argparse`の引数エラーは自動的に終了コード`2`・使い方メッセージになる(このCLIでは独自定義しない)。

## テスト方針

実装場所: `tests/gitlab_ai_platform/cli/`(`src/`をミラー、
[ADR-0001](../adr/0001-repository-structure.md))。

- `test_single_run.py`: `GitLabReader` / `WorkspaceManager` / `ClaudeCodeRunner` /
  `StateStore`を満たす手書きフェイクと、実DBの`SqliteStateStore(":memory:")`を組み合わせて
  `execute_review`を実行する(実GitLab・実git・実Claude Code subprocessには繋がない、
  CLAUDE.mdのテスト方針)。以下を検証する:
  - 正常系: `SingleRunResult`の内容、`reviews/`配下への保存、State Storeが`DONE`に
    更新されること
  - `timeout_seconds`/`allowed_tools`/`disallowed_tools`/`permission_mode`が
    そのまま`runner.run`に渡ること、省略時は`config.runner_timeout_seconds`が使われること
  - GitLab Adapterが失敗した場合、State Storeに何も起票されないこと
  - Workspace Manager/Claude Code Runner/Reviewのいずれかが失敗した場合、State Storeが
    `FAILED`に更新されてから元の例外が再送出されること
  - 同一commitへの再実行が`DuplicateReviewError`を発生させず、`RUNNING`→`DONE`の更新を
    やり直せること
  - `_clone_url_for`/`_credential_helper`(GitLab認証の組み立てロジック)を単体で検証し、
    トークンの値そのものが含まれないことを確認する
  - `build_workspace_manager`が既存の`credential.helper`を空値でクリアしてから
    独自の値を設定すること
  - `execute_review_job`(M3-1): `SqliteJobRepository(":memory:")`を組み合わせ、正常系で
    review Jobが`DONE`(`result`にproject/mr_iid/sha/結果保存先パス)へ更新されること、
    `execute_review`が例外を送出した場合にJobが`FAILED`(`error`にメッセージ)へ更新されてから
    元の例外が再送出されること、`sha`が`payload`経由で`execute_review`へ正しく伝わることを
    検証する
- `test_watch.py`: `build_on_detected`/`run_watch_loop`/`run_watch`を検証する(`test_single_run.py`
  と同じくフェイク+実DBの`SqliteStateStore(":memory:")`/`SqliteJobRepository(":memory:")`で、
  実サービスには繋がない)。
  - `build_on_detected`: 正常系で`execute_review_job`相当の結果(State Store・review Jobが
    ともに`DONE`)になること、
    既知のパイプライン例外はログに記録して例外を送出しないこと(呼び出し元を止めない)、
    それ以外の例外はそのまま伝播すること、検出後にMRが別commitへ進んでいても
    `execute_review`には検出時の`sha`(`DetectedReview.commit_sha`)がそのまま渡り、
    その`sha`に対してレビュー・保存が行われること(Pollerが起票したレコードが
    孤立しないこと)
  - `run_watch_loop`: `MrPoller`が検出した複数の`DetectedReview`が処理されること
    (M2-1以降は並行実行のため、完了順序ではなく処理結果の集合で検証する)、
    `config.max_parallel`を超えて同時実行されないこと・実際に複数MRが同時実行される
    (単なる逐次実行ではない)ことを実行中の同時実行数を計測して検証すること、1件のMRの
    想定外の例外が他のMRの処理を妨げず、State Storeが正しく更新されたうえで例外が
    `run_watch_loop`の外へ伝播すること(Issue #80の「失敗時の隔離」)
  - `run_watch`: `ProcessLock`を取得・解放すること、ロック取得済みの状態で呼ぶと
    `AlreadyRunningError`を送出すること、`state_db_path`が`":memory:"`でもロックファイル名が
    不正にならず起動できること(`_lock_path_for`の`":memory:"`特別扱い)
  - M3-6: `config.webhook_enabled=false`(既定)では`run_watch_loop`がWebhookサーバーを
    起動しないこと、`true`の場合は実際にHTTPリクエストを送ってPollerと同じ
    `execute_review_job`パイプラインで処理されState Storeが`DONE`になることを検証する
    ([specs/webhook-receiver.md](webhook-receiver.md)参照)
- `test_worker_pool.py`: `ReviewWorkerPool`を検証する。投入したジョブがバックグラウンド
  スレッドで実行されること、同時実行数が`max_workers`を超えないこと、想定外の例外を
  送出したジョブが`stop_event`をセットし`shutdown_and_reraise`で再送出されること、
  1件のジョブの失敗が他のジョブの実行を妨げないこと(Issue #80の「失敗時の隔離」)を
  検証する
- `test_lock.py`: `ProcessLock`の取得・解放・多重取得時の`AlreadyRunningError`を検証する。
  Windows分岐(`msvcrt`)は開発機がmacOSのため実機検証はできず、`sys.platform`/
  `sys.modules["msvcrt"]`をテスト用のフェイクに差し替えてロジックのみ検証する
  (`references/spike-S3-git-worktree-windows.md`と同様の制約)
- `test_dispatcher.py`(M3-3): `build_review_handler`/`build_job_handlers`を
  `test_single_run.py`/`test_watch.py`と同じ手書きフェイク(`GitLabReader`/`WorkspaceManager`/
  `ClaudeCodeRunner`)+実DBの`SqliteStateStore(":memory:")`で検証する。`RunnerDispatcher`は
  `claim`/`heartbeat`/`complete`/`fail`のみを満たす手書きフェイク`JobRepository`で制御フロー
  (`run_once`の返り値、handler成功時の`complete`呼び出し、例外送出時の
  `fail(..., retry=True)`、未対応JobType(handlerが無い)時の`fail(..., retry=False)`、
  `job_types`省略時に`handlers`登録済みの種別のみをclaim対象にすること、Job処理中の
  定期的な`heartbeat`呼び出し、`heartbeat`が`LeaseLostError`を送出してもhandler本体は
  中断されないこと、`run_forever`の`stop_event`までの空振りポーリング)を検証する。
  `run_dispatcher`(合成ルート)は実サービスに繋がない範囲(`run_once=True`でJobが無い状態、
  `stop_event`を起動前にセットする)で具象実装の組み立てが例外を出さないことを検証する
- `test_api_server.py`(M3-7): `run_api_server`を実DBの`SqliteJobRepository(":memory:")`と
  組み合わせて検証する。`config.api_token`が空の場合に`ConfigError`を送出すること
  (ポートをbindしないこと)、`stop_event`が既にセットされていれば`start`後すぐに`stop`が
  呼ばれ正常に戻ること、`ApiServer`が実際に構築され`server_port`が取得できることを
  実サービスに繋がない範囲で検証する(`api/test_server.py`の詳細な入出力検証は
  [specs/http-api.md](http-api.md)側)
- `test_main.py`: `run_single_review`/`run_watch`/`run_dispatcher`/`run_api_server`/
  `run_decompose`/`run_respond`を`monkeypatch`で差し替え、CLI引数が正しく渡ること、各例外型が
  対応する終了コード・標準エラー出力になること、正常系で標準出力にサマリ(結果パス・指摘件数)が
  表示されることを検証する。`watch`/`worker`/`api`はSIGINT/SIGTERM受信で`stop_event`が
  セットされること、`main`終了後にシグナルハンドラが元へ戻ることも検証する。`watch`も
  `review`と同じ5種類のパイプライン例外(構成段階を想定し`run_watch`自体から送出させる)が
  同じ終了コードへ変換されることをパラメタライズテストで検証する。`worker`は同じ5種類の
  パイプライン例外に加え`JobError`が`EXIT_JOB_ERROR`(18)に変換されることも検証し、
  `--worker-id`/`--job-types`/`--poll-interval`/`--heartbeat-interval`/`--visibility-timeout`/
  `--once`が正しく`run_dispatcher`へ渡ること、省略時は`worker_id`/`job_types`が`None`・
  `run_once`が`False`になること、不正な`--job-types`値・非正数の間隔指定が終了コード`2`
  (argparse)になることを検証する。`api`は`ConfigError`が`EXIT_CONFIG_ERROR`(10)、
  `JobError`が`EXIT_JOB_ERROR`(18)に変換されることを検証する(CLI固有オプションを
  持たないため引数関連のテストは無い)。`decompose`は`run_decompose`の戻り値(`claude`の
  終了コード)がそのままCLIの終了コードになること、`project`/`--permission-mode`/
  `--config`/`--env`が正しく渡ること、`ClaudeCommandNotFoundError`が
  `EXIT_CLAUDE_NOT_FOUND`(17)に変換されること、`ConfigError`が`review`/`watch`/`worker`/
  `api`と同じ`EXIT_CONFIG_ERROR`(10)経路に乗ることを検証する。`respond`(M4-5)は`job_id`が
  位置引数として`run_respond`へ渡ること(省略時は`None`)、`run_respond`が`Job`を返した場合に
  標準出力へJob IDを含むサマリが表示されること、`None`を返した場合(一覧表示のみ)は
  サマリを表示しないこと、`JobError`(`JobNotFoundError`/`InvalidJobTransitionError`含む)が
  `EXIT_JOB_ERROR`(18)に変換されることを検証する
- `test_respond.py`(M4-5): `collect_answers`が質問を順に提示し回答を集めること(Job
  Repositoryを一切呼ばないこと)、`list_waiting_human_jobs`が`WAITING_HUMAN`のJobを一覧表示する
  こと(空の場合は「ありません」の旨を表示すること)、`respond_to_job`を実DBの
  `SqliteJobRepository`(`enqueue`→`claim`→`wait_for_human`でWAITING_HUMAN状態を作る)と
  組み合わせ、`WAITING_HUMAN → RUNNING → DONE`と正しく遷移し統合後の`result`が永続化される
  ことを検証する。回答収集中(`ask`呼び出し中)に`KeyboardInterrupt`を送出させても
  Job Repositoryが一切呼ばれずJobが`WAITING_HUMAN`のまま変化しないこと、`RUNNING`遷移後の
  失敗(`update_status(DONE)`だけを失敗させる手書きフェイク`JobRepository`で再現)では
  `FAILED`へ更新されてから元の例外が再送出されることを検証する。`run_respond`(合成ルート)は
  `job_id`省略時に一覧表示のみで状態変更しないこと、存在しない`job_id`で`JobNotFoundError`、
  `WAITING_HUMAN`以外の状態や`_RESULT_RESOLVERS`未登録の`job_type`(`review`/`implement`)を
  指定すると`InvalidJobTransitionError`を送出すること、正常系で`DONE`のJobを返すことを検証する
  (`unittest.mock`は使わず手書きフェイク、CLAUDE.mdのテスト方針)。M4-6(ADR-0029)で追加した
  `design`種別Jobについても、`issue-analysis`と同様に`WAITING_HUMAN → RUNNING → DONE`と正しく
  遷移し`design.build_resolved_design_job_result`で統合された`result`が永続化されることを
  検証する
- `test_decompose.py`: 実`claude`・実MCPサーバーには繋がない(CLAUDE.mdのテスト方針)。
  `build_mcp_config`が`--config`/`--env`パスを`adapter_mcp_server`起動コマンドへ引き継ぎ、
  GitLab PAT等の値を一切含まないこと・`--log-dir`が指定時のみ付与されること、
  `build_system_prompt`/`build_initial_prompt`が対象プロジェクトを含むこと、
  `build_claude_command`が`-p`を付けず`--mcp-config`/`--strict-mcp-config`/
  `--append-system-prompt`/(任意で)`--permission-mode`/末尾の初期プロンプトを正しく
  並べること、`run_decompose`がフェイクの`popen`に対して組み立てたコマンドで起動し
  `proc.wait()`の戻り値をそのまま返すこと、`popen`が`FileNotFoundError`を送出した場合に
  `ClaudeCommandNotFoundError`に変換されることを検証する
- `test_exit_codes.py`: 終了コードの値が重複しないこと、`argparse`が使う`2`と衝突しないことを
  検証する

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表のCLI行、
  「データフロー(MVP)」2〜9
- [ADR-0008: CLI 単発レビュー実行の設計](../adr/0008-cli-single-run-design.md)
- [ADR-0009: CLI 常駐(watch)モードの設計](../adr/0009-cli-watch-design.md)
- [ADR-0012: 要件→Issue分解ワークフロー(`decompose`)の対話型セッション設計](../adr/0012-decompose-interactive-session.md)
- [ADR-0015: 並列レビュー実行の設計](../adr/0015-parallel-review-execution.md) —
  `watch`の`ReviewWorkerPool`による並列実行の設計判断
- [ADR-0022: Runner のプロセス分離(Runner Dispatcher)の設計](../adr/0022-runner-process-separation.md) —
  `worker`サブコマンド・`RunnerDispatcher`の設計判断
- [ADR-0023: 最小限の HTTP API / サーバ層の設計](../adr/0023-http-api.md) —
  `api`サブコマンド・`ApiServer`の設計判断
- [ADR-0028: `WAITING_HUMAN`後の回答取り込み・Job完了の設計](../adr/0028-waiting-human-answer-integration.md) —
  `respond`サブコマンドの設計判断
- [ADR-0029: 設計フェーズの出力先とRunner実行方式の設計](../adr/0029-design-phase.md) —
  `respond`の`design`種別Jobへの対応拡張
- [poller.md](poller.md) — `watch`が結線するMR Pollerの仕様(`on_detected`コールバック)
- [webhook-receiver.md](webhook-receiver.md) — `watch`が任意有効化で結線するWebhook受信
  サーバー(M3-6)の仕様
- [ADR-0018: Webhook 受信対応(任意有効化)の設計](../adr/0018-webhook-receiver.md)
- [http-api.md](http-api.md) — `api`サブコマンドが起動する最小限のHTTP API(M3-7)の仕様
- [gitlab-adapter.md](gitlab-adapter.md) / [workspace-manager.md](workspace-manager.md) /
  [claude-code-runner.md](claude-code-runner.md) / [review-output.md](review-output.md) /
  [state-store.md](state-store.md) / [job-model.md](job-model.md) —
  このCLIが結線する各コンポーネントの仕様(`job-model.md`は`worker`が呼び出す
  `claim`/`heartbeat`/`complete`/`fail`の詳細)
- [adapter-mcp-server.md](adapter-mcp-server.md) — `decompose`が`--mcp-config`で登録する
  GitLab Adapter MCP Serverの仕様
- [issue-analysis.md](issue-analysis.md) — `respond`が結線する`issue-analysis`のresult構造
  (`WAITING_HUMAN`後の再開、M4-5)
- [design-phase.md](design-phase.md) — `respond`が結線する`design`のresult構造
  (`WAITING_HUMAN`後の再開、M4-6)
- `references/spike-S3-git-worktree-windows.md` §8.1 — GitLab認証(credential helper)の
  実機検証結果
- ソースコード: `src/gitlab_ai_platform/cli/`
  (`main.py` / `single_run.py` / `watch.py` / `worker_pool.py` / `dispatcher.py` /
  `api_server.py` / `decompose.py` / `respond.py` / `lock.py` / `exit_codes.py` /
  `__main__.py` / `__init__.py`)、`src/gitlab_ai_platform/api/`
  (`server.py` / `errors.py` / `__init__.py`)
