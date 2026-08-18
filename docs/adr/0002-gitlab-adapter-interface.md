# ADR-0002: GitLab Adapter のインターフェース設計

- Issue: [#29](https://github.com/AtsushiNi/gitlab-ai-platform/issues/29) (M1-1)
- 状態: 決定

## 背景・制約

- GitLab Adapterは「GitLabとのやりとりを一手に引き受ける唯一の窓口」であり、実装(REST)を差し替え可能にする必要がある(将来MCP実装への差し替えも想定)
- 書き込み操作は「read / branch作成 / push / MR作成 / コメント」のみ許可し、「merge / protected branchへの直push / branch削除 / 管理操作」を禁止する。GitLab PATのスコープ機構(`api`/`read_api`)だけではこの粒度の制御ができないため、**Adapter層のコード自体が呼び出せる操作を絞り込む**ことが必須

## 決定

### インターフェースは`typing.Protocol`を使う

`abc.ABC`(明示的継承)ではなく`typing.Protocol`(構造的部分型)を採用する。将来のMCP実装が本パッケージに依存せず、同じメソッド形状を満たすだけで嵌る。`@runtime_checkable`を付け、防御的チェックに使えるようにする。

読み取り(`GitLabReader`)と書き込み(`GitLabWriter`)を別のProtocolに分け、`GitLabAdapter`はその合成として定義する。

### 書き込みの許可リストは「メソッドとして存在しないこと」で表現する

`GitLabWriter`には`create_branch` / `push_file_changes` / `create_merge_request` / `create_merge_request_comment`のみを定義する。`merge`・`delete_branch`・プロジェクト管理系は意図的に定義しない。存在しないメソッドは呼べないため、「プロンプト上の約束事に頼らない」という設計方針を型システムレベルで実現する。`test_protocol.py`で許可リストとの一致を検証するテストを置く。

`push_file_changes`はGitLab Commits API経由のコミット作成であり、git経由の直接pushではない。protected branchへの拒否などの実行時権限判定はREST実装側の責務。

### データ型はGitLab REST APIのレスポンスをそのまま透過させない

`MergeRequest` / `MergeRequestDiff` / `Discussion` / `Note` / `Branch` / `CommitAction`をAdapter独自のdataclassとして定義し、呼び出し側がREST APIのJSON構造に直接依存しないようにする。

## 却下した選択肢

- **`abc.ABC`**: 将来のMCP実装が本パッケージへの依存(継承)を必須にされてしまう
- **許可リストを実行時のガード関数で表現する**: 「そもそもメソッドとして存在しない」という型チェック時点で検出できる制約を優先した
- **GitLab REST APIのレスポンス(dict)をそのまま返す**: 呼び出し側がAPIのフィールド名変更に直接晒される

## 影響

- REST実装は`GitLabReader`/`GitLabWriter`を満たす具象クラスとして実装する
- 将来のMCP実装も本ADRのProtocolを満たす形で追加すれば、呼び出し側の変更なしに差し替えられる

## 追記(M1-3)

- protected branchへの直push拒否を`push_file_changes`の事前チェックとして実装
- 全書き込み操作の監査ログをREST実装に追加
- config層でのbranch名パターンによる追加ガードは、Adapter層がConfigに依存する形になり責務境界が崩れるため見送り(Runner側の設計が固まった時点で再検討)

## 追記(M2-10)

Issue操作(読み取り・作成・更新)とMR更新を許可リストに追加。`update_issue`/`update_merge_request`は`title`/`description`のみをキーワード専用引数として持ち、GitLab APIの`state_event`(close/reopen等)に相当する引数を**メソッドシグネチャ自体に存在させない**ことで、状態遷移(merge・クローズ・削除等)を構造的に禁止し続けている。`state_event`を受け付けてREST実装側で拒否する案は実行時バリデーション依存に逆戻りするため却下した。
