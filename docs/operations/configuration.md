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
| `GITLAB_AI_PLATFORM_GITLAB_TOKEN` | なし | 必須 | 自動実行系(`review`単発実行・`watch`のPoller/Webhook経由レビュー実行)用のGitLab PAT。GitLab REST API認証(`PRIVATE-TOKEN`ヘッダ、`gitlab_adapter/rest.py`)、およびgit clone/fetch時のcredential helper経由のPAT供給(`cli/single_run.py`の`_build_workspace_manager`)に使う |
| `GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP` | `GITLAB_AI_PLATFORM_GITLAB_TOKEN`と同じ値 | 省略可 | 対話型GitLab Adapter MCP Server(`adapter_mcp_server/main.py`)専用のGitLab PAT。書き込み操作(branch作成・push・MR/Issue作成等)を含む経路のため、`GITLAB_AI_PLATFORM_GITLAB_TOKEN`(自動実行系、読み取り専用の想定)とは別のトークン・アカウントに分けることを推奨する([operations/security.md §4.1](security.md)、[ADR-0019](../adr/0019-gitlab-token-scoping.md))。未設定の場合は`GITLAB_AI_PLATFORM_GITLAB_TOKEN`にフォールバックする |
| `GITLAB_AI_PLATFORM_WEBHOOK_SECRET` | なし | `webhook.enabled=true`の場合のみ必須 | Webhook受信サーバー(M3-6、[specs/webhook-receiver.md](../specs/webhook-receiver.md))がGitLab Webhookの`X-Gitlab-Token`ヘッダと突き合わせるSecret Token。GitLab側のWebhook設定画面の「Secret token」に同じ値を設定する。GitLab PATとは別の秘密であり、GitLab API認証には使わない |
| `GITLAB_AI_PLATFORM_STORE_POSTGRES_PASSWORD` | なし | 省略可(`store.backend = "postgresql"`の場合に通常必要) | PostgreSQL State Store(M3-5、[specs/state-store.md](../specs/state-store.md)、[ADR-0021](../adr/0021-state-store-postgresql.md))の接続パスワード。ホスト・ポート・DB名・ユーザー名は`config.toml`の`[store.postgres]`に書く。`store.backend = "sqlite"`(既定)の場合は使われない。ローカルDocker Compose等のtrust認証運用ではパスワード無しもありうるため必須にはしていない |

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
| `max_parallel` | `5` | int(正の整数) | 省略可 | `watch`サブコマンドが検出したMRを並行実行する際のワーカースレッド数の上限(M2-1、[specs/cli.md](../specs/cli.md)、[ADR-0015](../adr/0015-parallel-review-execution.md))。`review`単発実行では使われない |

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

M3-5([specs/state-store.md](../specs/state-store.md)、[ADR-0021](../adr/0021-state-store-postgresql.md))で
PostgreSQLにも対応した。`backend`でSQLite/PostgreSQLを切り替える(既定はSQLiteのまま、
Windows運用は無変更で動く)。

| キー | 既定値 | 型 | 必須/省略可 | 影響範囲 |
|---|---|---|---|---|
| `backend` | `"sqlite"` | str(`"sqlite"` \| `"postgresql"`) | 省略可 | `build_state_store`(`store/factory.py`)が構築する具象実装を選ぶ。`"postgresql"`にする場合は`psycopg`が必要(`pip install ".[postgres]"`) |
| `db_path` | `"state.db"` | str | 省略可 | `backend = "sqlite"`の場合のみ使用。`SqliteStateStore`のDBファイルパス。レビュー実行状態(`RUNNING`/`DONE`/`FAILED`)を保持し、同一commitへの二重起票防止にも使う([specs/state-store.md](../specs/state-store.md))。`backend = "postgresql"`でも、`run_watch`の多重起動防止(`ProcessLock`)のロックファイルパスの元として引き続き使われる |

#### `[store.postgres]`

`backend = "postgresql"`の場合のみ使用。パスワードは`.env`/環境変数
(`GITLAB_AI_PLATFORM_STORE_POSTGRES_PASSWORD`、前述)経由。

| キー | 既定値 | 型 | 必須/省略可 | 影響範囲 |
|---|---|---|---|---|
| `host` | `"localhost"` | str | 省略可 | PostgreSQL接続先ホスト |
| `port` | `5432` | int(正の整数) | 省略可 | PostgreSQL接続先ポート |
| `dbname` | `"gitlab_ai_platform"` | str | 省略可 | 接続先DB名 |
| `user` | `"gitlab_ai_platform"` | str | 省略可 | 接続ユーザー名 |

### `[job]`

| キー | 既定値 | 型 | 必須/省略可 | 影響範囲 |
|---|---|---|---|---|
| `db_path` | `"job.db"` | str | 省略可 | `SqliteJobRepository`のDBファイルパス(M3-1)。`review`種別のJobのライフサイクル(`PENDING`/`RUNNING`/`WAITING_HUMAN`/`DONE`/`FAILED`)を保持する。State Store(`[store]`)とは別のDBファイル([specs/job-model.md](../specs/job-model.md)) |

### `[webhook]`

M3-6([specs/webhook-receiver.md](../specs/webhook-receiver.md)、[ADR-0018](../adr/0018-webhook-receiver.md))。
`watch`サブコマンド内で任意有効化されるWebhook受信サーバーの設定。**MR Pollerを置き換えない**
(`enabled=false`が既定で、Pollerのみの従来動作のまま)。

| キー | 既定値 | 型 | 必須/省略可 | 影響範囲 |
|---|---|---|---|---|
| `enabled` | `false` | bool | 省略可 | `true`にすると`watch`サブコマンドがWebhookサーバーを背景スレッドで起動する。`true`の場合、`.env`の`GITLAB_AI_PLATFORM_WEBHOOK_SECRET`が必須になる |
| `host` | `"0.0.0.0"` | str | 省略可 | Webhookサーバーの待受アドレス。社内ネットワーク外に晒さないよう、必要に応じて`127.0.0.1`やリバースプロキシ経由に限定するアドレスへ変更する |
| `port` | `8088` | int(正の整数) | 省略可 | Webhookサーバーの待受ポート |
| `path` | `"/webhook"` | str(`/`始まり) | 省略可 | GitLab側のWebhook URL(`http(s)://<host>:<port><path>`)に設定するパス |

GitLab側の設定手順(対象プロジェクトの「Settings > Webhooks」):

1. URL: `http(s)://<このプロセスが動くホスト>:<port><path>`(既定なら`http://<host>:8088/webhook`)
2. Secret token: `.env`の`GITLAB_AI_PLATFORM_WEBHOOK_SECRET`と同じ値
3. Trigger: **Merge request events** のみを有効化する(Push eventsは不要。
   [ADR-0018](../adr/0018-webhook-receiver.md)「扱うイベントはMerge Request Hookのみ」)
4. SSL verification: TLS終端の有無に応じて設定(本サーバー自体はHTTPのみを話す。
   TLSが必要な場合はリバースプロキシを前段に置く)

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
backend = "sqlite"
db_path = "state.db"

[job]
db_path = "job.db"

[webhook]
enabled = false
host = "0.0.0.0"
port = 8088
path = "/webhook"
```

上記はすべて既定値と同じ値のため、実運用では`[gitlab]`の`url`/`projects`(既定値が無く必須)
以外は省略してよい。`webhook.enabled = true`にする場合は`.env`に
`GITLAB_AI_PLATFORM_WEBHOOK_SECRET`の設定も必要になる。

`store.backend = "postgresql"`にする場合の例(M3以降のLinux/Docker運用を想定、
[ADR-0021](../adr/0021-state-store-postgresql.md)):

```toml
[store]
backend = "postgresql"

[store.postgres]
host = "localhost"
port = 5432
dbname = "gitlab_ai_platform"
user = "gitlab_ai_platform"
```

この場合`psycopg`が必要(`pip install ".[postgres]"`)で、`.env`に
`GITLAB_AI_PLATFORM_STORE_POSTGRES_PASSWORD`の設定も必要になることが多い。

## `.env`の例

```text
# 自動実行系(review/watch)用。read_apiスコープで足りる
GITLAB_AI_PLATFORM_GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
# 対話型GitLab Adapter MCP Server用(省略可、未設定ならGITLAB_AI_PLATFORM_GITLAB_TOKENに
# フォールバック)。書き込みを含むためapiスコープが必要
GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP=glpat-yyyyyyyyyyyyyyyyyyyy
# webhook.enabled = true にする場合のみ必要(GitLab側Webhook設定のSecret tokenと同じ値)
GITLAB_AI_PLATFORM_WEBHOOK_SECRET=whsec-xxxxxxxxxxxxxxxxxxxx
# store.backend = "postgresql" にする場合のみ通常必要
GITLAB_AI_PLATFORM_STORE_POSTGRES_PASSWORD=xxxxxxxxxxxxxxxxxxxx
```

雛形は`.env.example`(リポジトリ直下)を参照。PATのスコープ・アカウント分離・トークン管理の
方針は [operations/security.md](security.md)・[ADR-0019](../adr/0019-gitlab-token-scoping.md)
を参照(D-9・M3-8)。
