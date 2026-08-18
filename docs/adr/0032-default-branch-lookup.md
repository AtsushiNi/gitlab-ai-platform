# ADR-0032: GitLab Adapterへのdefault branch取得メソッドの追加

- Issue: [#114](https://github.com/AtsushiNi/gitlab-ai-platform/issues/114) (M4-8)
- 状態: 決定

## 背景・制約

- `GitLabWriter.create_branch(project, branch_name, ref)`(許可リストの書き込み操作、
  `docs/adr/0002-gitlab-adapter-interface.md`)は、branchを作成する起点の`ref`を呼び出し側が
  用意する前提のシグネチャになっている
- 実装フェーズ(M4-8)は、対象プロジェクトのdefault branch(多くの場合`main`)を起点に
  実装用branchを作成する必要がある(ADR-0031「決定」、ADR-0033)。しかし`GitLabReader`
  (`gitlab_adapter/protocol.py`)には、プロジェクトのdefault branchを取得する手段が
  これまで存在しなかった(ADR-0027・ADR-0029・ADR-0030がいずれも「Adapterにdefault branch
  取得等の新規メソッドが必要になる」と指摘し、対応を先送りしてきた課題)
- 本Issueで初めて実際にbranch作成が必要になるため、ここで対応する

## 決定

### `GitLabReader`に`get_default_branch(project: str) -> str`を追加する

```python
def get_default_branch(self, project: str) -> str:
    """プロジェクトのdefault branch名を取得する。"""
    ...
```

`GitLabRestAdapter`(`gitlab_adapter/rest.py`)は`GET /projects/:id`のレスポンスから
`default_branch`フィールドのみを取り出す実装にする。読み取り専用の追加のため、
`docs/operations/security.md` §2.3の禁止操作リストには抵触しない
(§2.1の読み取り操作リストに1件追加するのみ)。

### 包括的な`Project`型・`get_project`メソッドは導入せず、最小限の`get_default_branch`にする

Issue本文が選択肢として挙げていた`GitLabReader.get_project(project) -> Project`
(プロジェクト情報全体を返す包括的なメソッド)ではなく、`get_default_branch(project) -> str`
という最小限のメソッドを追加した。

理由:

- 現時点で実装フェーズが必要とするのは`default_branch`という1フィールドのみで、
  `Project`型が持つべき他のフィールド(可視性、名前空間、その他メタデータ)を今使う予定が無い
- `gitlab_adapter/types.py`の既存の型(`MergeRequest`/`Issue`等)は「APIレスポンスから
  必要なフィールドだけを正規化する」という設計方針を取っており、使う予定のないフィールドを
  持つ包括的な型を先回りして作らない
- 将来他のフィールド(可視性・保護branch一覧等)が必要になった時点で、
  `get_project(project) -> Project`のような包括的なメソッド・型を追加すればよい
  (このADRの決定を変更するのではなく、新しいメソッドを追加する形で拡張できる)

## 却下した選択肢

- **`GitLabReader.get_project(project) -> Project`(包括的な型)**: 「決定」節に記載の通り、
  現時点で必要なフィールドが`default_branch`のみであるため、YAGNI(不要な先回り実装をしない)
  の観点から見送った
- **呼び出し側(`build_implement_handler`)がdefault branch名を設定値として決め打ちする
  (例: `"main"`固定)**: 対象プロジェクトによってdefault branchは`main`/`master`/`develop`等
  さまざまであり、決め打ちは誤ったbranchを起点にしてしまう危険がある。Adapter経由で
  実際の値を取得する方が確実
- **`create_branch`の`ref`引数をoptionalにし、省略時にAdapter内部でdefault branchを
  自動解決する**: `create_branch`(書き込み操作)の内部でGET(読み取り)を暗黙に行うことになり、
  「1メソッド=1操作」という`GitLabWriter`の許可リストの単純さが崩れる。呼び出し側が
  `get_default_branch`→`create_branch`と明示的に2段階で呼ぶ方が、何が起きているか
  追いやすい

## 影響

- `src/gitlab_ai_platform/gitlab_adapter/protocol.py`の`GitLabReader`に`get_default_branch`を
  追加(7→8メソッド)。`tests/gitlab_ai_platform/gitlab_adapter/test_protocol.py`の
  メソッド集合完全一致テストを更新
- `src/gitlab_ai_platform/gitlab_adapter/rest.py`の`GitLabRestAdapter`に実装を追加。
  `tests/gitlab_ai_platform/gitlab_adapter/test_rest.py`にテストを追加
- `src/gitlab_ai_platform/adapter_mcp_server/tools.py`の`TOOL_FACTORIES`/`TOOL_DESCRIPTIONS`に
  `get_default_branch`を追加(読み取り専用の追加のため、`docs/adr/0010-gitlab-mcp-tool-bridge.md`
  「新しい権限を一切追加しない」という設計方針にも抵触しない。GitLab Adapterの許可リストを
  そのまま透過するだけ)。`tests/gitlab_ai_platform/adapter_mcp_server/`配下の
  `ALLOWED_TOOL_NAMES`完全一致テスト群(14→15メソッド)を更新
- `src/gitlab_ai_platform/cli/dispatcher.py`の`build_implement_handler`(ADR-0033)が
  `get_default_branch`を使う
