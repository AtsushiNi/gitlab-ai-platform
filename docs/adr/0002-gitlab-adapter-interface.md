# ADR-0002: GitLab Adapter のインターフェース設計

- Issue: [#29](https://github.com/AtsushiNi/gitlab-ai-platform/issues/29) (M1-1)
- 状態: 決定

## 背景・制約

- `docs/architecture.md` の設計方針により、GitLab Adapterは「GitLabとのやりとりを一手に引き受ける
  唯一の窓口」であり、実装(REST, M1-2)を差し替え可能にする必要がある。将来、社内GitLabが
  公式MCPをサポートするバージョンになった場合にMCP実装へ差し替える前提(`references/spike-S2-gitlab-rest-api.md`)。
- 書き込み操作は「read / branch作成 / push / MR作成 / コメント」のみ許可し、
  「merge / protected branchへの直push / branch削除 / 管理操作」を禁止する。
  Spike S-2で確認した通り、GitLab PATのスコープ機構(`api`/`read_api`等)だけではこの粒度の
  制御はできない(`api`スコープは常に読み書き全体を含む)。マージ可否はプロジェクトロールで
  制御される仕組みのため、**Adapter層のコード自体が呼び出せる操作を絞り込む**ことが必須。

## 決定

### インターフェースは `typing.Protocol` を使う

`abc.ABC`(明示的継承が必要)ではなく `typing.Protocol`(構造的部分型)を採用する。

- 将来のMCP実装が本パッケージに依存せず、同じメソッド形状を満たすだけでこのインターフェースに
  嵌る(`docs/architecture.md`のmermaid図で「Protocol + REST実装」と明記されている表現と一致)。
- `@runtime_checkable`を付け、`isinstance(impl, GitLabAdapter)`でテスト・呼び出し側の
  防御的チェックに使えるようにする(メソッド名の存在チェックのみで、シグネチャや戻り値型までは
  検証しない制約は許容する)。

読み取り(`GitLabReader`)と書き込み(`GitLabWriter`)を別のProtocolに分け、
`GitLabAdapter`はその合成として定義する。読み取り専用の呼び出し元(将来的に`read_api`スコープの
PATしか持たないコンポーネントを作る場合)が`GitLabWriter`に依存せずに済むようにするため。

### 書き込みの許可リストは「メソッドとして存在しないこと」で表現する

`GitLabWriter`には以下の4メソッドのみを定義する: `create_branch` / `push_file_changes` /
`create_merge_request` / `create_merge_request_comment`。

`merge`・`delete_branch`・プロジェクト管理系のメソッドは意図的に定義しない。呼び出し側が
Adapterのインターフェースだけを見て実装する限り、これらの操作はコード上呼び出しようがない
(存在しないメソッドは呼べない)。これは「プロンプト上の約束事に頼らない」という設計方針
(`docs/architecture.md`)をインターフェースの型システムレベルで実現する。

`tests/gitlab_ai_platform/gitlab_adapter/test_protocol.py`に、`GitLabWriter`の公開メソッド集合が
許可リストと一致することを検証するテストを置き、将来誰かが禁止操作をうっかり追加した場合に
テストが落ちるようにしている。

`push_file_changes`はGitLab Commits API(`POST /projects/:id/repository/commits`)経由の
コミット作成を表し、git経由の直接pushではない。protected branchへの拒否など**実行時の権限判定**は
このインターフェースの責務ではなく、REST実装(M1-2)側で行う
(インターフェースはあくまで「呼び出せる操作の形」を絞るところまで)。

### データ型はGitLab REST APIのレスポンスをそのまま透過させない

`MergeRequest` / `MergeRequestDiff` / `Discussion` / `Note` / `Branch` / `CommitAction` を
Adapter独自のdataclassとして`types.py`に定義し、呼び出し側(Poller/Runner/CLI)がREST APIの
JSON構造やフィールド名の変化に直接依存しないようにする。MCP実装に差し替えた際も、この型さえ
維持すれば呼び出し側の変更は不要になる。

## 却下した選択肢

- **`abc.ABC`による抽象基底クラス**: 将来のMCP実装がこのパッケージへの依存(継承)を必須にされる。
  構造的部分型の方が「同じ口に嵌める」という要件に合う。
- **許可リストを実行時のガード関数(デコレータや検証層)で表現する**: M1-1の時点ではインターフェース
  定義のみが責務であり、実行時のガードはREST実装や許可リスト機構強化(M1-3)の役割。M1-1では
  「そもそもメソッドとして存在しない」という最も強い制約(コンパイル/型チェック時点で検出できる)を
  優先した。
- **GitLab REST APIのレスポンス(dict)をそのまま返す**: 呼び出し側がAPIのフィールド名変更や
  REST/MCPの構造差異に直接晒される。Adapter層で正規化する方針を優先した。

## 影響

- M1-2(REST実装)は`GitLabReader`/`GitLabWriter`を満たす具象クラスとして実装する。
- 将来のMCP実装(M1後続)も本ADRのProtocolを満たす形で追加すれば、呼び出し側の変更なしに
  差し替えられる。

## 追記(M1-3、[#31](https://github.com/AtsushiNi/gitlab-ai-platform/issues/31))

本ADRで定義した「メソッドとして存在しない」という静的な制約に加えて積み上げる予定だった
実行時チェックのうち、確定した内容:

- **protected branchへの直push拒否**は、当初の想定通りM1-2([PR #46](https://github.com/AtsushiNi/gitlab-ai-platform/pull/46))で
  `push_file_changes`の対象branch事前チェック(`ProtectedBranchError`)として実装済み。
- **全書き込み操作の監査ログ**をM1-3でREST実装(`rest.py`)に追加した。X-1(セキュリティレビュー)の
  証跡として、書き込み操作の呼び出し(成功/拒否/エラー)を構造化ログに残す。
- **GitLabのprotected branchフラグに依存しない、config層でのbranch名パターンによる追加ガード**は
  M1-3では見送った。理由: (1) push先branch名を決めるのはRunner/Poller側だが、M1時点ではまだ
  未実装でどんなbranch名を生成しうるか実態がなく、パターンを先に固定する費用対効果が低い。
  (2) 追加するとAdapter層がConfigに依存する形になり、「GitLab APIとのやりとりに専念する」という
  Adapterの責務境界(`docs/architecture.md`)が崩れる。Runner側の設計が固まった時点
  (M2以降)で、Runner層のガードとして再検討する。

## 追記(M2-10、[#47](https://github.com/AtsushiNi/gitlab-ai-platform/issues/47))

`docs/requirements.md` 3-C(新規の開発要件をIssueへ分解する)の土台として、Issue操作
(読み取り・作成・更新)とMR更新を許可リストに追加した。

### 許可リストの拡張

`GitLabReader`に`list_issues` / `get_issue`を追加(読み取り5→7)。`GitLabWriter`に
`create_issue` / `update_issue` / `update_merge_request`を追加(書き込み4→7)。

`Issue`型は`types.py`に新設し、`MergeRequest`と同じ設計方針(project/iid/title/description/
state/author/labels/web_urlの正規化されたdataclass、`frozen=True`)に倣った。

### 状態遷移を「引数として存在しない」ことで禁止する

Issue本文(#47)の「merge・クローズ・削除等は含めない」という制約を、`update_issue`/
`update_merge_request`という**更新系メソッドの内部**でも維持する必要があった。GitLab REST API
の`PUT /projects/:id/issues/:iid`および`PUT /projects/:id/merge_requests/:iid`は、
`title`/`description`と同じボディに`state_event`(`close`/`reopen`、MRの場合はさらに
mergeに近い遷移も)を受け付けるため、素朴に「更新用のオプション引数を全部受け取る」設計にすると
呼び出し側が`state_event="close"`を渡すだけでクローズできてしまい、「メソッドとして存在しない」
という本ADRの元々の設計思想(禁止操作をコード上呼び出しようがなくする)が骨抜きになる。

このため、`update_issue`/`update_merge_request`のシグネチャには`title: str | None = None`と
`description: str | None = None`の2つのキーワード専用引数のみを持たせ、`state_event`に相当する
引数を**メソッドシグネチャ自体に存在させない**設計にした。REST実装(`rest.py`)側も、送信ボディを
組み立てるヘルパー`_build_update_body(*, title, description)`を経由させることで、実装の途中で
誰かが`state_event`を書き足そうとしても、まずヘルパー関数のシグネチャ変更が必要になるという
一段の防御を入れている。

これは本ADRの「実行時チェックではなく、そもそも呼び出せない・渡せないという構造的な制約を
優先する」という設計思想(冒頭「書き込みの許可リストは『メソッドとして存在しないこと』で表現する」
節)を、メソッド単位の粒度からメソッドの**引数**単位の粒度まで一段細かく適用したもの。

`test_protocol.py`には、`close_issue` / `reopen_issue` / `close_merge_request` /
`reopen_merge_request` / `delete_issue`を禁止操作名の集合に追加し、`GitLabWriter`の公開メソッド
集合との非交差を検証する形で反映した。`test_rest.py`には、`update_issue`/`update_merge_request`
が実際にGitLab APIへ送信するリクエストボディに`state_event`キーが含まれないことを直接検証する
回帰テストを追加した(メソッドが存在しないことのテストだけでは、「引数を持たない」という制約は
検証できないため)。

### 却下した選択肢

- **`update_issue`/`update_merge_request`に`state_event: Literal["close", "reopen"] | None`を
  持たせ、実装側(REST実装)で`merge`相当の値だけを拒否する**: 実行時バリデーションに頼る設計であり、
  本ADRが最初から避けてきた「プロンプト上の約束事・実行時チェックへの依存」に逆戻りする。
  型システムレベルで「存在しない」ことを保証できる今回の設計を優先した。
- **`close_issue` / `reopen_issue`のような専用メソッドを別途増やす**: Issue本文で明示的に
  「merge・クローズ・削除等は含めない」とされており、現時点で必要になっていない操作を
  先回りして許可リストに載せる理由がない。将来必要になった時点で、個別に本ADRを更新して追加する
  (Issue本文の「必要になれば個別に追加を検討する」という方針に従う)。
