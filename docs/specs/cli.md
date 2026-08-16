# CLI

- 実装場所: `src/gitlab_ai_platform/cli/`
- 対応Issue: [#38](https://github.com/AtsushiNi/gitlab-ai-platform/issues/38) (M1-10)、
  [#39](https://github.com/AtsushiNi/gitlab-ai-platform/issues/39) (M1-11)、
  [#48](https://github.com/AtsushiNi/gitlab-ai-platform/issues/48) (M2-11)、
  [#80](https://github.com/AtsushiNi/gitlab-ai-platform/issues/80) (M2-1、`watch`の並列実行)
- 関連ADR: [ADR-0008](../adr/0008-cli-single-run-design.md)、
  [ADR-0009](../adr/0009-cli-watch-design.md)、
  [ADR-0012](../adr/0012-decompose-interactive-session.md)、
  [ADR-0015](../adr/0015-parallel-review-execution.md)
- ステータス: 実装済み(単発レビュー実行`review`サブコマンド、常駐`watch`サブコマンド、
  要件→Issue分解の対話型`decompose`サブコマンド)

## 責務

3つのサブコマンドを提供する:

- `review`: 指定した1つのproject/MRに対し、GitLab Adapter → Workspace Manager →
  Review(プロンプト) → Claude Code Runner → Review(パース・保存) → State Storeという
  一連のパイプラインを1回だけ実行する。「デバッグとプロンプト改善の主要導線」
  (`docs/architecture.md`)として、結果の保存先パスと簡単なサマリを標準出力に表示する
- `watch`: MR Poller(M1-5)で対象プロジェクトを定期走査し、検出したMRごとに`review`と
  同じレビュー実行パイプラインを呼び出し続ける常駐モード。検出した複数MRのレビューは
  `config.max_parallel`個までのワーカースレッドで並行実行する(M2-1、[ADR-0015](../adr/0015-parallel-review-execution.md))。
  Ctrl+C(SIGINT)/SIGTERMでgraceful shutdownし、同一設定に対する多重起動を防ぐ
- `decompose`: 指定した1つのprojectに対し、GitLab Adapter MCP Server(M2-12、
  `adapter_mcp_server`)を`--mcp-config`で登録した**対話型**の`claude`セッションを起動する
  (M2-11、`docs/requirements.md` 3-C)。`review`/`watch`のheadless実行(`-p`付き、標準出力の
  JSONをパース)とは異なり、stdin/stdout/stderrをそのまま人間に継承させ、ターミナルで
  人間とClaude Codeが直接対話しながら新しい開発要件を複数のGitLab Issueへ分解・起票する

## 前提と非対象

- 前提:
  - `config.toml` + `.env`から読み込んだ`Config`(`config/models.py`。M1-10でWorkspace
    Manager/Claude Code Runner/Review/State Store向けのフィールドを追加済み)が利用可能
    であること(`decompose`もこの前提を共有するが、`Config`の値そのものは使わず、
    検証済みの`--config`/`--env`パスをそのまま`adapter_mcp_server`の起動コマンドへ
    引き継ぐだけであることに注意。`docs/adr/0012-decompose-interactive-session.md`)
  - `claude` CLIがPATH上で実行可能であること、Bedrock認証が環境変数経由で設定済みであること
    (Claude Code Runnerの前提, `docs/specs/claude-code-runner.md`と同じ)
  - 対象プロジェクトへのgit clone/fetchがネットワーク的に到達可能であること(`review`/`watch`)
  - `decompose`は人間が対話するため、実行時にターミナル(TTY)を持つWindows端末上での利用を
    想定する(`docs/requirements.md` 3-C、`docs/architecture.md`「Windows/Linuxの分担」)。
    `review`/`watch`と異なり、対象プロジェクトのローカルclone/worktreeが存在することは
    前提にしない(要件がまだIssue化されていない段階から始まるため)
- 非対象:
  - オーケストレーション(Job間の遷移)はしない(`docs/architecture.md`のCLIの境界)
  - `review`はMR Pollerによる複数MR横断の走査はしない。`project`/`mr_iid`は呼び出し時に
    人間が指定する
  - GitLabへの自動コメント投稿はしない(Review, M1-9の境界を継承)
  - `watch`は失敗したレビューの自動リトライ・監視・プロセス再起動はしない
    (`docs/adr/0009-cli-watch-design.md`。M3以降のLinux/Docker移行後のスコープ)
  - `decompose`はIssue分解案の自動決定・無人起票はしない。粒度・優先度・依存関係の判断は
    常に人間が対話の中で下す(`docs/requirements.md` 3-Cの「Bとの違い」)。分解後の
    設計・実装・MR作成(B/M4のスコープ)には進まない

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
    decompose <project> \
    [--permission-mode MODE]
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

`watch`はサブコマンド固有の引数を持たない。走査対象プロジェクト・ポーリング間隔・
レビュー待ちラベル等はすべて`config.toml`(`Config`)から読む
(`--timeout`等をMR単位で都度変えるユースケースは想定していない。デバッグ用途は
`review`を使う)。

`decompose`は`project`(GitLabのプロジェクトパス)を人間が明示指定する。`review`と異なり
`mr_iid`に相当するものは存在しない(要件がまだIssue化されていない段階から始まるため)。
`--allowed-tools`/`--disallowed-tools`は公開しない(対話型セッションでは`claude`自身の
既定の権限確認フロー、または`--permission-mode`で人間がその場で制御する想定のため)。

### Python API

#### `review`(実装場所: `src/gitlab_ai_platform/cli/single_run.py`)

```python
from gitlab_ai_platform.config import Config
from gitlab_ai_platform.gitlab_adapter.protocol import GitLabReader
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
    commitを上書きしないため)。"""


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
    SubprocessClaudeCodeRunner/SqliteStateStoreを組み立て、`execute_review`に委譲する。"""


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
    config: Config,
) -> "Callable[[DetectedReview], None]":
    """`DetectedReview`ごとに`execute_review`を呼ぶコールバックを組み立てる。既知の
    パイプライン例外はログに記録して握りつぶし、想定外の例外はそのまま伝播させる。"""


def run_watch_loop(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
    *,
    stop_event: threading.Event | None = None,
) -> None:
    """パイプライン本体。`MrPoller`と`build_on_detected`を結線する。
    4つの依存先はすべてProtocol型で受け取る(具象実装に依存しない)。検出された各MRの
    処理は`ReviewWorkerPool(config.max_parallel, stop_event)`へ投入し、並行実行する
    (M2-1)。`stop_event`を省略した場合はここで生成し、`MrPoller.run`とプールの両方に
    同じオブジェクトを渡す(ワーカースレッドの想定外の例外がポーリングループの早期終了に
    反映されるようにするため)。"""


def run_watch(config: Config, *, stop_event: threading.Event | None = None) -> None:
    """合成ルート。`config`から具象実装を組み立て、`ProcessLock`(多重起動防止、
    `cli/lock.py`)を取得してから`run_watch_loop`に委譲する。"""
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

`decompose`は`SingleRunResult`に相当する構造化結果を持たない。対話型セッションのため成否は
人間が直接判断し、`run_decompose`は`claude`プロセスの終了コード(`int`)を返すだけである。

## 処理の流れ(`execute_review`)

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
   `execute_review(adapter, workspace, runner, store, config, review.project,
   review.mr_iid, sha=review.commit_sha)`を呼ぶ(`review`サブコマンドと同じパイプライン
   本体を再利用。`sha`にMR Pollerが検出・起票した時点のcommitを明示的に渡すことで、
   `execute_review`が実行時点の最新commitを取得し直して別のcommitとして起票し直して
   しまい、Pollerが起票した元のレコードが`RUNNING`/`FAILED`/`DONE`に一度も遷移せず
   孤立する事態を防ぐ)
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

## エラー時の振る舞い(`cli/main.py`)

このモジュール自身は独自の例外型を持たない。パイプライン(`review`は
`execute_review`/`run_single_review`、`watch`は`run_watch`、`decompose`は`run_decompose`)が
送出する例外をそのまま受け取り、`cli/exit_codes.py`の終了コードとエラーメッセージ
(標準エラー出力)に変換する。

| 例外 | 終了コード | サブコマンド | 備考 |
|---|---|---|---|
| `config.ConfigError` | 10 | 全て | `load_config`失敗時。PATの値は含めない(`ConfigError`自体の契約) |
| `gitlab_adapter.errors.GitLabAdapterError` | 11 | review/watch | |
| `workspace.errors.WorkspaceError` | 12 | review/watch | |
| `runner.errors.RunnerError` | 13 | review/watch | `log_path`属性があれば標準エラー出力にあわせて表示する |
| `review.errors.ReviewError` | 14 | review/watch | Claude Codeの応答が結果スキーマを満たさなかった場合等 |
| `store.errors.StateStoreError` | 15 | review/watch | |
| `cli.lock.AlreadyRunningError` | 16 | watch | 同一`state_db_path`に対する多重起動時(`ProcessLock`) |
| `decompose.ClaudeCommandNotFoundError` | 17 | decompose | 対話型`claude`プロセスの起動自体に失敗した場合(`FileNotFoundError`)。それ以外は`claude`プロセス自身の終了コードをそのまま返す(`docs/adr/0012-decompose-interactive-session.md`) |
| `KeyboardInterrupt` | 130 | 全て | `watch`はCtrl+C自体を`stop_event`経由のgraceful shutdownに変換するため、通常この経路には来ない |
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
- `test_watch.py`: `build_on_detected`/`run_watch_loop`/`run_watch`を検証する(`test_single_run.py`
  と同じくフェイク+実DBの`SqliteStateStore(":memory:")`で、実サービスには繋がない)。
  - `build_on_detected`: 正常系で`execute_review`相当の結果(State Storeが`DONE`)になること、
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
- `test_worker_pool.py`: `ReviewWorkerPool`を検証する。投入したジョブがバックグラウンド
  スレッドで実行されること、同時実行数が`max_workers`を超えないこと、想定外の例外を
  送出したジョブが`stop_event`をセットし`shutdown_and_reraise`で再送出されること、
  1件のジョブの失敗が他のジョブの実行を妨げないこと(Issue #80の「失敗時の隔離」)を
  検証する
- `test_lock.py`: `ProcessLock`の取得・解放・多重取得時の`AlreadyRunningError`を検証する。
  Windows分岐(`msvcrt`)は開発機がmacOSのため実機検証はできず、`sys.platform`/
  `sys.modules["msvcrt"]`をテスト用のフェイクに差し替えてロジックのみ検証する
  (`references/spike-S3-git-worktree-windows.md`と同様の制約)
- `test_main.py`: `run_single_review`/`run_watch`/`run_decompose`を`monkeypatch`で差し替え、
  CLI引数が正しく渡ること、各例外型が対応する終了コード・標準エラー出力になること、正常系で
  標準出力にサマリ(結果パス・指摘件数)が表示されることを検証する。`watch`はSIGINT/SIGTERM
  受信で`stop_event`がセットされること、`main`終了後にシグナルハンドラが元へ戻ることも検証する。
  `watch`も`review`と同じ5種類のパイプライン例外(構成段階を想定し`run_watch`自体から
  送出させる)が同じ終了コードへ変換されることをパラメタライズテストで検証する。`decompose`は
  `run_decompose`の戻り値(`claude`の終了コード)がそのままCLIの終了コードになること、
  `project`/`--permission-mode`/`--config`/`--env`が正しく渡ること、
  `ClaudeCommandNotFoundError`が`EXIT_CLAUDE_NOT_FOUND`(17)に変換されること、
  `ConfigError`が`review`/`watch`と同じ`EXIT_CONFIG_ERROR`(10)経路に乗ることを検証する
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
- [poller.md](poller.md) — `watch`が結線するMR Pollerの仕様(`on_detected`コールバック)
- [gitlab-adapter.md](gitlab-adapter.md) / [workspace-manager.md](workspace-manager.md) /
  [claude-code-runner.md](claude-code-runner.md) / [review-output.md](review-output.md) /
  [state-store.md](state-store.md) — このCLIが結線する各コンポーネントの仕様
- [adapter-mcp-server.md](adapter-mcp-server.md) — `decompose`が`--mcp-config`で登録する
  GitLab Adapter MCP Serverの仕様
- `references/spike-S3-git-worktree-windows.md` §8.1 — GitLab認証(credential helper)の
  実機検証結果
- ソースコード: `src/gitlab_ai_platform/cli/`
  (`main.py` / `single_run.py` / `watch.py` / `worker_pool.py` / `decompose.py` / `lock.py` /
  `exit_codes.py` / `__main__.py` / `__init__.py`)
