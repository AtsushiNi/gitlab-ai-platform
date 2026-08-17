# ADR-0023: 最小限の HTTP API / サーバ層の設計

- Issue: [#97](https://github.com/AtsushiNi/gitlab-ai-platform/issues/97) (M3-7)
- 状態: 決定

## 背景・制約

- `references/タスク整理.md`のM3-7は「Job投入、状態参照、結果取得。将来のUIや他ツール連携の口」を
  求めている。[ADR-0017](0017-job-queue.md)は影響節で「M3-7(HTTP API層)は`list_dead_letters`を
  使ってデッドレターJobの一覧・再投入APIを提供できる見込み」と、本Issueの立ち位置を予約している
- [ADR-0016](0016-job-abstraction.md)/[ADR-0017](0017-job-queue.md)が確定させた
  `JobRepository`(`src/gitlab_ai_platform/job/protocol.py`)には、本Issueが必要とする操作
  (`enqueue`/`get`/`list_by_status`/`list_dead_letters`)が既に揃っている。本ADRは
  `JobRepository`のメソッドを変更せず、その薄いHTTP層を追加する設計判断のみを扱う
- [ADR-0018](0018-webhook-receiver.md)(Webhook受信対応、M3-6)は「影響」節で「M3-7で別
  エンドポイントが増える場合、Webhookサーバーと同じプロセス内で共存させるか判断が必要になる」と
  本Issueへ検討事項を残していた。本ADRはこれに回答する
- ADR-0001の依存最小化方針により外部依存は最小限(`requests`/`pytest`/`mcp`のみ)。新規のWeb
  フレームワーク(Flask/FastAPI等)を追加してよいかは[ADR-0018](0018-webhook-receiver.md)と
  同様にこのIssueでも検討事項の1つ
- 本タスクは過去2回、着手直後にユーザーの都合で中断されている(実装内容に問題があったわけでは
  なく、並行実行数の調整のため)。今回は他のM3タスク(M3-1〜M3-6、M3-8)がすべて完了・`main`へ
  マージ済みの状態からの着手であり、`JobRepository`・`webhook/`・`cli/dispatcher.py`(`worker`
  サブコマンド)は本ADRの前提としてそのまま参照できる

## 決定

### 実装方式は`webhook/server.py`と同じく標準ライブラリ`http.server`を踏襲し、新規依存は追加しない

本Issueが要求する操作(Job投入・状態/結果参照・一覧取得の4種、後述)は「JSONを受け取って
JSONを返すエンドポイントが数本」という規模であり、ルーティング・ミドルウェア・テンプレート等の
フルスタックWebフレームワークが提供する機能は不要という判断は[ADR-0018](0018-webhook-receiver.md)
時点から変わっていない。`http.server.ThreadingHTTPServer` + `BaseHTTPRequestHandler`で
十分に実装できるため、ADR-0001の「標準ライブラリで代替が困難な場合のみ新規依存を許可する」
基準に照らして新規依存を追加しない。`ThreadingHTTPServer`を選ぶ理由も同じ(呼び出し元が
複数(将来のUI・他ツール・複数の運用スクリプト)になりうるため、リクエストごとに新しいスレッドで
処理する)。

### エンドポイントは`POST /jobs`(投入)・`GET /jobs/<id>`(状態・結果参照)・`GET /jobs?status=...`(一覧)・`GET /jobs/dead-letters`(デッドレター一覧)の4本とする

| メソッド・パス | 対応する`JobRepository`メソッド | 成功時ステータス |
|---|---|---|
| `POST /jobs` | `enqueue(job_type, payload, max_attempts=...)` | `201 Created` |
| `GET /jobs/<id>` | `get(job_id)` | `200 OK`(無ければ`404`) |
| `GET /jobs?status=<status>` | `list_by_status(status)` | `200 OK` |
| `GET /jobs/dead-letters` | `list_dead_letters()` | `200 OK` |

Issue本文が列挙する「Job投入、状態参照、結果取得、一覧取得」のうち、「状態参照」と
「結果取得」は別々のエンドポイントに分けない。`Job`(`job/protocol.py`)は`status`/`result`/
`error`を同じレコードのフィールドとして持ち、`JobRepository`にも状態と結果を別々に取得する
メソッドは存在しない(`get`のみ)。呼び出し側が2回リクエストする(状態確認→結果取得)よりも、
1回の`GET /jobs/<id>`で両方を返す方がJob抽象の設計(ADR-0016)に忠実であり、レスポンスの
一貫性(状態と結果の間にレースコンディションが生じない)も保てる。

`claim`/`heartbeat`/`complete`/`fail`(Runner Dispatcher専用、[ADR-0017](0017-job-queue.md)/
[ADR-0022](0022-runner-process-separation.md))はこのAPIからは公開しない。これらは特定の
`worker_id`(リース所有者)が握るべき操作であり、「将来のUIや他ツール連携の口」という本Issueの
目的(Job投入・監視)とは異なる関心事のため(却下した選択肢を参照)。

`GET /jobs`(全件一覧、`status`省略)は提供しない。`JobRepository.list_by_status`が`status`を
必須パラメータとする設計([ADR-0016](0016-job-abstraction.md))をAPI層でも維持し、`status`
クエリパラメータを必須とする(省略時は`400 Bad Request`)。

### 認証は`X-Api-Token`ヘッダの定数時間比較とし、Webhookとは別のトークンにする

[ADR-0018](0018-webhook-receiver.md)のSecret Token方式(ヘッダ比較、`secrets.compare_digest`)を
踏襲する。GitLab標準機能に合わせた`X-Gitlab-Token`とは異なり、本APIはGitLab側の機能に依存
しないため、汎用的なヘッダ名`X-Api-Token`を使う。

Webhookの`webhook_secret_token`とは**別のトークン**(`api_token`)にする。理由:

- Webhookは「GitLabだけが送信元」という単一の信頼関係だが、本APIは「将来のUI・他ツール・
  複数の運用スクリプト」という複数の呼び出し元を想定しており、書き込み系操作(`POST /jobs`、
  任意の`job_type`でJobを投入できる)を含む。異なる信頼境界には異なるシークレットを使うのが
  安全側の設計であり、片方が漏洩してももう片方には影響しない
- `webhook_secret_token`はGitLab Webhookの設定UIに直接貼り付ける値であり、本APIのトークンと
  兼用すると、GitLab側の設定変更(Webhook URLの再設定等)のたびに本APIのクライアント側にも
  影響が及ぶ結合が生まれる

`api_token`は`GITLAB_AI_PLATFORM_API_TOKEN`(`.env`/環境変数)経由で渡し、GitLab PAT・Webhook
Secret Token・PostgreSQLパスワードと同じ理由で`config.toml`には置かない。

### `Config`に`api_enabled`は追加しない。`api`サブコマンドの実行自体が有効化を意味する

`webhook_enabled`(既定`false`、明示的に有効化する設計)とは異なり、本APIは新しいCLI
サブコマンド`api`として提供する(次項)。サブコマンドを実行しない限りAPIサーバーは起動しない
ため、`Config`に追加の有効/無効フラグを持たせる意味がない。`api_token`の必須チェック
(空なら起動を拒否する)は`Config.from_raw`ではなく、`api`サブコマンドの合成ルート
(`cli/api_server.py`の`run_api_server`)側で行う。`Config.from_raw`に持たせると、
`api`サブコマンドを使わない運用者(`review`/`watch`/`worker`/`decompose`のみ使う大多数)にも
無関係な必須項目が増えてしまうため(`webhook_secret_token`は`webhook_enabled`という
Config内のフラグで条件分岐できるが、本APIには対応するフラグが無いためConfig内では
条件分岐できない)。

### Webhookサーバーとは同じプロセス・ポートで共存させず、独立した新しいCLIサブコマンド`api`として提供する

[ADR-0018](0018-webhook-receiver.md)が残した検討事項への回答。`worker`サブコマンド
([ADR-0022](0022-runner-process-separation.md))と同じ「パイプライン本体/合成ルートを
`cli/<name>.py`に同居させる」パターンで`cli/api_server.py`を追加する。

理由:

- [ADR-0018](0018-webhook-receiver.md)が`watch`にWebhookサーバーを統合した理由は
  「検出後の実行経路(`ReviewWorkerPool`)をPollerと完全に共有する必要があるため」だった。
  本APIにはこの理由が当てはまらない。`ApiServer`が必要とする依存は`JobRepository`のみで、
  GitLab Adapter・Workspace Manager・Claude Code Runner・`ReviewWorkerPool`のいずれにも
  依存しない(Jobの実行そのものはこの層の責務ではなく、`worker`サブコマンドが担う)
- Webhookサーバーの寿命は「Poller・MR検出」という`watch`固有のユースケースに紐づくが、
  本APIの寿命は「Job Repositoryの外部公開」という独立した関心事であり、`watch`
  (Poller/Webhookが有効かどうか)や`worker`(Runner Dispatcherが動いているかどうか)の
  稼働状況と無関係に起動・停止・スケールできるべきである(例: `worker`を複数ホストに
  分散させても、Job投入用のAPIは1箇所〔または複数、後述〕で十分)
- `worker`([ADR-0022](0022-runner-process-separation.md))は既に「同一`job_db_path`に対する
  複数プロセス/複数ホストの同時稼働を前提とし、`ProcessLock`のような多重起動防止を行わない」
  という前例を確立している。本APIも同じ理由(`JobRepository`(SQLite実装)がプロセス間の
  排他を`claim`のアトミックなUPDATE文と`busy_timeout`で担保する、[ADR-0017](0017-job-queue.md))
  により、複数の`api`プロセスの同時稼働(将来のロードバランサ配下での水平スケール)を妨げない
  設計にできる。Webhookサーバーと同居させると、この「複数プロセス前提」の設計を`watch`
  (`ProcessLock`で多重起動を防ぐ)に持ち込むことになり矛盾する

`api`サブコマンドは`review`/`watch`/`worker`/`decompose`と同じ`cli/main.py`のサブパーサーに
追加する。CLIオプションは持たせず(`--host`/`--port`/`--token`のような上書きオプションは
用意しない)、`config.toml`の`[api]`セクションと`.env`の`GITLAB_AI_PLATFORM_API_TOKEN`のみで
設定する(Webhookと同じ、[ADR-0018](0018-webhook-receiver.md)も`webhook`サブコマンドの
CLIオプションを持たない)。

### 待受アドレスの既定値はWebhookと異なり`127.0.0.1`(ループバックのみ)にする

Webhookの既定`0.0.0.0`は「社内GitLabサーバーからの到達性を最優先する」ためだったが
([ADR-0018](0018-webhook-receiver.md))、本APIの典型的な呼び出し元(将来のUI・運用
スクリプト)は同一ホストまたはリバースプロキシ経由が主要ユースケースとして想定され、
GitLabサーバーのような特定の外部到達性要件を持たない。書き込み系操作(任意のJobType・
payloadで`POST /jobs`できる)を含むAPIであることも踏まえ、既定は安全側(ループバックのみ)に
倒し、外部公開が必要な運用者が明示的に`config.toml`の`[api].host`を変更する設計にする
(既定ポートは`8090`、Webhookの既定`8088`と衝突しないよう別番号を割り当てる)。

## 却下した選択肢

- **Flask等のWebフレームワークを導入する**: [ADR-0018](0018-webhook-receiver.md)と同じ理由
  (ADR-0001の依存最小化方針、要求規模に対して過剰)で見送った
- **Webhookサーバーと同じプロセス・ポートで共存させる(`watch`にAPIサーバーも統合する)**:
  「決定」節の通り、本APIはPoller/Webhookの実行経路(`ReviewWorkerPool`)に依存せず、`watch`の
  稼働状況とも独立してスケールできるべきという設計判断のため見送った。同じポートで
  パスによって振り分ける案も検討したが、認証トークンを分離する設計(前述)と相性が悪く
  (1つの`ThreadingHTTPServer`インスタンスに2種類の認証ロジックが混在する)、却下した
- **`claim`/`heartbeat`/`complete`/`fail`もAPIとして公開し、Runner自体をリモートワーカーとして
  HTTP経由で動かせるようにする**: 本Issueの要件(「Job投入、状態参照、結果取得」)を超える
  スコープであり、[ADR-0022](0022-runner-process-separation.md)が確立した`worker`サブコマンド
  (DBベースの`claim`)による別プロセス/別ホスト実行の設計と役割が重複・競合する。将来
  「Runnerをコンテナ以外の環境(HTTP経由のみ到達可能な環境)で動かす」要件が具体化した時点で
  別Issueとして再検討する
- **`GET /jobs`(全件一覧、statusを省略可にする)を追加する**: `JobRepository.list_by_status`
  自体が`status`必須の設計([ADR-0016](0016-job-abstraction.md))であり、全件一覧はJob数が
  増えた際にAPIレスポンスが際限なく膨らむリスクがある。ページネーション等を今から設計するには
  時期尚早と判断し、`status`必須のまま最小限にとどめた。将来必要になった時点で別途検討する
- **Webhookと同じ`webhook_secret_token`を本APIの認証にも流用する**: 「決定」節の通り、
  信頼境界(送信元・操作の重大さ)が異なるため、シークレットを分離する方が安全側の設計になる
- **`Config`に`api_enabled`(既定`false`)を追加し、`watch`のように任意有効化する**: 本APIは
  新しいCLIサブコマンドとして提供するため、サブコマンドを実行しない限り起動しない。
  `webhook_enabled`と同じ「常駐プロセスの中で任意コンポーネントを有効化する」パターンは
  本Issueの構成(独立したサブコマンド)には不要
- **JWT等のトークン検証機構を導入する**: 有効期限・失効・スコープ等の管理が必要になり、
  「社内ツールの最小限のAPI」という要求に対して過剰。Webhookと同じ静的トークン比較で
  現時点は十分と判断した。将来、呼び出し元ごとに権限を分けたい要求が具体化すれば
  再検討する

## 影響

- `src/gitlab_ai_platform/api/`を新設する(`server.py`/`errors.py`/`__init__.py`)。詳細仕様は
  `docs/specs/http-api.md`
- `src/gitlab_ai_platform/cli/api_server.py`(新規、`run_api_server`)、
  `src/gitlab_ai_platform/cli/main.py`(`api`サブコマンド追加)を変更した。既存の`review`/
  `watch`/`worker`/`decompose`サブコマンド・`webhook/`パッケージ・`job/`パッケージは無改修
- `config/models.py`の`Config`に`api_host`/`api_port`/`api_token`を追加する。
  `config/loader.py`に`[api]`セクションの読み込みと`GITLAB_AI_PLATFORM_API_TOKEN`環境変数
  キーを追加する
- `docs/operations/configuration.md`に`[api]`セクション・`.env`の
  `GITLAB_AI_PLATFORM_API_TOKEN`を追記した
- 将来のUI(M4以降で具体化する可能性)や外部ツール連携は、本APIを経由してJob投入・監視
  ができるようになる。M4の`issue-analysis`/`design`/`implement`種別のJobも、
  `JobType`の値が増えるだけで本APIのコード変更なしに投入できる(`job_type`は文字列として
  そのまま`JobRepository.enqueue`へ渡すため)
