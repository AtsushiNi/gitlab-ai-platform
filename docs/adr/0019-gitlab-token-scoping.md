# ADR-0019: AI用GitLabアカウントとトークンスコープの設計

- Issue: [#98](https://github.com/AtsushiNi/gitlab-ai-platform/issues/98) (M3-8)
- 状態: 決定

## 背景・制約

### これまでの実装状況

- 書き込み操作の許可リストは[ADR-0002](0002-gitlab-adapter-interface.md)以来、GitLab Adapter
  (`gitlab_adapter/`)のコード側(Protocolにメソッドとして存在しない/引数として存在しない)で
  構造的に絞り込んでいる。GitLab PATのスコープ機構(`api`/`read_api`)だけでは
  「MR作成は許可するがmergeは禁止」という操作粒度の制御ができないためであり、この事実は
  ADR-0002以来一貫している。
- 一方、**GitLabアカウント・PATそのものの運用設計**(専用アカウントを作るか、スコープを
  どう最小化するか、複数経路でトークンを分けるか)はD-9([docs/operations/security.md](../operations/security.md))
  で「推奨(未実装・運用ルール)」として書かれたまま、本Issueまで正式決定されていなかった。
- 現状の実装(`config/loader.py`)は、GitLab PATを`GITLAB_AI_PLATFORM_GITLAB_TOKEN`という
  単一の環境変数で受け取り、以下3箇所すべてに同じ値を渡している:
  1. `cli/single_run.py`(`review`単発実行) — `GitLabRestAdapter`を`GitLabReader`型として
     受け取り、`GitLabWriter`のメソッドを一切呼ばない(構造的に読み取り専用、
     [security.md §3.3](../operations/security.md)参照)
  2. `cli/watch.py`(`watch`常駐実行。MR PollerとWebhook受信サーバーの両方がこの経路を共有する。
     [ADR-0018](0018-webhook-receiver.md)) — Pollerの`list_merge_requests`(読み取り)と、
     検出後のレビュー実行(1と同じ`GitLabReader`専用の経路)にのみ使う
  3. `adapter_mcp_server/main.py`(対話型Claude CodeのMCPサーバー、[ADR-0010](0010-gitlab-mcp-tool-bridge.md)) —
     `GitLabAdapter`(読み取り+書き込み14メソッド全体)を公開する

  つまり実態としては「自動実行系(1・2)は構造的に読み取り専用」「対話型(3)のみ書き込みを行う」
  という非対称な権能分布が既にコード上できている。Webhook受信サーバー自体
  (`webhook/server.py`)はGitLab PATを一切使わない。GitLab側の「Secret Token」
  (`X-Gitlab-Token`ヘッダの検証)は別の秘密(`GITLAB_AI_PLATFORM_WEBHOOK_SECRET`)であり、
  GitLab PATとは無関係。

### 決めるべきこと(Issue本文)

1. AI用GitLabアカウントを人間の個人アカウントと分けるべきか
2. PATスコープの最小化設計
3. 用途別トークンを分けるかどうか、分ける場合config側にどう反映するか
4. 監査可能性(棚卸し・ローテーション・失効時の影響範囲)
5. PAT漏洩時の対応手順

## 決定

### 1. AI用GitLabアカウントは人間の個人アカウントと分離し、用途ごとに2つ用意する

人間の個人アカウントを流用しない(これは以前から前提だが、本ADRで明文化する)。理由:

- GitLabの監査ログ(誰が操作したか)がAIによる操作と人間による操作を区別できなくなる
- 人間の異動・退職時にアカウントを無効化すると、AIの実行系も巻き添えで止まる
- 人間のアカウントは通常Maintainer以上のロールを持つことが多く、「ロールをMaintainer未満に
  抑える」という二重防御([security.md §4.1](../operations/security.md)既存記載)が
  そもそも成立しない

さらに、**単一のAI用アカウントではなく、権能の異なる2つのアカウントを用意する**:

| アカウント | 用途 | 想定ロール | 理由 |
|---|---|---|---|
| `ai-review-bot`(例) | 自動実行系(`review`単発実行・`watch`のPoller/Webhook経由レビュー実行) | **Reporter**(書き込み不可) | この経路はコード上`GitLabReader`しか呼ばない([security.md §3.3](../operations/security.md))。仮にPATのスコープ設定を誤って`api`にしてしまっても、アカウントのロール自体がReporterであれば、GitLab側がAPIレベルで書き込みを拒否する。「Adapter層のコード制御」に加えて「GitLabロール」という独立した層で二重に防御できる |
| `ai-interactive-bot`(例) | 対話型GitLab Adapter MCP Server | **Developer** | branch作成・push・MR/Issue作成・コメント投稿([protocol.py](../../src/gitlab_ai_platform/gitlab_adapter/protocol.py)の`GitLabWriter`)を行うため、これらの操作が許可されるロールが必要。Maintainer以上は与えない(merge・protected branch設定変更等の管理操作はAdapter層でも呼び出せないが、ロール側でも念のため許可しない) |

2アカウント運用にする追加コスト(プロジェクトメンバー登録が2倍、棚卸し対象が2倍)は許容できると
判断した。理由は「却下した選択肢」節を参照。

### 2. PATスコープは`read_api`/`api`の二択のまま、アカウント分離とセットで最小化する

[references/spike-S2-gitlab-rest-api.md](../../references/spike-S2-gitlab-rest-api.md)で
確認済みの通り、GitLab PATのスコープには「読み取り専用(`read_api`)」と
「読み書き全体(`api`)」の中間粒度が存在しない。この制約はADR-0002当時から変わっていない。

- `ai-review-bot`のPAT: **`read_api`**(自動実行系は読み取りしか行わないため)
- `ai-interactive-bot`のPAT: **`api`**(書き込みを含むため。読み取りメソッドも同じAdapterで
  呼ぶため`read_api`では不足する)

「1」のアカウントロール分離と組み合わせることで、`ai-review-bot`側は「PATスコープが
`read_api`」「アカウントロールがReporter(そもそも書き込みAPIが使えない)」
「Adapter層のコードに書き込みメソッドを渡さない」という3層の防御になる
(Adapter層の防御は既存、後2つが本ADRで追加される layer)。

### 3. 用途別トークンを分ける。ただし3経路ではなく「自動実行系」「対話型」の2トークンにする

Issue本文は「レビュー用Runner・Webhook受信・MCP対話型の3経路」を例示していたが、
「背景・制約」で述べた通りWebhook受信サーバー自体はGitLab PATを使わず、検出後のレビュー実行は
`watch`のPoller経路と完全に同じコードパス(`ReviewWorkerPool`)を通る。したがって
GitLab PATの観点では実質的に「自動実行系(単発`review`+`watch`のPoller/Webhook)」と
「対話型MCP」の**2経路**であり、トークンもこの2つに分ける。

#### config層への反映

`config/loader.py`に環境変数`GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP`
(`GITLAB_MCP_TOKEN_ENV_KEY`)を追加した。既存の`GITLAB_AI_PLATFORM_GITLAB_TOKEN`
(`GITLAB_TOKEN_ENV_KEY`)は自動実行系専用という位置付けに変え、新しい環境変数を
対話型MCP専用にする。

- `Config`(`config/models.py`)に`gitlab_token_mcp: str`フィールドを追加
- `Config.from_raw`は`gitlab_token_mcp`が空文字列(未設定)の場合、`gitlab_token`と
  同じ値にフォールバックする。**用途別トークンの分離は必須ではなくオプション**にする
  (「却下した選択肢」参照)。運用者が`.env`に`GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP`を
  追加設定した場合にのみ、実際に2つの異なるトークンとして扱われる
- `adapter_mcp_server/main.py`は`GitLabRestAdapter`の構築に`config.gitlab_token`ではなく
  `config.gitlab_token_mcp`を使うよう変更した(`cli/single_run.py`・`cli/watch.py`は
  従来通り`config.gitlab_token`のまま)
- 両トークンとも`Config.__repr__`でマスクする(既存の`gitlab_token`マスクと同様)

`.env.example`・[docs/operations/configuration.md](../operations/configuration.md)・
[docs/operations/security.md](../operations/security.md)を同じPRで更新した。

### 4. 監査可能性

- **アカウント分離自体が監査性を高める**: GitLabの監査ログ(プロジェクトの「Audit Events」)は
  操作を実行したアカウント単位で記録されるため、2アカウントに分けることで
  「どちらの経路(自動実行系/対話型)から行われた操作か」がGitLab側のログだけで判別できる。
  単一アカウントの場合、Adapter層の構造化ログ(`gitlab_adapter/rest.py`の`_record_write`、
  [ADR-0002追記(M1-3)](0002-gitlab-adapter-interface.md#追記m1-3-31)参照)と突き合わせないと
  経路を特定できなかった
- **棚卸し表の運用ルール**([security.md §4.3](../operations/security.md)を拡充):
  発行済みPAT一覧に「アカウント名」「用途(自動実行系/対話型)」「スコープ」「付与ロール」
  「発行日」「最終利用日」を記録する運用を明記した。2トークン運用になったことで
  棚卸し対象が明確に2行に分かれる
- **ローテーション方針**: 2トークンは独立して失効・再発行できる。対話型トークン
  (`api`スコープ、書き込み可能)は自動実行系トークン(`read_api`スコープ)より
  影響範囲が大きいため、優先してローテーション間隔を短くすることを推奨する
  (具体的な間隔は運用開始後の実績を見て[docs/operations/troubleshooting.md](../operations/troubleshooting.md)
  に追記する)

### 5. PAT漏洩時の対応手順

[docs/operations/security.md §4.4](../operations/security.md)に新設した。要点:

1. 漏洩したトークンがどちらのアカウント由来か特定する(`ai-review-bot`/`ai-interactive-bot`の
   どちらか。特定できない場合は両方とも失効させる)
2. 社内GitLabの`User Settings > Access Tokens`で該当PATを即座に**Revoke**する
   (新規発行と再設定は事後でよい。まず失効を優先する)
3. `ai-interactive-bot`(`api`スコープ)が漏洩した場合、書き込み操作(branch作成・push・
   MR/Issue作成・コメント投稿)が第三者に悪用されうる。該当プロジェクトの最近の
   Audit Events・MR/Issue・pushされたcommitを確認し、意図しない変更が無いか確認する
4. `ai-review-bot`(`read_api`スコープ、かつロールがReporter)が漏洩した場合の影響範囲は
   読み取りのみ(プロジェクトの非公開情報の閲覧)に限定される。とはいえ非公開リポジトリの
   コード・Issue内容が漏洩しうるため、同様に失効を優先する
5. 新しいPATを発行し、`.env`(または実環境変数)の該当キー
   (`GITLAB_AI_PLATFORM_GITLAB_TOKEN`または`GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP`)を
   更新する。既存の[docs/operations/troubleshooting.md §2.1](../operations/troubleshooting.md)
   (PAT失効時の`401`/`403`復旧手順)と同じ手順で反映を確認できる
6. 棚卸し表(§4.3)の当該行を更新し、失効日・再発行日を記録する

## 却下した選択肢

- **AI用アカウントを1つにまとめ、トークンだけ2つ発行する**: 「決定」節の通り、
  PATスコープだけでは中間粒度の制御ができないため、`api`スコープのトークンが1つでも
  存在すればそのアカウントは書き込み可能になる。自動実行系用のトークンだけを
  `read_api`にしても、同じアカウントに`api`スコープのトークンが別途発行されていれば
  (対話型用に必要)、アカウント自体のロールはDeveloper以上を要求される。これでは
  「自動実行系はGitLabロールレベルでも書き込み不可」という二重防御(§1の`ai-review-bot`の
  狙い)が成立しない。2アカウントに分けるコストより、この防御層を失うデメリットの方が
  大きいと判断した。
- **Issue本文の例示通り、レビュー用Runner・Webhook受信・MCP対話型の3トークンに分ける**:
  「背景・制約」で述べた通り、Webhook受信サーバー自体はGitLab PATを使わず、
  Webhook経由で検出されたレビューの実行はPoller経由の実行と完全に同一のコードパス
  (`ReviewWorkerPool`)を通る。この2つを別トークンにしても、実際にAPIを呼び出す箇所
  (`cli/watch.py`の`GitLabRestAdapter`構築)は1箇所であり、呼び出しコードを分岐させる
  実益がない。3つに分けると棚卸し対象が増えるだけで、対応するセキュリティ上の利益が
  伴わないため2トークンに留めた。
- **用途別トークンの分離を必須にする(フォールバックを許容しない)**: 個人検証環境や
  小規模な導入では、対話型MCPを使わない(=書き込み操作をそもそも行わない)運用もありうる。
  その場合に2つ目のPAT発行を強制するのは導入コストに見合わない。`GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP`
  未設定時は既存の`GITLAB_AI_PLATFORM_GITLAB_TOKEN`にフォールバックする設計にし、
  分離は運用者が必要に応じて追加導入できるオプションとした(既存ユーザーの`.env`も
  無変更で動作し続ける後方互換性も兼ねる)。
- **GitLab Adapter層にトークンごとの許可メソッド集合を持たせ、`read_api`トークンで
  `GitLabWriter`を呼んだら実行時エラーにする**: [ADR-0002](0002-gitlab-adapter-interface.md)が
  最初から避けてきた「実行時チェックへの依存」に逆戻りする。現状`cli/single_run.py`・
  `cli/watch.py`が型として`GitLabReader`しか受け取らないという静的な制約
  ([security.md §3.3](../operations/security.md))で十分に担保されており、
  Adapter層に新しい実行時ガードを追加する必要はないと判断した。
- **アカウント名・スコープ管理を自動化するツール(棚卸しスクリプト等)を本Issueで実装する**:
  M3-8はIssue本文で「設計中心のタスク」と明記されており、自動化ツールはスコープ外。
  §4の棚卸し運用は当面手動運用とし、必要になった時点で別Issueとして起票する
  (X-2 コスト・使用量の記録と合わせて検討する余地を残す)。

## 影響

- `src/gitlab_ai_platform/config/loader.py`・`config/models.py`:
  `GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP`(`GITLAB_MCP_TOKEN_ENV_KEY`)、
  `Config.gitlab_token_mcp`フィールドを追加
- `src/gitlab_ai_platform/adapter_mcp_server/main.py`: `GitLabRestAdapter`構築に
  `config.gitlab_token_mcp`を使うよう変更
- `.env.example`・[docs/operations/configuration.md](../operations/configuration.md)・
  [docs/operations/security.md](../operations/security.md): 本ADRの決定に合わせて更新
- `docs/operations/setup-windows.md`: 2アカウント運用を前提としたPAT発行手順への言及を
  security.mdへの参照という形で維持(手順の全面書き換えは別途、実際に社内GitLabで
  2アカウントを払い出す運用が始まった段階で行う。本ADR時点では実トークンを発行しない
  設計タスクのため、既存の1アカウント想定の記述と矛盾しない範囲に留めた)
- 既存の`.env`(`GITLAB_AI_PLATFORM_GITLAB_TOKEN`のみ設定)は無変更で動作し続ける
  (フォールバックにより後方互換)
