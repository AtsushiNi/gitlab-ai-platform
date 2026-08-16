# CLIリファレンス

- ステータス: 完了
- 対応Issue: [#20](https://github.com/AtsushiNi/gitlab-ai-platform/issues/20) (D-16)

`gitlab-ai-platform`コマンドの全サブコマンド・オプション・終了コードのリファレンス。
何ができるツールか・最初の一歩は[getting-started.md](getting-started.md)、日々の運用フローは
[review-workflow.md](review-workflow.md)を参照。仕様としての一次情報は
[specs/cli.md](../specs/cli.md)(実装場所: `src/gitlab_ai_platform/cli/`)。

## 起動方法

`pip install -e .`後は`[project.scripts]`により`gitlab-ai-platform`コマンドとして実行できる。
それ以外の環境でも`python -m gitlab_ai_platform.cli`として同じ引数で実行できる。

```powershell
gitlab-ai-platform review group/project-a 123
# または
python -m gitlab_ai_platform.cli review group/project-a 123
```

## コマンド全体の構文

```
gitlab-ai-platform [--config PATH] [--env PATH] [--log-level LEVEL] [--log-dir DIR] \
    review <project> <mr_iid> \
    [--timeout SECONDS] \
    [--allowed-tools TOOL [TOOL ...]] \
    [--disallowed-tools TOOL [TOOL ...]] \
    [--permission-mode MODE]

gitlab-ai-platform [--config PATH] [--env PATH] [--log-level LEVEL] [--log-dir DIR] watch
```

`--config`/`--env`/`--log-level`/`--log-dir`は共通オプションで、サブコマンド名(`review`/`watch`)
より**前**に指定する。

## サブコマンド

2つのサブコマンドを提供する([specs/cli.md](../specs/cli.md)「責務」):

- **`review`**: 指定した1つのproject/MRに対し、GitLab Adapter → Workspace Manager →
  Review(プロンプト) → Claude Code Runner → Review(パース・保存) → State Storeという
  一連のパイプラインを1回だけ実行する。「デバッグとプロンプト改善の主要導線」
  ([architecture.md](../architecture.md))として、結果の保存先パスと簡単なサマリを
  標準出力に表示する
- **`watch`**: MR Poller(M1-5)で対象プロジェクトを定期走査し、検出したMRごとに`review`と
  同じレビュー実行パイプラインを呼び出し続ける常駐モード。Ctrl+C(SIGINT)/SIGTERMで
  graceful shutdownし、同一設定に対する多重起動を防ぐ

このCLI自身はオーケストレーション(Job間の遷移)を行わない。`review`はMR Pollerによる
複数MR横断の走査をせず、`project`/`mr_iid`は呼び出し時に人間が指定する。`watch`は失敗した
レビューの自動リトライ・監視・プロセス再起動をしない
([ADR-0009](../adr/0009-cli-watch-design.md)。M3以降のLinux/Docker移行後のスコープ)。
いずれのサブコマンドもGitLabへの自動コメント投稿はしない(何をしないかの詳細は
[getting-started.md](getting-started.md)「何をしないか」参照)。

## 共通オプション(サブコマンドより前に指定)

| オプション | 必須 | 既定値 | 説明 |
|---|---|---|---|
| `--config PATH` | - | `config.toml` | 設定ファイルのパス。`config.load_config`にそのまま渡す |
| `--env PATH` | - | `.env` | シークレットファイル(GitLab PAT等)のパス |
| `--log-level LEVEL` | - | `INFO` | ルートロガーのログレベル(`DEBUG`/`INFO`/`WARNING`等、Python標準`logging`の値がそのまま使える) |
| `--log-dir DIR` | - | なし(コンソール出力のみ) | 構造化ログ(JSON、日次ローテーション)の出力先ディレクトリ。省略時はコンソールへの読みやすいテキスト形式のみ |

`--config`/`--env`の読み込みに失敗した場合(`ConfigError`)、終了コード`10`で終了する
(下記「終了コード」参照)。

## `review`サブコマンド

指定した1つのproject/MRを1本レビューする。デバッグ・プロンプト改善用の主要導線。

```
gitlab-ai-platform review <project> <mr_iid> \
    [--timeout SECONDS] \
    [--allowed-tools TOOL [TOOL ...]] \
    [--disallowed-tools TOOL [TOOL ...]] \
    [--permission-mode MODE]
```

### 引数・オプション

| 引数/オプション | 必須 | 既定値 | 説明 |
|---|---|---|---|
| `project` | ✓ | - | GitLabのプロジェクトパス(`group/project`形式) |
| `mr_iid` | ✓ | - | MRのIID(整数) |
| `--timeout SECONDS` | - | `config.toml`の`runner.timeout_seconds`(既定`1800`) | Claude Codeのタイムアウト秒数。正の整数のみ受け付ける(0や負値を指定すると引数エラーになる) |
| `--allowed-tools TOOL [TOOL ...]` | - | 空 | Claude Codeに明示的に許可するツール名。`claude --allowedTools`に対応 |
| `--disallowed-tools TOOL [TOOL ...]` | - | 空 | Claude Codeで禁止するツール名。`claude --disallowedTools`に対応 |
| `--permission-mode MODE` | - | なし | Claude Codeの`--permission-mode`に対応する値をそのまま渡す |

### 実行例

```powershell
gitlab-ai-platform review group/project-a 123
gitlab-ai-platform --log-level DEBUG --log-dir logs review group/project-a 123 --timeout 3600
```

### 正常終了時の出力

標準出力に、保存先パス・指摘件数のサマリを表示する(`cli/main.py`の`_print_summary`)。

```
レビュー完了: group/project-a !123 (abcdef012345)
  概要: <レビュー結果のsummary>
  指摘件数: critical=0 major=2 minor=1
  結果(Markdown): reviews/group/project-a/123/<sha>/result.md
  結果(JSON): reviews/group/project-a/123/<sha>/result.json
  実行ログ: logs/runner/<...>
  worktree: workspace/<...>
```

結果の読み方は[reading-results.md](reading-results.md)を参照。

## `watch`サブコマンド

対象プロジェクトを定期走査し、レビュー待ちMRを検出次第レビューし続ける常駐モード。

```
gitlab-ai-platform watch
```

`watch`はサブコマンド固有の引数を持たない。走査対象プロジェクト・ポーリング間隔・
レビュー待ちラベル等はすべて`config.toml`(`Config`)から読む(`--timeout`等をMR単位で
都度変えるユースケースは想定していない。デバッグ用途は`review`を使う)。

### 終了方法

Ctrl+C(SIGINT)またはSIGTERMを送ると、実行中のサイクル(検出済み全MRの処理)完了後に
graceful shutdownする(終了コード`0`)。

### 多重起動防止

同一`state_db_path`(`config.toml`の`store.db_path`)に対して2つ目の`watch`プロセスを
起動しようとすると、ロック取得に失敗して即座に終了コード`16`で終了する
(`cli/lock.py`の`ProcessLock`、OSのアドバイザリロックを使用)。

## 終了コード

`argparse`が自動的に使う`2`(引数エラー・使い方メッセージ)と衝突しないよう、10番台を
パイプラインの各段階(GitLab Adapter/Workspace/Runner/Review/State Store)専用に割り当てている
(`cli/exit_codes.py`)。どの段階で失敗したかを終了コードだけで判別できるようにし、デバッグ・
自動化スクリプトの両方から失敗箇所を特定しやすくする設計。

| 終了コード | 定数名 | 意味 | 対象例外 | サブコマンド |
|---|---|---|---|---|
| `0` | `EXIT_OK` | 正常終了 | - | 両方 |
| `1` | `EXIT_UNEXPECTED_ERROR` | 想定外のエラー | 上記以外の例外(捕捉せず伝播。Pythonの既定の終了コード1相当) | 両方 |
| `2` | (argparseの既定) | 引数エラー | `argparse`の`ArgumentError`等。このCLIでは独自定義しない | 両方 |
| `10` | `EXIT_CONFIG_ERROR` | 設定読み込みエラー | `config.ConfigError`(`load_config`失敗時。PATの値はエラーメッセージに含まれない) | 両方 |
| `11` | `EXIT_GITLAB_ADAPTER_ERROR` | GitLab Adapterエラー | `gitlab_adapter.errors.GitLabAdapterError` | 両方 |
| `12` | `EXIT_WORKSPACE_ERROR` | Workspace Managerエラー | `workspace.errors.WorkspaceError` | 両方 |
| `13` | `EXIT_RUNNER_ERROR` | Claude Code Runnerエラー | `runner.errors.RunnerError`(`log_path`属性があれば標準エラー出力に実行ログのパスも表示) | 両方 |
| `14` | `EXIT_REVIEW_ERROR` | レビュー結果の解析エラー | `review.errors.ReviewError`(Claude Codeの応答が結果スキーマを満たさなかった場合等) | 両方 |
| `15` | `EXIT_STATE_STORE_ERROR` | State Storeエラー | `store.errors.StateStoreError` | 両方 |
| `16` | `EXIT_ALREADY_RUNNING` | 多重起動エラー | `cli.lock.AlreadyRunningError`(同一`state_db_path`に対する多重起動時) | `watch`のみ |
| `130` | `EXIT_INTERRUPTED` | 中断(Ctrl+C) | `KeyboardInterrupt` | 両方(`watch`は通常SIGINTを`stop_event`経由のgraceful shutdown(終了コード`0`)に変換するため、この経路に来るのは`load_config`中などごく限られたタイミングのみ) |

### `watch`実行中の1件のレビュー失敗は終了コードに現れない

`watch`のループ内(`build_on_detected`が1件のレビュー実行を包む箇所)で発生した`11`〜`15`
5種類のパイプライン例外は、ログ(`watch.review_failed`)に記録してプロセスを継続する
(State Storeは既に`FAILED`へ更新済みのため、以降のサイクルでMR Pollerが「処理済み」として
スキップし、自動リトライはしない)。プロセスが終了コード`11`〜`15`で終了するのは、
`run_watch`が具象実装(`GitLabRestAdapter`/`GitWorkspaceManager`/`SqliteStateStore`等)を
組み立てる構成段階(ループが始まる前、例: 不正な`state_db_path`で`SqliteStateStore`の
初期化自体が失敗する場合)でこれらの例外が発生した場合のみ。詳細は
[specs/cli.md](../specs/cli.md)「エラー時の振る舞い」を参照。

## トラブルシューティングとの関係

終了コードから原因を特定した後の対処法は
[operations/troubleshooting.md](../operations/troubleshooting.md)(ステータス: 未着手の場合は
現時点ではスコープ外)を参照。

## 関連ドキュメント

- [specs/cli.md](../specs/cli.md) — このCLIの仕様(処理の流れ・Python API・テスト方針を含む一次情報)
- [ADR-0008: CLI 単発レビュー実行の設計](../adr/0008-cli-single-run-design.md)
- [ADR-0009: CLI 常駐(watch)モードの設計](../adr/0009-cli-watch-design.md)
- [getting-started.md](getting-started.md) — 何ができるツールか・最初の一歩
- [reading-results.md](reading-results.md) — レビュー結果の読み方
- [operations/configuration.md](../operations/configuration.md) — `config.toml`/`.env`の全項目リファレンス
- ソースコード: `src/gitlab_ai_platform/cli/`(`main.py` / `single_run.py` / `watch.py` /
  `lock.py` / `exit_codes.py` / `__main__.py`)
