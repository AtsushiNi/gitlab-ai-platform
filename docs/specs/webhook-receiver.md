# Webhook Receiver

- 実装場所: `src/gitlab_ai_platform/webhook/`
- 対応Issue: [#96](https://github.com/AtsushiNi/gitlab-ai-platform/issues/96) (M3-6)
- 関連ADR: [ADR-0018](../adr/0018-webhook-receiver.md)
- ステータス: 実装済み

## 責務

GitLabのMerge Request Hook(Webhook)を受信し、MR Poller(`poller/`)と同じ二重起票防止
ロジック(`poller.ticket_if_unprocessed`)を通してレビューを起票する。任意有効化
(`config.webhook_enabled`、既定`false`)のコンポーネントで、`cli/watch.py`の常駐(`watch`)
モードに組み込まれる形で提供する。

## 前提と非対象

- 前提:
  - `cli/watch.py`の`run_watch_loop`から呼ばれること(単発`review`実行では使わない)。
    `config.webhook_enabled=true`の場合のみインスタンス化される
  - GitLab側のWebhook設定で「Merge request events」を有効化し、Secret Tokenに
    `.env`(`GITLAB_AI_PLATFORM_WEBHOOK_SECRET`)と同じ値を設定していること。
    「Push events」は有効化不要([ADR-0018](../adr/0018-webhook-receiver.md)参照)
  - このプロセスが動くホストへGitLabサーバーからHTTP(S)で到達可能であること
    (TLS終端・リバースプロキシの要否は運用側の判断。本コンポーネント自体はHTTPのみを話す)
- 非対象:
  - Push Hook(`object_kind: "push"`)は扱わない。対象MRの特定に追加のGitLab API呼び出しが
    必要になり、Merge Request Hookだけで要件を満たせるため([ADR-0018](../adr/0018-webhook-receiver.md)
    「却下した選択肢」)
  - レビューの実行(Workspace Manager準備→Claude Code Runner起動→Review解析)はしない。
    検出後は`on_detected`コールバック(`cli/watch.py`がPollerと共有する`ReviewWorkerPool`への
    投入ラッパー)に委ねる
  - MR Pollerを置き換えない。`webhook.enabled=false`(既定)であれば、Pollerのみが動く
    従来通りの構成になる
  - HMAC署名によるペイロード検証はしない。GitLab標準のSecret Token方式(ヘッダ比較)のみ
  - GitLab側のWebhook登録・設定変更(GitLab管理画面での操作)は本コンポーネントの範囲外
    (運用手順として`docs/operations/configuration.md`に記載する)

## 公開インターフェース

```python
from collections.abc import Callable

from gitlab_ai_platform.poller import DetectedReview
from gitlab_ai_platform.store.protocol import StateStore


class WebhookServer:
    """GitLab Webhook(Merge Request Hook)を受信し、レビューJobを起票するHTTPサーバー。"""

    def __init__(
        self,
        store: StateStore,
        *,
        review_label: str,
        secret_token: str,
        host: str,
        port: int,
        path: str,
        on_detected: Callable[[DetectedReview], None],
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

```python
from typing import Any

from gitlab_ai_platform.webhook.types import ParsedMergeRequestEvent


def parse_merge_request_event(payload: Any) -> ParsedMergeRequestEvent | None:
    """Merge Request Hookペイロードをパースする。

    `object_kind`が`"merge_request"`以外、または`state`が`"opened"`以外の場合は`None`を返す
    (対象外のイベントとして無視してよいことを表す)。必須フィールドを欠く場合は
    `WebhookPayloadError`を送出する。
    """
```

`cli/watch.py`側の結線(`run_watch_loop`内の非公開ヘルパー`_build_webhook_server`):
`config.webhook_enabled`が真の場合のみ`WebhookServer`を組み立てて`start`/`stop`する。
`on_detected`にはPollerと同じ`pool.submit(functools.partial(review_job, review))`ラッパーを渡す。

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/webhook/types.py`。

| 型 | フィールド | 補足 |
|---|---|---|
| `ParsedMergeRequestEvent` (frozen dataclass) | `project: str`, `mr_iid: int`, `commit_sha: str`, `labels: tuple[str, ...]` | Merge Request Hookペイロードから抽出した、起票判断に必要な最小限の情報。まだState Storeへの起票は行っていない(`ParsedMergeRequestEvent`→`labels`に`review_label`が含まれるかの判定→`poller.ticket_if_unprocessed`という順で処理する) |

`DetectedReview`/`PollError`(`poller.types`)をそのまま再利用し、Webhook独自の起票結果の型は
作らない([ADR-0018](../adr/0018-webhook-receiver.md)「重複させない」)。

### HTTPリクエスト/レスポンス

- リクエスト: `POST <webhook.path>`(既定`/webhook`)。ヘッダ`X-Gitlab-Event`
  (`"Merge Request Hook"`固定)・`X-Gitlab-Token`(Secret Token)、ボディはGitLabの
  Merge Request Hook JSONペイロード
- レスポンス: ボディは`{"status": "..."}`または`{"error": "..."}`形式のJSON(人間の
  デバッグ用途。GitLab側はステータスコードのみを見る)

| ステータスコード | 状況 |
|---|---|
| `200 OK` | Secret Token検証OKだが、対象外イベント(`X-Gitlab-Event`がMerge Request Hook以外)、対象外MR(`レビュー待ち`ラベル無し・`opened`以外の状態)、または既に起票済み(重複) |
| `202 Accepted` | 新規にState Storeへ起票し、`on_detected`コールバックへ引き渡した |
| `400 Bad Request` | リクエストボディがJSONとして不正、またはMerge Request Hookとして必須フィールドを欠く |
| `401 Unauthorized` | `X-Gitlab-Token`ヘッダが設定済みSecret Tokenと不一致(未設定含む) |
| `404 Not Found` | `webhook.path`と異なるパスへのリクエスト |
| `500 Internal Server Error` | State Store操作(`ticket_if_unprocessed`内部の`find`/`create`)が`DuplicateReviewError`以外の`StateStoreError`で失敗した |

`config`(`config/models.py`)には以下のフィールドを追加した(既定値・バリデーションの詳細は
[configuration.md](../operations/configuration.md)参照):

| フィールド | `config.toml`上の位置 | 既定値 |
|---|---|---|
| `webhook_enabled` | `[webhook].enabled` | `false` |
| `webhook_host` | `[webhook].host` | `"0.0.0.0"` |
| `webhook_port` | `[webhook].port` | `8088` |
| `webhook_path` | `[webhook].path` | `"/webhook"` |
| `webhook_secret_token` | `.env`の`GITLAB_AI_PLATFORM_WEBHOOK_SECRET` | なし(`webhook_enabled=true`時は必須) |

## 処理の流れ

1. `cli/watch.py`の`run_watch_loop`が`config.webhook_enabled`を見て、真の場合のみ
   `WebhookServer`を構築し`start()`する(`ThreadingHTTPServer.serve_forever`を背景スレッドで実行)
2. GitLabからのリクエストを受信すると、`WebhookServer._authenticate_and_parse`が
   以下の順で検証する:
   1. `X-Gitlab-Token`を`secrets.compare_digest`で定数時間比較。不一致なら
      `InvalidSecretTokenError`→`401`
   2. `X-Gitlab-Event`が`"Merge Request Hook"`以外なら`None`を返す(エラーにしない)→`200`
   3. リクエストボディを`json.loads`。失敗すれば`WebhookPayloadError`→`400`
   4. `parse_merge_request_event`でペイロードをパース。`object_kind`不一致/`state`が
      `opened`以外/`last_commit`欠如なら`None`を返す→`200`。必須フィールド欠如なら
      `WebhookPayloadError`→`400`
3. パース結果の`labels`に`config.review_label`(`レビュー待ち`)が含まれない場合は無視する→`200`
4. `poller.ticket_if_unprocessed(store, project, mr_iid, commit_sha)`を呼ぶ(MR Pollerと
   完全に同じ実装、`store.find`→`store.create`のダンス)
   - `DetectedReview`が返れば新規起票。`on_detected(review)`を呼んでから`202`
   - `None`が返れば既に起票済み(PollerまたはWebhookの過去のリクエストが先着)→`200`
   - `PollError`が返れば`DuplicateReviewError`以外の`StateStoreError`が発生している→`500`
5. `stop_event`(`run_watch_loop`の`finally`節)がセットされると`WebhookServer.stop()`が
   `ThreadingHTTPServer.shutdown()`→スレッドの`join()`→`server_close()`を行う

## エラー時の振る舞い

`webhook/errors.py`に独自の例外を定義する。いずれも`WebhookServer`内部でHTTPステータスへ
変換され、`cli/watch.py`より外側(`run_watch`/`cli.main`)へは伝播しない
(GitLab側からのリクエストに起因するエラーはHTTPレスポンスとして完結させる方針)。

| 例外 | 送出元 | 変換後のHTTPステータス |
|---|---|---|
| `InvalidSecretTokenError` | `WebhookServer._authenticate_and_parse` | `401` |
| `WebhookPayloadError` | `WebhookServer._authenticate_and_parse`(JSONデコード失敗) / `parse_merge_request_event`(必須フィールド欠如) | `400` |
| `store.errors.DuplicateReviewError` | `poller.ticket_if_unprocessed`内部 | (例外は送出されず`None`として扱われる)`200` |
| `store.errors.StateStoreError`(上記以外) | `poller.ticket_if_unprocessed`内部 | `500` |

`on_detected`コールバック自体(`ReviewWorkerPool.submit`)が送出しうる例外は、このコンポーネントの
責務範囲外(`ReviewWorkerPool`/`run_watch_loop`側の責務、[cli.md](cli.md)参照)。

## テスト方針

実装場所: `tests/gitlab_ai_platform/webhook/`(`src/`をミラー、
[ADR-0001](../adr/0001-repository-structure.md))。

- `test_parser.py`: `parse_merge_request_event`を検証する。`object_kind`が
  `merge_request`以外は`None`、`state`が`opened`以外(`closed`/`merged`/`locked`)は`None`、
  `last_commit`欠如は`None`、ラベル抽出(`title`欠如エントリの無視含む)、必須フィールド
  (`object_attributes`/`project`/`iid`が整数でない等)欠如時に`WebhookPayloadError`を
  送出することを検証する
- `test_server.py`: `WebhookServer`を実際に`127.0.0.1`の空きポート(`port=0`)で起動し、
  `http.client.HTTPConnection`で実際にHTTPリクエストを送って検証する(実GitLabには
  繋がらないが、HTTPサーバー自体は本物を起動する。手書きフェイクの`StateStore`と組み合わせる)。
  以下を検証する:
  - 正常系: Secret Token一致・`Merge Request Hook`・`レビュー待ち`ラベル付き・`opened`状態の
    リクエストが`202`を返し、`on_detected`が1回呼ばれ、State Storeに起票されること
  - Secret Token不一致/未設定は`401`、起票もコールバック呼び出しもされないこと
  - `X-Gitlab-Event`が`Merge Request Hook`以外は`200`で無視されること(コールバック無し)
  - 不正なJSON・必須フィールド欠如ペイロードは`400`
  - `レビュー待ち`ラベルが無い、または`state`が`opened`以外のMRイベントは`200`で無視されること
  - 既に起票済み(State Storeに既存レコードあり、または`create`が`DuplicateReviewError`を
    送出)の場合は`200`(duplicate)で、`on_detected`が呼ばれないこと(重複起票の防止)
  - `DuplicateReviewError`以外の`StateStoreError`は`500`を返すこと
  - `webhook.path`と異なるパスへのリクエストは`404`
  - `start`/`stop`が例外なく呼べること
- `tests/gitlab_ai_platform/poller/test_poller.py`: `ticket_if_unprocessed`を`MrPoller`経由
  だけでなく直接呼び出すテストを追加し、Webhookからも同じ契約で呼ばれることを保証する
  ([poller.md](poller.md)参照)
- `tests/gitlab_ai_platform/cli/test_watch.py`: `run_watch_loop`が`config.webhook_enabled`に
  応じてWebhookサーバーを起動/しないこと、Webhook経由で検出したMRがPollerと同じ
  `execute_review_job`パイプラインで処理されState Storeが`DONE`になることを検証する
  ([cli.md](cli.md)参照)
- `tests/gitlab_ai_platform/config/`: `[webhook]`セクションの読み込み・既定値、
  `webhook.enabled=true`時のSecret Token必須チェック、`repr(config)`でのマスクを検証する

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表・
  「MVP → AI Platformへの成長パス」のMR Poller行
- [ADR-0018: Webhook 受信対応(任意有効化)の設計](../adr/0018-webhook-receiver.md)
- [poller.md](poller.md) — `ticket_if_unprocessed`を共有するMR Pollerの仕様
- [cli.md](cli.md) — `run_watch_loop`がこのコンポーネントを結線する箇所
- [state-store.md](state-store.md) — 二重起票防止の一意制約
- [job-model.md](job-model.md) — 検出後に起票される`review`種別Jobのライフサイクル
- [operations/configuration.md](../operations/configuration.md) — `[webhook]`セクションと
  `GITLAB_AI_PLATFORM_WEBHOOK_SECRET`の設定方法、GitLab側のWebhook登録手順
- ソースコード: `src/gitlab_ai_platform/webhook/`
  (`server.py` / `parser.py` / `types.py` / `errors.py` / `__init__.py`)
