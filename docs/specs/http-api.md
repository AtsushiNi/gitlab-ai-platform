# HTTP API

- 実装場所: `src/gitlab_ai_platform/api/`
- 対応Issue: [#97](https://github.com/AtsushiNi/gitlab-ai-platform/issues/97) (M3-7)
- 関連ADR: [ADR-0023](../adr/0023-http-api.md)
- ステータス: 実装済み

## 責務

`JobRepository`(`job/`)への最小限のHTTP API。Job投入(`POST /jobs`)・状態/結果参照
(`GET /jobs/<id>`)・一覧取得(`GET /jobs?status=...`、`GET /jobs/dead-letters`)を提供する。
`cli/main.py`の`api`サブコマンドから起動される独立した常駐プロセスで、「将来のUIや他ツール
連携の口」(`references/タスク整理.md` M3-7)として、`review`/`watch`/`worker`/`decompose`とは
別の入口を提供する。

## 前提と非対象

- 前提:
  - `cli/api_server.py`の`run_api_server(config, ...)`(合成ルート)から呼ばれること
  - `.env`(または環境変数)の`GITLAB_AI_PLATFORM_API_TOKEN`が設定済みであること
    (未設定の場合、`run_api_server`が`ConfigError`を送出し起動しない。ADR-0023「決定」)
  - `api`サブコマンドを実行するプロセスから`job_db_path`が指すJob DBファイルへアクセス
    できること(`worker`/`review`/`watch`と同じDBファイルを指す設定にすることで、投入した
    Jobを`worker`が処理できる)
- 非対象:
  - Jobの実行(`worker`サブコマンド、[ADR-0022](../adr/0022-runner-process-separation.md)の
    責務)はしない。このAPIはJob Repositoryへの読み書きのみを行う
  - `claim`/`heartbeat`/`complete`/`fail`(Runner Dispatcher専用の操作、
    [ADR-0017](../adr/0017-job-queue.md))は公開しない
  - Webhookサーバー(`webhook/`、M3-6)とは別プロセス・別ポートで動く。同じプロセス内での
    共存はしない([ADR-0023](../adr/0023-http-api.md)「決定」)
  - `GET /jobs`(全件一覧、`status`省略)は提供しない。`status`クエリパラメータは必須
  - ページネーション・レート制限・JWT等の高度な認可機構は持たない(静的トークンの
    定数時間比較のみ)
  - GitLabへの操作(コメント投稿等)は行わない。`payload`/`result`の中身は呼び出し側
    (Job種別ごとの定義、例: `review/job.py`)の責務であり、このAPI自体は関与しない

## 公開インターフェース

```python
from gitlab_ai_platform.job.protocol import JobRepository


class ApiServer:
    """Job Repositoryを操作する最小限のHTTP API(Job投入・状態参照・結果取得・一覧取得)。"""

    def __init__(
        self,
        job_repo: JobRepository,
        *,
        token: str,
        host: str,
        port: int,
    ) -> None:
        """起動はしない(`start`を呼ぶまでポートをbindするのみ)。"""

    @property
    def server_port(self) -> int:
        """実際にbindされたポート番号(`port=0`指定時のOS割り当てを含む、主にテスト用)。"""

    def start(self) -> None:
        """バックグラウンドスレッドでリクエスト処理を開始する。"""

    def stop(self) -> None:
        """リクエスト処理を停止し、リソースを解放する(`start`前に呼んでも安全)。"""
```

`cli/api_server.py`側の合成ルート:

```python
import threading

from gitlab_ai_platform.config import Config


def run_api_server(
    config: Config, *, stop_event: threading.Event | None = None
) -> None:
    """`config`からSqliteJobRepository/ApiServerを組み立てて起動し、`stop_event`が
    セットされるまで待つ。`config.api_token`が空の場合は`ConfigError`を送出する。"""
```

## 入出力スキーマ

### HTTPリクエスト/レスポンス

全エンドポイント共通: `X-Api-Token`ヘッダ(`config.api_token`と`secrets.compare_digest`で
定数時間比較)。不一致・未設定は`401 Unauthorized`。レスポンスボディは常にJSON。

#### `POST /jobs` — Job投入

リクエストボディ:

```json
{"job_type": "review", "payload": {"project": "group/project", "mr_iid": 1}, "max_attempts": 3}
```

| フィールド | 型 | 必須/省略可 | 補足 |
|---|---|---|---|
| `job_type` | `str` | 必須 | `JobType`の値(`review`/`issue-analysis`/`design`/`implement`)のいずれか |
| `payload` | `object` | 必須 | `JobRepository.enqueue`にそのまま渡すJSONオブジェクト |
| `max_attempts` | `int`(正の整数) | 省略可 | 省略時は`JobRepository`の既定値(`DEFAULT_MAX_ATTEMPTS`)を使う |

成功時: `201 Created`、ボディは投入されたJobの表現(下記「Jobの JSON 表現」)。

#### `GET /jobs/<id>` — Job状態/結果参照

成功時: `200 OK`、ボディはJobの JSON 表現。存在しない`id`は`404 Not Found`。

#### `GET /jobs?status=<status>` — 状態別一覧取得

`status`クエリパラメータ必須(`JobStatus`の値。`pending`/`running`/`waiting_human`/`done`/
`failed`)。成功時: `200 OK`、ボディは`{"jobs": [<Jobの JSON 表現>, ...]}`。`status`省略・
不正な値は`400 Bad Request`。

#### `GET /jobs/dead-letters` — デッドレター一覧取得

成功時: `200 OK`、ボディは`{"jobs": [<Jobの JSON 表現>, ...]}`
(`JobRepository.list_dead_letters()`をそのまま返す)。

#### Jobの JSON 表現

`Job`(`job/protocol.py`)の全フィールドをJSON互換の型に変換したもの(`datetime`は
ISO 8601文字列、`JobType`/`JobStatus`は`.value`)。

```json
{
  "id": "b3f6...",
  "job_type": "review",
  "status": "pending",
  "payload": {"project": "group/project", "mr_iid": 1},
  "result": null,
  "error": null,
  "created_at": "2026-08-17T09:00:00+00:00",
  "updated_at": "2026-08-17T09:00:00+00:00",
  "attempts": 0,
  "max_attempts": 3,
  "lease_owner": null,
  "lease_expires_at": null,
  "dead_letter_at": null
}
```

### ステータスコード一覧

| ステータスコード | 状況 |
|---|---|
| `200 OK` | `GET`の正常系 |
| `201 Created` | `POST /jobs`の正常系 |
| `400 Bad Request` | リクエストボディがJSONとして不正、`job_type`/`payload`欠如や型不正、`status`クエリパラメータの欠如・不正値、`max_attempts`が正の整数でない |
| `401 Unauthorized` | `X-Api-Token`ヘッダが設定済みトークンと不一致(未設定含む) |
| `404 Not Found` | 未知のパス、または存在しない`job_id`への`GET /jobs/<id>` |
| `500 Internal Server Error` | `JobRepository`操作が`JobError`を送出した(DB接続不良等) |

`config`(`config/models.py`)には以下のフィールドを追加した(既定値・バリデーションの詳細は
[configuration.md](../operations/configuration.md)参照):

| フィールド | `config.toml`上の位置 | 既定値 |
|---|---|---|
| `api_host` | `[api].host` | `"127.0.0.1"` |
| `api_port` | `[api].port` | `8090` |
| `api_token` | `.env`の`GITLAB_AI_PLATFORM_API_TOKEN` | なし(`api`サブコマンド起動時は必須) |

## 処理の流れ

1. `cli/main.py`の`api`サブコマンドが`cli/api_server.py`の`run_api_server(config, ...)`を呼ぶ
2. `run_api_server`は`config.api_token`が空なら`ConfigError`を送出して終了する。それ以外は
   `SqliteJobRepository(config.job_db_path)`と`ApiServer`を組み立て、`start()`する
   (`ThreadingHTTPServer.serve_forever`を背景スレッドで実行)
3. リクエストを受信すると、`ApiServer.handle_get`/`handle_post`が以下の順で処理する:
   1. `X-Api-Token`を`secrets.compare_digest`で定数時間比較。不一致なら`401`
   2. パスに応じて`job_repo.enqueue`/`get`/`list_by_status`/`list_dead_letters`のいずれかを
      呼ぶ(リクエストの検証エラーは`400`、`JobRepository`起因のエラー(`JobError`)は`500`)
   3. 結果をJobの JSON 表現(または一覧)に変換して返す
4. `stop_event`(SIGINT/SIGTERM経由、`cli/main.py`が登録するハンドラ)がセットされると
   `run_api_server`が`ApiServer.stop()`(`ThreadingHTTPServer.shutdown()`→スレッドの
   `join()`→`server_close()`)と`job_repo.close()`を行う

## エラー時の振る舞い

`api/errors.py`に独自の例外を定義する。いずれも`ApiServer`内部でHTTPステータスへ変換され、
`cli/api_server.py`より外側へは伝播しない(リクエストに起因するエラーはHTTPレスポンスとして
完結させる、`webhook/`と同じ方針)。

| 例外 | 送出元 | 変換後のHTTPステータス |
|---|---|---|
| `InvalidTokenError` | `ApiServer._authenticate` | `401` |
| `InvalidRequestError` | `ApiServer`内のリクエスト解析(JSON不正、必須フィールド欠如、`status`/`job_type`/`max_attempts`の不正値) | `400` |
| `job.errors.JobError` | `JobRepository`呼び出し | `500` |

`run_api_server`自体が送出しうる例外(`config.api_token`が空の場合の`ConfigError`、
`SqliteJobRepository`の構築失敗による`JobError`)は`cli/main.py`が`review`/`watch`/`worker`と
同じ変換で終了コード(`EXIT_CONFIG_ERROR`=10、`EXIT_JOB_ERROR`=18)へ変換する
([specs/cli.md](cli.md)参照)。

## テスト方針

実装場所: `tests/gitlab_ai_platform/api/`・`tests/gitlab_ai_platform/cli/test_api_server.py`
(`src/`をミラー、[ADR-0001](../adr/0001-repository-structure.md))。

- `test_server.py`: `ApiServer`を実際に`127.0.0.1`の空きポート(`port=0`)で起動し、
  `http.client.HTTPConnection`で実際にHTTPリクエストを送って検証する(`webhook/test_server.py`
  と同じ方針。実サービスには繋がないが、実DBの`SqliteJobRepository(":memory:")`と組み合わせる)。
  以下を検証する:
  - `POST /jobs`: 正常系(`201`、投入されたJobのJSON表現、`job_repo.get`で実際に取得できる
    こと)、`max_attempts`指定時に反映されること、`job_type`/`payload`欠如・型不正・不正な
    `job_type`値・不正な`max_attempts`が`400`になること
  - `GET /jobs/<id>`: 正常系(`200`、投入済みJobのJSON表現)、存在しない`id`が`404`
  - `GET /jobs?status=<status>`: 指定状態のJobのみ返すこと、`status`省略・不正値が`400`
  - `GET /jobs/dead-letters`: デッドレター化したJobのみ返すこと(空の場合も`200`で`[]`)
  - トークン不一致/未設定は`401`(全エンドポイント共通)
  - 未知のパスは`404`
  - `JobRepository`が`JobError`を送出した場合に`500`になること(手書きフェイクで再現)
  - `start`/`stop`が例外なく呼べること
- `test_api_server.py`(`cli/`): `run_api_server`が`config.api_token`空時に`ConfigError`を
  送出すること、`stop_event`が既にセットされていれば`ApiServer.start`後すぐに戻り`stop`が
  呼ばれること(実サービスに繋がらない範囲での合成ルート検証)を検証する
- `tests/gitlab_ai_platform/cli/test_main.py`: `run_api_server`を`monkeypatch`で差し替え、
  `api`サブコマンドがSIGINT/SIGTERM受信で`stop_event`をセットすること、`ConfigError`が
  `EXIT_CONFIG_ERROR`(10)、`JobError`が`EXIT_JOB_ERROR`(18)に変換されることを検証する
- `tests/gitlab_ai_platform/config/`: `[api]`セクションの読み込み・既定値、`repr(config)`での
  `api_token`のマスクを検証する

## 関連ドキュメント

- [architecture.md](../architecture.md) 「MVP → AI Platformへの成長パス」のJob抽象・状態機械の行
- [ADR-0023: 最小限の HTTP API / サーバ層の設計](../adr/0023-http-api.md)
- [job-model.md](job-model.md) — このAPIが呼び出す`JobRepository`(`enqueue`/`get`/
  `list_by_status`/`list_dead_letters`)の仕様
- [cli.md](cli.md) — `api`サブコマンドのCLI引数・終了コード
- [webhook-receiver.md](webhook-receiver.md) — 同じ`http.server`ベースの実装パターンの前例。
  Webhookサーバーとの共存を見送った判断は[ADR-0023](../adr/0023-http-api.md)参照
- [operations/configuration.md](../operations/configuration.md) — `[api]`セクションと
  `GITLAB_AI_PLATFORM_API_TOKEN`の設定方法
- ソースコード: `src/gitlab_ai_platform/api/`(`server.py` / `errors.py` / `__init__.py`)、
  `src/gitlab_ai_platform/cli/api_server.py`
