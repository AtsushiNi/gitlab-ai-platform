# 設定リファレンス

- ステータス: 実装済み(M1-10時点の`Config`全項目を網羅)
- 対応Issue: [#12](https://github.com/AtsushiNi/gitlab-ai-platform/issues/12) (D-8)

> **設定項目を追加・変更したら、このファイルも同じPR/コミットで更新すること。**
> 一次資料は `src/gitlab_ai_platform/config/loader.py` と `config/models.py`。このファイルは
> それらの内容を「動かす人」向けに一覧化したものであり、実装からずれたら価値がなくなる
> (`docs/README.md`の更新ルール1参照)。

`config.toml`(非シークレットの設定)と`.env`(GitLab PAT等のシークレット)の全項目・既定値・
影響範囲の一覧。導入時の最小手順は [operations/setup-windows.md](setup-windows.md) を、
`review`サブコマンドのCLIオプション一覧は [specs/cli.md](../specs/cli.md) を参照。

## 読み込み方法

- 実装: `src/gitlab_ai_platform/config/loader.py`の`load_config(config_path, env_path)`
- 既定のパス: `config.toml` / `.env`(いずれもカレントディレクトリ相対)。`review`サブコマンドでは
  `--config` / `--env` で変更できる([specs/cli.md](../specs/cli.md)参照)
- `.env`は`KEY=VALUE`形式の最小パーサ(`parse_env_file`)で読む。外部ライブラリは使わない。
  `#`始まりの行・空行は無視し、値の前後の`'`または`"`は取り除く
- `.env`の値は、実際にexportされた環境変数(`os.environ`)で上書きされる。CI・本番実行では
  環境変数を、ローカル開発では`.env`ファイルを使う想定
- `config.toml`が存在しない場合は空扱いになる。ただし`gitlab.url`/`gitlab.projects`には
  既定値が無いため、その場合`ConfigError`になる
- 値が不正な場合(空文字列・非正数・`gitlab.projects`が空配列等)は、全項目分のエラーを
  まとめた`ConfigError`を送出する(1件ずつ直して再実行する手間を減らすため)。PATの値自体は
  例外メッセージに含めない

## シークレット(`.env` または環境変数)

`config.toml`にはGitLab PATを書かない(リポジトリにコミットされうるファイルのため)。

| キー | 既定値 | 必須/省略可 | 影響範囲 |
|---|---|---|---|
| `GITLAB_AI_PLATFORM_GITLAB_TOKEN` | なし | 必須 | GitLab REST API認証(`PRIVATE-TOKEN`ヘッダ、`gitlab_adapter/rest.py`)、およびgit clone/fetch時のcredential helper経由のPAT供給(`cli/single_run.py`の`_build_workspace_manager`)に使う |

## `config.toml`

セクション(`[gitlab]`等)ごとに記載。「環境変数」列があるのはGitLab PATのみで、それ以外は
すべて`config.toml`からのみ読み込む(環境変数での上書きは無い)。

### `[gitlab]`

| キー | 既定値 | 型 | 必須/省略可 | 影響範囲 |
|---|---|---|---|---|
| `url` | なし | str(`http://`または`https://`で始まる) | 必須 | GitLab REST APIのベースURL(`gitlab_adapter/rest.py`の`_api_base`)。git clone URL(`<url>/<project>.git`)の構築にも使う。末尾の`/`は自動で取り除かれる |
| `projects` | なし | `list[str]`(1件以上、空文字列不可) | 必須 | MR Poller(`poller/poller.py`)が走査対象とするプロジェクトパス(`group/project`形式)の一覧。**現時点ではCLIの`review`単発実行(M1-10)からは参照されない。MrPollerを配線するwatchモード(M1-11)向け** |

### `[poller]`

| キー | 既定値 | 型 | 必須/省略可 | 影響範囲 |
|---|---|---|---|---|
| `interval_seconds` | `60` | int(正の整数) | 省略可 | `MrPoller.run(interval_seconds=...)`のポーリング間隔秒。**現時点では未配線(M1-11で使用予定)** |
| `max_parallel` | `5` | int(正の整数) | 省略可 | `Config`には存在するが、**現時点でどのコードからも参照されていない予約フィールド**(将来のレビュー並列実行数の上限用) |

### `[review]`

| キー | 既定値 | 型 | 必須/省略可 | 影響範囲 |
|---|---|---|---|---|
| `label` | `"レビュー待ち"` | str | 省略可 | `MrPoller.poll_once`が`list_merge_requests(labels=(label,))`で絞り込むGitLab MRラベル名。**現時点では未配線(M1-11で使用予定)** |

### `[workspace]`

| キー | 既定値 | 型 | 必須/省略可 | 影響範囲 |
|---|---|---|---|---|
| `root` | `"workspace"` | str | 省略可 | `GitWorkspaceManager`のベースディレクトリ。`<root>/repos/`(bare clone群)と`<root>/worktrees/`(worktree群)を作る([specs/workspace-manager.md](../specs/workspace-manager.md)) |
| `max_disk_mb` | `5000` | int(正の整数、MB単位) | 省略可 | `<root>/worktrees/`配下の合計サイズ上限。超過すると古いworktreeの破棄を試み、それでも収まらない場合はエラー(`DiskLimitExceededError`、[specs/workspace-manager.md](../specs/workspace-manager.md)参照) |

### `[runner]`

| キー | 既定値 | 型 | 必須/省略可 | 影響範囲 |
|---|---|---|---|---|
| `log_dir` | `"logs/runner"` | str | 省略可 | `SubprocessClaudeCodeRunner`が実行ログ(コマンド・stdout・stderr・所要時間)を保存するルート。保存先は`<log_dir>/<projectスラッグ>/mr-<iid>/<sha先頭12桁>-<timestamp>.json`([specs/claude-code-runner.md](../specs/claude-code-runner.md)) |
| `timeout_seconds` | `1800` | int(正の整数) | 省略可 | Claude Code実行のタイムアウト秒。超過するとSIGTERM送信後`ClaudeCodeTimeoutError`。`review`サブコマンドの`--timeout`で実行時に上書きできる([specs/cli.md](../specs/cli.md)) |

### `[reviews]`

| キー | 既定値 | 型 | 必須/省略可 | 影響範囲 |
|---|---|---|---|---|
| `root` | `"reviews"` | str | 省略可 | レビュー結果(JSON/Markdown)の保存先ルート。`save_review`が`<root>/<project>/<mr_iid>/<sha>/`に保存し、`<root>/index.jsonl`に索引を1行追記する([specs/review-output.md](../specs/review-output.md)) |

### `[store]`

| キー | 既定値 | 型 | 必須/省略可 | 影響範囲 |
|---|---|---|---|---|
| `db_path` | `"state.db"` | str | 省略可 | `SqliteStateStore`のDBファイルパス。レビュー実行状態(`RUNNING`/`DONE`/`FAILED`)を保持し、同一commitへの二重起票防止にも使う([specs/state-store.md](../specs/state-store.md)) |

## `config.toml`の例

```toml
[gitlab]
url = "https://gitlab.example.com"
projects = ["group/project-a", "group/project-b"]

[poller]
interval_seconds = 60
max_parallel = 5

[review]
label = "レビュー待ち"

[workspace]
root = "workspace"
max_disk_mb = 5000

[runner]
log_dir = "logs/runner"
timeout_seconds = 1800

[reviews]
root = "reviews"

[store]
db_path = "state.db"
```

上記はすべて既定値と同じ値のため、実運用では`[gitlab]`の`url`/`projects`(既定値が無く必須)
以外は省略してよい。

## `.env`の例

```text
GITLAB_AI_PLATFORM_GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
```

雛形は`.env.example`(リポジトリ直下)を参照。レビュー用途(読み取りのみ)なら`read_api`
スコープで足りる。PATのスコープ・トークン管理の方針は [operations/security.md](security.md)
を参照(D-9、本ドキュメント作成時点では未着手)。
