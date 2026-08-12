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
このインターフェースの責務ではなく、REST実装(M1-2)と許可リスト機構の強化(M1-3)側で行う
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
- M1-3(書き込み許可リスト機構)は、本ADRで定義した「メソッドとして存在しない」という静的な制約に加え、
  実行時のチェック(protected branch判定など)を積み上げる。
- 将来のMCP実装(M1後続)も本ADRのProtocolを満たす形で追加すれば、呼び出し側の変更なしに
  差し替えられる。
