# ADR-0018: Webhook 受信対応(任意有効化)の設計

- Issue: [#96](https://github.com/AtsushiNi/gitlab-ai-platform/issues/96) (M3-6)
- 状態: 決定

## 背景・制約

- 現状のMR検出は`src/gitlab_ai_platform/poller/`(30〜60秒間隔のポーリング)のみ。
  `docs/architecture.md`の「設計原則」は当初から「Webhookではなくポーリングを選ぶ」
  (社内GitLab側の設定変更を避けたい、MVPとしてのシンプルさを優先)としつつ、
  「将来M3-6でWebhookとの併用へ拡張可能な形にしておく」と明記していた
- 本Issueはその約束を果たす: Webhook受信経路を追加しつつ、**Pollerを置き換えない**。
  Webhookは任意有効化(既定OFF)で、無効なら従来通りPollerのみで動く。有効にした場合も
  Pollerは併走し続け、両方から同じMRが検出されても二重にレビューが起票されないこと
  (重複起票の防止)が要件
- ADR-0016(Job抽象)は既に「M3-6(Webhook対応)はJob起票のインターフェース(`enqueue`)にのみ
  依存するため、トラック2として並行着手できる」としてこのIssueの立ち位置を予約している。
  M3-1が確立した`review`種別Jobの起票経路(`cli/single_run.py`の`execute_review_job`)を
  そのまま再利用する方針は本ADRでも維持する
- ADR-0001(リポジトリ構成と依存方針)により、外部依存は`requests`/`pytest`/`mcp`のみ。
  新規のWebサーバーフレームワーク(Flask等)を追加してよいかがこのIssueの検討事項の1つ

## 決定

### Webhookサーバーは標準ライブラリ`http.server`で実装し、新規依存は追加しない

`http.server.ThreadingHTTPServer` + `BaseHTTPRequestHandler`で十分に実装できる要求である
(受け付けるのは「POSTでJSONを受け取り、ヘッダを検証し、200/202/4xx/5xxを返す」という
最小限のHTTPサーバー)。ルーティング・テンプレート・セッション管理など、Flask/FastAPI等の
フルスタックWebフレームワークが提供する機能は不要なため、ADR-0001の「標準ライブラリで
代替が困難な場合のみ新規依存を許可する」という基準に照らして新規依存を追加しない。

`ThreadingHTTPServer`を選ぶ理由: GitLabからのWebhook配信はリトライ・複数イベント種別の
連続送信がありうるため、1リクエストずつ逐次処理する`HTTPServer`ではブロッキングが
ボトルネックになりうる。`ThreadingHTTPServer`はリクエストごとに新しいスレッドで処理する
標準ライブラリの選択肢であり、追加依存なしにこの要求を満たす。

### 扱うイベントは「Merge Request Hook」(`object_kind: "merge_request"`)のみとし、Push Hookは扱わない

GitLabのPush Hook(`object_kind: "push"`)はブランチ単位のイベントで、そのpushがどのMRに
属するか・対象MRに`レビュー待ち`ラベルが付いているかをペイロード自体からは判断できない
(判断するには追加のGitLab API呼び出し(該当ブランチのMR一覧取得)が必要になり、Poller側の
「ラベル付きMR抽出」ロジックをWebhook側でも作り直すことになる)。

一方、Merge Request Hookのペイロードには判断に必要な情報がそのまま揃っている:

- `object_attributes.iid` — MR IID
- `object_attributes.state` — `"opened"`/`"closed"`/`"merged"`等(Pollerの
  `list_merge_requests(state="opened")`と同じ条件で絞り込める)
- `object_attributes.last_commit.id` — 現在のcommit SHA(新規pushで変わる)
- `labels[].title` — 現在付与されているラベル一覧(`レビュー待ち`ラベルの有無を判定できる)
- `project.path_with_namespace` — `group/project`形式のプロジェクトパス(GitLab Adapter/
  State Store/Job Repositoryが使うキーと同じ形式)

MR Poller側で発生する検出条件(ラベル付与・新規push・再オープン)は、GitLab側でMerge
Request Hookが`action: "update"`(ラベル変更や新規push含む)/`"open"`/`"reopen"`として
発火するため、Push Hookを別途扱わなくてもカバーできる。GitLab側のWebhook設定では
「Merge request events」のみを有効にすればよく、「Push events」は不要
(`docs/operations/configuration.md`にセットアップ手順として明記する)。

### Secret Token検証は`X-Gitlab-Token`ヘッダの定数時間比較で行う

GitLab Webhookの標準機能である「Secret Token」(Webhook設定時に登録する共有シークレット)を
採用する。リクエストの`X-Gitlab-Token`ヘッダと、設定済みの`webhook.secret_token`を
`secrets.compare_digest`で比較し、不一致・未設定なら`401 Unauthorized`で拒否する。
署名(HMAC)方式ではなくGitLabの一般的な「Secret Token」方式を選んだ理由は、GitLab側の
Webhook設定UIがデフォルトで提供する検証方式そのものであり、GitLab Adapterの認証
(PAT)と同様に「シークレット値をヘッダ経由で受け渡す」という既存の設計方針
(`cli/single_run.py`のcredential helper等)と一貫するため。

Secret Tokenの値自体はGitLab PAT(`GITLAB_AI_PLATFORM_GITLAB_TOKEN`)と同じ理由で
`config.toml`(リポジトリにコミットされうる)には置かず、`.env`または環境変数
(`GITLAB_AI_PLATFORM_WEBHOOK_SECRET`)経由で渡す(`config/loader.py`の既存パターンを踏襲)。

### 重複起票の防止は、PollerとWebhookが同じState Store一意制約ダンス(`find`→`create`)を共有することで実現する

MR Pollerの`MrPoller._ticket_if_unprocessed`が持っていた「`store.find`で未処理か確認し、
`store.create`で起票する。競合による`DuplicateReviewError`は無視する」というダンスを、
`poller/poller.py`のモジュール関数`ticket_if_unprocessed(store, project, mr_iid,
commit_sha)`として切り出し、`MrPoller`とWebhookサーバーの両方から呼ぶ**1つの実装**にする
(ロジックの複製をしない、Issue #96の要件「Pollerとの重複起票防止の具体的な仕組み」への回答)。

これにより:

- 二重起票の最終防止線は引き続きState Storeの`(project, mr_iid, commit_sha)`一意制約
  (ADR-0003)そのものであり、Webhook側が独自の排他制御を持つ必要がない
- Poller・Webhookのどちらが先に同じ`(project, mr_iid, commit_sha)`を検出しても、後から
  検出した側は`store.find`で既存レコードを見つけて何もしない(Webhookが先勝ちしても
  Pollerの次回走査が同じレコードを見つけてスキップする、その逆も同様)
- 将来Webhook側の判断ロジックに変更が入っても(例: 対象イベント種別の追加)、
  State Storeとのやり取り部分は変更不要

「ラベル付きMR抽出」の判断自体は検出経路ごとに異なる(Pollerは`list_merge_requests`の
`labels`パラメータでGitLab APIに絞り込ませる、Webhookはペイロードの`labels[]`を直接見る)ため、
ここは共通化しない(共通化すると、Webhookのためだけに「ペイロードから絞り込んだ結果を
Pollerの絞り込みインターフェースに合わせて変換する」不要な抽象化が必要になる)。

### Webhookサーバーは新しいCLIサブコマンドを作らず、既存の`watch`サブコマンドに任意有効化の追加コンポーネントとして統合する

`config.toml`に`[webhook]`セクション(`enabled`、既定`false`)を追加し、`cli/watch.py`の
`run_watch_loop`が`config.webhook_enabled`が真の場合のみWebhookサーバーを起動する。

理由:

- Webhookは「MR検出のもう1つの経路」であり、Poller同様に検出後の実行(Workspace Manager
  準備→Claude Code Runner起動→Review解析、`execute_review_job`)は完全に共有すべきもの。
  新しいサブコマンド(例: `gitlab-ai-platform webhook`)にすると、Poller用プロセスと
  Webhook用プロセスを別々に起動・監視する運用が必要になり、「片方だけでも動く」という
  要件は満たせても「両方を同時に動かす」典型的な運用(Webhookで即時検出しつつ、
  Webhook配信漏れの保険としてPollerも継続する)が2プロセス構成前提になってしまう
- `run_watch_loop`は既に`MrPoller`が検出した`DetectedReview`を`ReviewWorkerPool`(M2-1、
  `cli/worker_pool.py`)へ投入する`on_detected`コールバックを持っている
  (`build_on_detected`が組み立てる)。Webhookが検出した`DetectedReview`も同じ
  コールバック・同じワーカープールへ投入することで、実行経路(並列数制御・エラー処理・
  ログ)を完全に共有できる
- `webhook.enabled=false`(既定値)であればWebhookサーバーの起動処理自体がスキップされ、
  従来の`watch`(Pollerのみ)と完全に同じ挙動になる(「片方だけでも動く」を満たす)

`watch`プロセス内でWebhookサーバーを背景スレッド(`ThreadingHTTPServer.serve_forever`を
別スレッドで実行)として起動し、`run_watch_loop`が`finally`節で`shutdown()`する
(`MrPoller.run`/`ReviewWorkerPool`のgraceful shutdownと同じ`stop_event`/`finally`パターン)。

## 却下した選択肢

- **Flask等のWebフレームワークを導入する**: ルーティング・ミドルウェア等の機能は
  「1エンドポイントでJSONを受け取るだけ」の要求に対して過剰であり、ADR-0001の依存最小化
  方針に反する。将来M3-7(HTTP API層)でエンドポイントが増えた際に改めて要否を検討する
- **Push Hookも扱う**: 対象MRの特定にGitLab APIへの追加問い合わせが必要になり、
  Merge Request Hookだけで要件を満たせるため見送った。将来「pushからMRへの逆引き」が
  別の目的(例: MR未作成のpushの検知)で必要になれば、別Issueとして再検討する
- **署名(HMAC)ベースの検証**: GitLabのWebhook機能はSecret Token方式が標準であり、
  HMAC署名はGitLab側の設定UIでは提供されない機能のため見送った
- **Webhookサーバーを独立した新規CLIサブコマンド(別プロセス)として提供する**: 検出後の
  実行経路(ワーカープール・Job起票)を`watch`と共有できなくなり、Poller/Webhookを同時運用
  する際に2プロセス分のState Store/Job Repository接続・ロック管理が必要になる。`watch`
  プロセス内への統合の方が「片方だけでも動く、両方も動く」という要件をシンプルに満たせる
- **State Store以外の重複起票防止機構(分散ロック等)を新設する**: ADR-0003が確立した
  `(project, mr_iid, commit_sha)`一意制約は単一プロセス内の複数検出経路(Poller/Webhook)を
  区別する必要がなく、既存の機構をそのまま使い回せる。新しい機構を追加する理由がない

## 影響

- `src/gitlab_ai_platform/webhook/`を新設する(`server.py`/`parser.py`/`types.py`/
  `errors.py`)。詳細仕様は`docs/specs/webhook-receiver.md`
- `poller/poller.py`に`ticket_if_unprocessed`をモジュール関数として追加し、
  `MrPoller._ticket_if_unprocessed`はこれを呼ぶだけになる(`poller/__init__.py`で公開)
- `config/models.py`の`Config`に`webhook_enabled`/`webhook_host`/`webhook_port`/
  `webhook_path`/`webhook_secret_token`を追加する。`config/loader.py`に`[webhook]`
  セクションの読み込みと`GITLAB_AI_PLATFORM_WEBHOOK_SECRET`環境変数キーを追加する
- `cli/watch.py`の`run_watch_loop`がWebhookサーバーの起動/停止を担う。`cli/single_run.py`
  (単発`review`実行)は変更しない(Webhookは常駐モード専用の機能のため)
- 将来M3-7(最小限のHTTP API/サーバ層)で別エンドポイントが増える場合、Webhook
  サーバーと同じプロセス内で共存させるか判断が必要になる(本ADRのスコープ外)
