# ADR-0037: 自動実行系GitLabトークンのスコープ引き上げ

- Issue: [#127](https://github.com/AtsushiNi/gitlab-ai-platform/issues/127)
- 状態: 決定

## 背景・制約

- [ADR-0019](0019-gitlab-token-scoping.md)(M3-8)は、自動実行系アカウント(`ai-review-bot`、
  `GITLAB_AI_PLATFORM_GITLAB_TOKEN`)を「この経路はコード上`GitLabReader`しか呼ばない」という
  理由で`read_api`スコープ・**Reporterロール**に絞る設計にした。
- しかしM4-8(#114、実装フェーズ、`create_branch`)・M4-9(#115、pushフェーズ、
  `push_file_changes`/`create_merge_request`)により、`run_dispatcher`(`worker`)経由の
  無人実行トラックが同じトークンで**書き込みAPI**を呼ぶようになった。ADR-0019の前提
  (「自動実行系は読み取りしかしない」)はここで崩れている
  ([ADR-0034](0034-push-and-mr-phase.md)・[security.md §3.5](../operations/security.md)に
  既知課題として記録済み)。
- `run_dispatcher`は`review`/`issue-analysis`/`design`/`plan`/`implement`/`push`の全
  `JobHandler`へ同一の`GitLabRestAdapter(config.gitlab_url, config.gitlab_token)`を渡す
  ([`cli/dispatcher.py`](../../src/gitlab_ai_platform/cli/dispatcher.py)の`run_dispatcher`)。
  `read_api`スコープ・Reporterロールのままでは、`implement`/`push`の書き込み呼び出しが
  実運用のGitLabでAPIレベルで拒否される。

## 決定

### 自動実行系アカウント(`ai-review-bot`)を1つのまま、ロール・スコープを引き上げる

新しいアカウントを追加せず、既存の`ai-review-bot`(`GITLAB_AI_PLATFORM_GITLAB_TOKEN`)のロール・
PATスコープを次のように変更する:

| 項目 | 変更前(ADR-0019) | 変更後(本ADR) |
|---|---|---|
| ロール | Reporter | **Developer** |
| PATスコープ | `read_api` | **`api`** |

`ai-interactive-bot`(対話型GitLab Adapter MCP Server用、`GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP`)は
変更しない。引き続きDeveloperロール・`api`スコープのまま、`ai-review-bot`とは別アカウント・
別トークンを維持する(用途別トークンの仕組み自体はADR-0019のまま存置)。

これにより`ai-review-bot`は`ai-interactive-bot`と**同じロール・スコープ**になるが、
「自動実行系(無人)」「対話型(人間がVS Code経由で起動)」という監査ログ上のアカウント分離
(ADR-0019「決定1」の理由: 誰の操作かGitLab監査ログで判別できる、退職時のアカウント無効化が
互いに影響しない、等)は維持する。

### 安全性はGitLabロールではなく、既存のAdapter層の許可リスト+ハンドラごとの静的型で担保する

`ai-interactive-bot`が最初からそうであったのと同じ考え方に揃える([security.md §2.3](../operations/security.md)):

- **Adapter層の許可リスト**([`gitlab_adapter/protocol.py`](../../src/gitlab_ai_platform/gitlab_adapter/protocol.py)):
  merge・protected branchへの直push・branch削除・プロジェクト管理操作・Issue/MRの状態遷移は
  そもそもメソッドとして存在しない。トークンのロール・スコープに関わらず、このAdapterを
  経由する限り呼び出す手段がない
- **ハンドラごとの静的型**([`cli/dispatcher.py`](../../src/gitlab_ai_platform/cli/dispatcher.py)):
  `build_review_handler`/`build_issue_analysis_handler`/`build_design_handler`/
  `build_plan_handler`は引数を`adapter: GitLabReader`と宣言しており、同じ`GitLabRestAdapter`
  インスタンスを渡してもmypy上その関数内では書き込みメソッドを呼べない。この防御は
  トークンの実際の権限とは独立しており、本ADRによる変更の影響を受けない

## 却下した選択肢

- **無人実行トラックを「読み取り専用」「無人実装・push可能」の2アカウントに分割する**
  (Issue #127本文が例示した方向性のひとつ。ADR-0019の「アカウント分離」パターンを
  そのまま踏襲する案): `review`/`issue-analysis`/`design`/`plan`用と`implement`/`push`用で
  GitLabロールレベルの二重防御を維持できる利点はあるが、アカウント・トークンをもう1つ
  増やす運用コスト(棚卸し対象の増加、`run_dispatcher`が2つの`GitLabRestAdapter`を
  組み立てて`JobType`ごとに配線する実装、`config`層への新しい環境変数の追加)に見合う
  ほどのリスク低減ではないと判断した。前述の通りAdapter層の許可リスト+ハンドラごとの
  静的型という、トークン権限に依存しない防御が既に存在するため
- **禁止操作(merge等)をGitLab側のカスタムロール機能で細かく制限する**: 検証時点
  ([references/spike-S2-gitlab-rest-api.md](../../references/spike-S2-gitlab-rest-api.md))で
  GitLab PATのスコープには`api`/`read_api`の中間粒度が存在しないことを確認済み
  (ADR-0002・ADR-0019から変わらない制約)。カスタムロール機能の導入・運用コストは
  本Issueのスコープを超えるため見送った

## 影響

- **GitLab側の運用変更**(本リポジトリのコード変更を伴わない): `ai-review-bot`アカウントの
  ロールをReporter→Developerへ、発行済みPATのスコープを`read_api`→`api`へ変更する
  (発行済みPATのスコープ自体は事後変更できないため、実質的には新PATの再発行+旧PATのRevoke)
- [`docs/adr/0019-gitlab-token-scoping.md`](0019-gitlab-token-scoping.md): 「追記」節を追加し、
  本ADRへのポインタを記録(決定1の表そのものは変更せず、履歴として残す)
- [`docs/operations/security.md`](../operations/security.md) §3.4(実装フェーズの3層防御の
  記述を更新。「GitLab側のスコープ・ロールが書き込みAPIを拒否する」という層は本ADR以降
  成立しないため、残存リスクとして明記)・§3.5(「既知の課題」から「対応済み」の記述へ)・
  §4.1(トークン表を更新)
- [`docs/operations/configuration.md`](../operations/configuration.md)・
  [`.env.example`](../../.env.example)・[`docs/operations/setup-windows.md`](../operations/setup-windows.md)・
  [`docs/operations/troubleshooting.md`](../operations/troubleshooting.md): スコープの記述を更新
- [`src/gitlab_ai_platform/adapter_mcp_server/main.py`](../../src/gitlab_ai_platform/adapter_mcp_server/main.py)
  のモジュールdocstring: `config.gitlab_token`のスコープ説明を更新(コードの挙動は変更しない)

### 残存リスク(本ADRで新たに生じる考慮事項)

- [security.md §3.4](../operations/security.md)が記録している「実装フェーズ中にClaude Codeが
  Bash経由で素の`git push`を試みても、GitLab側のスコープ・ロールが拒否する」という3層目の
  防御は、本ADR以降**成立しなくなる**(`ai-review-bot`が書き込み可能になるため)。
  実効的な防御は次の2つに縮退する:
  1. `disallowed_tools=("Bash(git push:*)",)`(Claude Codeの協力的な振る舞いを前提とした
     一段の防御。構造的な保証ではない)
  2. `GitWorkspaceManager`の`git_config`(credential.helper)がworktreeの`.git/config`に
     永続化されないこと(Claude CodeがBash経由で素の`git push`を叩いても、認証情報が
     worktree内に残っていないため、そもそも認証できず失敗する可能性が高い)
  2は環境変数として`GITLAB_AI_PLATFORM_GITLAB_TOKEN`をOS環境変数にexportする運用を
  選んだ場合に弱まる(§3.4が元々指摘していた懸念)。本ADRによりこの経路が突破された場合の
  影響度が「読み取りのみ」から「書き込み可能」に上がるため、`worker`プロセスへの
  トークン供給は`.env`ファイル経由に限定する運用(§3.4が既に推奨していた内容)を
  より強く推奨する
- 上記を踏まえても、実際に書き込みAPIを呼び出す経路はAdapter層の許可リスト+
  ハンドラごとの静的型で構造的に絞り込まれているため(「決定」節参照)、
  リスクの主な変化は「実装フェーズでのBash経由の逸脱」という、そもそも本フェーズ導入時
  (M4-8, ADR-0033)から`disallowed_tools`という非構造的な防御に依存せざるを得ないと
  分かっていた箇所に限定される
