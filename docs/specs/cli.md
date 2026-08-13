# CLI

- 実装場所: `src/gitlab_ai_platform/cli/`
- 対応Issue: [#38](https://github.com/AtsushiNi/gitlab-ai-platform/issues/38) (M1-10)、
  [#39](https://github.com/AtsushiNi/gitlab-ai-platform/issues/39) (M1-11)
- 関連ADR: [ADR-0008](../adr/0008-cli-single-run-design.md)、
  [ADR-0009](../adr/0009-cli-watch-design.md)
- ステータス: 実装済み(単発レビュー実行`review`サブコマンド、常駐`watch`サブコマンド)

## 責務

2つのサブコマンドを提供する:

- `review`: 指定した1つのproject/MRに対し、GitLab Adapter → Workspace Manager →
  Review(プロンプト) → Claude Code Runner → Review(パース・保存) → State Storeという
  一連のパイプラインを1回だけ実行する。「デバッグとプロンプト改善の主要導線」
  (`docs/architecture.md`)として、結果の保存先パスと簡単なサマリを標準出力に表示する
- `watch`: MR Poller(M1-5)で対象プロジェクトを定期走査し、検出したMRごとに`review`と
  同じレビュー実行パイプラインを呼び出し続ける常駐モード。Ctrl+C(SIGINT)/SIGTERMで
  graceful shutdownし、同一設定に対する多重起動を防ぐ

## 前提と非対象

- 前提:
  - `config.toml` + `.env`から読み込んだ`Config`(`config/models.py`。M1-10でWorkspace
    Manager/Claude Code Runner/Review/State Store向けのフィールドを追加済み)が利用可能
    であること
  - `claude` CLIがPATH上で実行可能であること、Bedrock認証が環境変数経由で設定済みであること
    (Claude Code Runnerの前提, `docs/specs/claude-code-runner.md`と同じ)
  - 対象プロジェクトへのgit clone/fetchがネットワーク的に到達可能であること
- 非対象:
  - オーケストレーション(Job間の遷移)はしない(`docs/architecture.md`のCLIの境界)
  - `review`はMR Pollerによる複数MR横断の走査はしない。`project`/`mr_iid`は呼び出し時に
    人間が指定する
  - GitLabへの自動コメント投稿はしない(Review, M1-9の境界を継承)
  - `watch`は失敗したレビューの自動リトライ・監視・プロセス再起動はしない
    (`docs/adr/0009-cli-watch-design.md`。M3以降のLinux/Docker移行後のスコープ)

## 公開インターフェース

### コマンド

```
gitlab-ai-platform [--config PATH] [--env PATH] [--log-level LEVEL] [--log-dir DIR] \
    review <project> <mr_iid> \
    [--timeout SECONDS] \
    [--allowed-tools TOOL [TOOL ...]] \
    [--disallowed-tools TOOL [TOOL ...]] \
    [--permission-mode MODE]

gitlab-ai-platform [--config PATH] [--env PATH] [--log-level LEVEL] [--log-dir DIR] watch
```

`pip install -e .`後は`gitlab-ai-platform`(`[project.scripts]`)として、それ以外でも
`python -m gitlab_ai_platform.cli`として実行できる。

| 引数/オプション | 必須 | 既定値 | 説明 |
|---|---|---|---|
| `--config` | - | `config.toml` | 設定ファイルのパス(`config.load_config`にそのまま渡す) |
| `--env` | - | `.env` | シークレットファイルのパス |
| `--log-level` | - | `INFO` | ルートロガーのログレベル |
| `--log-dir` | - | なし(コンソールのみ) | 構造化ログ(JSON、日次ローテーション)の出力先 |
| `project`(review) | ✓ | - | GitLabのプロジェクトパス(`group/project`形式) |
| `mr_iid`(review) | ✓ | - | MRのIID |
| `--timeout`(review) | - | `config.toml`の`runner.timeout_seconds` | Claude Codeのタイムアウト秒数 |
| `--allowed-tools`(review) | - | 空 | `claude --allowedTools`に対応 |
| `--disallowed-tools`(review) | - | 空 | `claude --disallowedTools`に対応 |
| `--permission-mode`(review) | - | なし | `claude --permission-mode`に対応 |

`watch`はサブコマンド固有の引数を持たない。走査対象プロジェクト・ポーリング間隔・
レビュー待ちラベル等はすべて`config.toml`(`Config`)から読む
(`--timeout`等をMR単位で都度変えるユースケースは想定していない。デバッグ用途は
`review`を使う)。

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
分離パターンをそのまま踏襲する([ADR-0009](../adr/0009-cli-watch-design.md))。

```python
import threading

from gitlab_ai_platform.config import Config
from gitlab_ai_platform.gitlab_adapter.protocol import GitLabReader
from gitlab_ai_platform.poller import DetectedReview
from gitlab_ai_platform.runner.protocol import ClaudeCodeRunner
from gitlab_ai_platform.store.protocol import StateStore
from gitlab_ai_platform.workspace.protocol import WorkspaceManager


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
    4つの依存先はすべてProtocol型で受け取る(具象実装に依存しない)。"""


def run_watch(config: Config, *, stop_event: threading.Event | None = None) -> None:
    """合成ルート。`config`から具象実装を組み立て、`ProcessLock`(多重起動防止、
    `cli/lock.py`)を取得してから`run_watch_loop`に委譲する。"""
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

1. `MrPoller(adapter, store, config.projects, review_label=config.review_label)`を構築する
2. `poller.run(interval_seconds=config.poll_interval_seconds, stop_event=stop_event,
   on_detected=build_on_detected(...))`で`config.poll_interval_seconds`間隔の走査ループを
   開始する(ループ制御自体はMR Poller、`docs/specs/poller.md`の責務)
3. 各サイクルで新たに起票された`DetectedReview`ごとに、`execution_id_scope()`で新しい
   実行IDを振ってから`execute_review(adapter, workspace, runner, store, config,
   review.project, review.mr_iid, sha=review.commit_sha)`を呼ぶ(`review`サブコマンドと
   同じパイプライン本体を再利用。`sha`にMR Pollerが検出・起票した時点のcommitを明示的に
   渡すことで、`execute_review`が実行時点の最新commitを取得し直して別のcommitとして
   起票し直してしまい、Pollerが起票した元のレコードが`RUNNING`/`FAILED`/`DONE`に
   一度も遷移せず孤立する事態を防ぐ)
4. 既知のパイプライン例外(`GitLabAdapterError`/`WorkspaceError`/`RunnerError`/
   `ReviewError`/`StateStoreError`)はログ(`watch.review_failed`)に記録して次のMRの
   処理を続ける。State Storeは`execute_review`が既に`FAILED`へ更新済みのため、
   このレコードは以降のサイクルで「処理済み」としてMR Pollerがスキップする
   (自動リトライはしない)
5. 上記5種類に属さない想定外の例外は握りつぶさず、`run_watch_loop`の外(`run_watch`
   → `cli.main`)へそのまま伝播させ、プロセスを終了させる([ADR-0009](../adr/0009-cli-watch-design.md)
   「1件のレビュー失敗はログに記録して継続する。想定外の例外はプロセスを落とす」)
6. `stop_event`がセットされると、実行中のサイクル(検出された全MRの処理)完了後に
   ループを終了する

## エラー時の振る舞い(`cli/main.py`)

このモジュール自身は独自の例外型を持たない。パイプライン(`review`は
`execute_review`/`run_single_review`、`watch`は`run_watch`)が送出する例外をそのまま
受け取り、`cli/exit_codes.py`の終了コードとエラーメッセージ(標準エラー出力)に変換する。

| 例外 | 終了コード | サブコマンド | 備考 |
|---|---|---|---|
| `config.ConfigError` | 10 | 両方 | `load_config`失敗時。PATの値は含めない(`ConfigError`自体の契約) |
| `gitlab_adapter.errors.GitLabAdapterError` | 11 | 両方 | |
| `workspace.errors.WorkspaceError` | 12 | 両方 | |
| `runner.errors.RunnerError` | 13 | 両方 | `log_path`属性があれば標準エラー出力にあわせて表示する |
| `review.errors.ReviewError` | 14 | 両方 | Claude Codeの応答が結果スキーマを満たさなかった場合等 |
| `store.errors.StateStoreError` | 15 | 両方 | |
| `cli.lock.AlreadyRunningError` | 16 | watch | 同一`state_db_path`に対する多重起動時(`ProcessLock`) |
| `KeyboardInterrupt` | 130 | 両方 | `watch`はCtrl+C自体を`stop_event`経由のgraceful shutdownに変換するため、通常この経路には来ない |
| 上記以外の例外 | 1 | 両方 | 想定外のバグとして扱う(捕捉せず伝播させ、Pythonの既定の終了コード1相当を返す) |

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
  - `run_watch_loop`: `MrPoller`が検出した複数の`DetectedReview`を順に処理すること
  - `run_watch`: `ProcessLock`を取得・解放すること、ロック取得済みの状態で呼ぶと
    `AlreadyRunningError`を送出すること、`state_db_path`が`":memory:"`でもロックファイル名が
    不正にならず起動できること(`_lock_path_for`の`":memory:"`特別扱い)
- `test_lock.py`: `ProcessLock`の取得・解放・多重取得時の`AlreadyRunningError`を検証する。
  Windows分岐(`msvcrt`)は開発機がmacOSのため実機検証はできず、`sys.platform`/
  `sys.modules["msvcrt"]`をテスト用のフェイクに差し替えてロジックのみ検証する
  (`references/spike-S3-git-worktree-windows.md`と同様の制約)
- `test_main.py`: `run_single_review`/`run_watch`を`monkeypatch`で差し替え、CLI引数が正しく
  渡ること、各例外型が対応する終了コード・標準エラー出力になること、正常系で標準出力に
  サマリ(結果パス・指摘件数)が表示されることを検証する。`watch`はSIGINT/SIGTERM受信で
  `stop_event`がセットされること、`main`終了後にシグナルハンドラが元へ戻ることも検証する。
  `watch`も`review`と同じ5種類のパイプライン例外(構成段階を想定し`run_watch`自体から
  送出させる)が同じ終了コードへ変換されることをパラメタライズテストで検証する
- `test_exit_codes.py`: 終了コードの値が重複しないこと、`argparse`が使う`2`と衝突しないことを
  検証する

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表のCLI行、
  「データフロー(MVP)」2〜9
- [ADR-0008: CLI 単発レビュー実行の設計](../adr/0008-cli-single-run-design.md)
- [ADR-0009: CLI 常駐(watch)モードの設計](../adr/0009-cli-watch-design.md)
- [poller.md](poller.md) — `watch`が結線するMR Pollerの仕様(`on_detected`コールバック)
- [gitlab-adapter.md](gitlab-adapter.md) / [workspace-manager.md](workspace-manager.md) /
  [claude-code-runner.md](claude-code-runner.md) / [review-output.md](review-output.md) /
  [state-store.md](state-store.md) — このCLIが結線する各コンポーネントの仕様
- `references/spike-S3-git-worktree-windows.md` §8.1 — GitLab認証(credential helper)の
  実機検証結果
- ソースコード: `src/gitlab_ai_platform/cli/`
  (`main.py` / `single_run.py` / `watch.py` / `lock.py` / `exit_codes.py` /
  `__main__.py` / `__init__.py`)
