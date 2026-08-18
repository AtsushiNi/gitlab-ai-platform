# ADR-0019: AI用GitLabアカウントとトークンスコープの設計

- Issue: [#98](https://github.com/AtsushiNi/gitlab-ai-platform/issues/98) (M3-8)、追記: [#127](https://github.com/AtsushiNi/gitlab-ai-platform/issues/127)
- 状態: 決定

## 背景・制約

- 書き込み操作の許可リストは[ADR-0002](0002-gitlab-adapter-interface.md)以来、GitLab Adapterのコード側で構造的に絞り込んでいる。GitLab PATのスコープ機構(`api`/`read_api`)だけでは「MR作成は許可するがmergeは禁止」という操作粒度の制御ができないため
- **GitLabアカウント・PATそのものの運用設計**(専用アカウントの要否、スコープ最小化、トークン分離)は未決定だった
- 実装は自動実行系(単発`review`実行・`watch`常駐)と対話型(GitLab Adapter MCP Server)の2経路があり、自動実行系は構造的に読み取り専用(`GitLabReader`型でしか受け取らない)、対話型のみ書き込みを行う非対称な権能分布が既にコード上できている

## 決定

### 1. AI用GitLabアカウントは人間の個人アカウントと分離し、用途ごとに2つ用意する

人間のアカウント流用は監査ログの区別がつかない・異動時の巻き添え停止等の問題があるため避ける。さらに権能の異なる2アカウントを用意する:

| アカウント | 用途 | ロール | 理由 |
|---|---|---|---|
| `ai-review-bot` | 自動実行系(単発`review`・`watch`) | Reporter(書き込み不可) | コード上`GitLabReader`しか呼ばない経路。GitLabロールでも二重に防御できる |
| `ai-interactive-bot` | 対話型GitLab Adapter MCP Server | Developer | branch作成・push・MR/Issue作成・コメント投稿が必要。Maintainer以上は与えない |

### 2. PATスコープは`read_api`/`api`の二択のまま、アカウント分離とセットで最小化する

中間粒度のスコープは存在しないため、`ai-review-bot`は`read_api`、`ai-interactive-bot`は`api`とする。「PATスコープ」「アカウントロール」「Adapter層のコード制御」の3層防御になる。

### 3. 用途別トークンを分ける(自動実行系/対話型の2トークン)

`config/loader.py`に`GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP`(対話型MCP専用)を追加。既存の`GITLAB_AI_PLATFORM_GITLAB_TOKEN`は自動実行系専用とする。`gitlab_token_mcp`が未設定の場合は`gitlab_token`にフォールバックし、**分離は必須ではなくオプション**にする(運用者が任意で追加設定)。

### 4. 監査可能性

アカウント分離自体がGitLab監査ログ(Audit Events)での経路判別を可能にする。棚卸し表にアカウント名・用途・スコープ・付与ロール・発行日・最終利用日を記録する運用とする。

### 5. PAT漏洩時の対応手順

漏洩元アカウントを特定し即座にRevoke、影響範囲(書き込み可否)を確認し新PATを発行する手順を[docs/operations/security.md](../operations/security.md)に定めた。

## 却下した選択肢

- **AI用アカウントを1つにまとめ、トークンだけ2つ発行する**: 同一アカウントに`api`スコープのトークンが1つでも存在すればアカウント自体がDeveloper以上のロールを要求され、GitLabロールレベルの二重防御が成立しない
- **レビュー用Runner・Webhook受信・MCP対話型の3トークンに分ける**: Webhook受信サーバー自体はGitLab PATを使わず、検出後のレビュー実行はPoller経由と同一コードパスのため、3分割の実益がない
- **用途別トークンの分離を必須にする**: 小規模導入では2つ目のPAT発行を強制するのは導入コストに見合わないため、フォールバックを許容するオプション設計にした
- **GitLab Adapter層にトークンごとの許可メソッド集合を持たせ実行時エラーにする**: ADR-0002が避けてきた実行時チェック依存に逆戻りする

## 影響

- `config/loader.py`・`config/models.py`に`gitlab_token_mcp`フィールドを追加
- `adapter_mcp_server/main.py`は`config.gitlab_token_mcp`を使うよう変更
- 既存の`.env`(トークン1つのみ設定)は無変更で動作し続ける(フォールバックにより後方互換)

## 追記(M4フォローアップ、#127): `ai-review-bot`のロール・スコープ引き上げ

M4-8(実装フェーズ、`create_branch`)・M4-9(pushフェーズ、`push_file_changes`/`create_merge_request`)により、自動実行系トークンが書き込みAPIを呼ぶようになり、「決定1」の前提(自動実行系は読み取りのみ)が崩れた。新しいアカウントを追加せず、`ai-review-bot`のロール・スコープを引き上げる:

| 項目 | 変更前 | 変更後 |
|---|---|---|
| ロール | Reporter | **Developer** |
| PATスコープ | `read_api` | **`api`** |

`ai-interactive-bot`は変更しない。安全性はGitLabロールではなく、既存の**Adapter層の許可リスト**(merge等はそもそもメソッドとして存在しない)と**ハンドラごとの静的型**(`build_review_handler`等は`adapter: GitLabReader`と宣言されmypy上書き込みメソッドを呼べない)で担保する方針に切り替えた。

却下: 「無人実行トラックを読み取り専用/書き込み可能の2アカウントに分割する」案は、アカウント・トークンを増やす運用コストに見合うリスク低減ではないと判断した。

残存リスク: 実装フェーズでClaude CodeがBash経由で素の`git push`を試みても、以前はGitLab側のロール・スコープが拒否する3層目の防御があったが、本改定以降は成立しない。`disallowed_tools=("Bash(git push:*)",)`と、worktreeの`.git/config`に認証情報を永続化しない設計の2つに実効的な防御が縮退する。
