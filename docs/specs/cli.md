# CLI

- 実装場所: `src/gitlab_ai_platform/cli/`
- 対応Issue: [#38](https://github.com/AtsushiNi/gitlab-ai-platform/issues/38) (M1-10)
- 関連ADR: [ADR-0008](../adr/0008-cli-single-run-design.md)
- ステータス: 実装中(単発レビュー実行`review`サブコマンドのみ。常駐`watch`モードはM1-11で追加)

## 責務

指定した1つのproject/MRに対し、GitLab Adapter → Workspace Manager → Review(プロンプト) →
Claude Code Runner → Review(パース・保存) → State Storeという一連のパイプラインを実行する
`review`サブコマンドを提供する。「デバッグとプロンプト改善の主要導線」
(`docs/architecture.md`)として、結果の保存先パスと簡単なサマリを標準出力に表示する。

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
  - MR Pollerによる複数MR横断の走査はしない。`project`/`mr_iid`は呼び出し時に人間が指定する
  - GitLabへの自動コメント投稿はしない(Review, M1-9の境界を継承)
  - 常駐(watch)モードはM1-11で別サブコマンドとして追加する

## 公開インターフェース

### コマンド

```
gitlab-ai-platform [--config PATH] [--env PATH] [--log-level LEVEL] [--log-dir DIR] \
    review <project> <mr_iid> \
    [--timeout SECONDS] \
    [--allowed-tools TOOL [TOOL ...]] \
    [--disallowed-tools TOOL [TOOL ...]] \
    [--permission-mode MODE]
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
| `--timeout` | - | `config.toml`の`runner.timeout_seconds` | Claude Codeのタイムアウト秒数 |
| `--allowed-tools` | - | 空 | `claude --allowedTools`に対応 |
| `--disallowed-tools` | - | 空 | `claude --disallowedTools`に対応 |
| `--permission-mode` | - | なし | `claude --permission-mode`に対応 |

### Python API

実装場所: `src/gitlab_ai_platform/cli/single_run.py`。

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
    timeout_seconds: int | None = None,
    allowed_tools: "Sequence[str]" = (),
    disallowed_tools: "Sequence[str]" = (),
    permission_mode: str | None = None,
) -> "SingleRunResult":
    """パイプライン本体。4つの依存先はすべてProtocol型で受け取る(具象実装に依存しない)。"""


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
2. `(project, mr_iid, mr.sha)`を`StateStatus.RUNNING`として起票する。既存レコードがあれば
   (MR Pollerと異なり無視せず)`RUNNING`へ上書きする(単発実行は同一commitへの繰り返し
   実行が主要なユースケースであるため、[ADR-0008](../adr/0008-cli-single-run-design.md)参照)
3. `workspace.prepare(project, mr_iid, mr.sha)`でworktreeを用意する
4. `review.build_review_instructions()`でinstructionsを組み立て、
   `runner.run(worktree.path, instructions, context, ...)`でClaude Codeをヘッドレス実行する
5. `review.parse_review_output(run_result)`で結果をパースする
6. `runner.build_prompt(instructions, context)`でRunnerに渡した完成後のプロンプト全文を
   再現し、`review.save_review(config.reviews_root, ...)`で結果・入力プロンプト・実行ログを
   `reviews/<project>/<mr_iid>/<sha>/`へ保存する
7. 手順3〜6のいずれかで例外が発生した場合は`(project, mr_iid, sha)`を`FAILED`に更新してから
   元の例外を再送出する。全て成功した場合は`DONE`に更新し(`reviewed_at`/`result_path`も
   記録)、`SingleRunResult`を返す

## エラー時の振る舞い(`cli/main.py`)

このモジュール自身は独自の例外型を持たない。`execute_review`/`run_single_review`が送出する
各段階の例外をそのまま受け取り、`cli/exit_codes.py`の終了コードとエラーメッセージ
(標準エラー出力)に変換する。

| 例外 | 終了コード | 備考 |
|---|---|---|
| `config.ConfigError` | 10 | `load_config`失敗時。PATの値は含めない(`ConfigError`自体の契約) |
| `gitlab_adapter.errors.GitLabAdapterError` | 11 | |
| `workspace.errors.WorkspaceError` | 12 | |
| `runner.errors.RunnerError` | 13 | `log_path`属性があれば標準エラー出力にあわせて表示する |
| `review.errors.ReviewError` | 14 | Claude Codeの応答が結果スキーマを満たさなかった場合等 |
| `store.errors.StateStoreError` | 15 | |
| `KeyboardInterrupt` | 130 | |
| 上記以外の例外 | 1 | 想定外のバグとして扱う(捕捉せず伝播させ、Pythonの既定の終了コード1相当を返す) |

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
- `test_main.py`: `run_single_review`を`monkeypatch`で差し替え、CLI引数が正しく
  渡ること、各例外型が対応する終了コード・標準エラー出力になること、正常系で標準出力に
  サマリ(結果パス・指摘件数)が表示されることを検証する
- `test_exit_codes.py`: 終了コードの値が重複しないこと、`argparse`が使う`2`と衝突しないことを
  検証する

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表のCLI行、
  「データフロー(MVP)」2〜9
- [ADR-0008: CLI 単発レビュー実行の設計](../adr/0008-cli-single-run-design.md)
- [gitlab-adapter.md](gitlab-adapter.md) / [workspace-manager.md](workspace-manager.md) /
  [claude-code-runner.md](claude-code-runner.md) / [review-output.md](review-output.md) /
  [state-store.md](state-store.md) — このCLIが結線する各コンポーネントの仕様
- `references/spike-S3-git-worktree-windows.md` §8.1 — GitLab認証(credential helper)の
  実機検証結果
- ソースコード: `src/gitlab_ai_platform/cli/`
  (`main.py` / `single_run.py` / `exit_codes.py` / `__main__.py` / `__init__.py`)
