# ADR-0010: GitLab MCP Tool Bridge の設計

- Issue: [#62](https://github.com/AtsushiNi/gitlab-ai-platform/issues/62) (M2-12)
- 状態: 決定

## 背景・制約

### Claude Code Runner(M1-7)とは別の経路であること

過去の議論で、「GitLab Adapter(M1-1〜M1-3)を作ったのだから、対話型Claude Code(Windows VS Code
拡張・CLI)は既にGitLab操作を呼び出せるはずだ」という認識のズレがあった。実際には以下の2つは
まったく別の経路であり、本ADRはこのうち後者を新設する。

- **Claude Code Runner(`runner/`、[ADR-0005](0005-claude-code-runner-design.md))**:
  MRのタイトル・説明・コメント・diffを`build_prompt`で**静的な文字列**に埋め込み、`claude -p
  "<prompt>"`としてヘッドレス実行する。Claude Code自身はこの文字列を読むだけで、実行中に
  能動的にGitLab APIを呼び出す手段を持たない。M1レビュー自動化の主経路
- **本ADRのMCP Tool Bridge**: 対話型Claude Code(人間が操作するセッション)が、実行中に
  エージェント自身の判断で「このMRの別のコメントを見る」「調査結果をコメント投稿する」といった
  操作を能動的に呼び出すための経路。M2-4(追加調査モード)・M2-11(要件→Issue分解ワークフロー)が
  前提とする基盤

つまり「GitLab Adapterというライブラリが存在すること」と「Claude Codeエージェント自身がそれを
ツールとして呼び出せること」は別問題であり、後者を実現するには対話型Claude Codeのツール呼び出し
機構(MCP)に載せる必要がある、というのが本ADRの出発点。

### 対象操作は現時点のGitLabAdapterの9メソッドのみ

本Issue着手時点で、別セッションがIssue [#47](https://github.com/AtsushiNi/gitlab-ai-platform/issues/47)
(M2-10: `list_issues`/`get_issue`/`create_issue`/`update_issue`/`update_merge_request`の追加)を
並行実装中だが、まだこのブランチには反映されていない。そのため本ADR・実装は、現時点で
`GitLabAdapter`に存在するメソッドのみを対象にする。

- 読み取り5([`GitLabReader`](../specs/gitlab-adapter.md#公開インターフェース)):
  `get_version` / `list_merge_requests` / `get_merge_request` / `get_merge_request_diffs` /
  `list_merge_request_discussions`
- 書き込み4(`GitLabWriter`、ADR-0002の許可リスト): `create_branch` / `push_file_changes` /
  `create_merge_request` / `create_merge_request_comment`

### 新しい権限を追加してはならない

このブリッジは`GitLabAdapter`(Protocol、ADR-0002)に既に存在するメソッドを透過的に公開する
だけの層であり、`GitLabWriter`の許可リストを回避・拡張する経路になってはならない。
merge・protected branchへの直push・branch削除・管理操作はAdapter自体にメソッドが存在しない
ため、このブリッジ経由でも呼び出しようがない、という性質をそのまま引き継ぐ。

### 認証情報の扱い

GitLab PAT等の認証情報は、MCPサーバー起動時の環境変数(または`.env`)経由で受け取る。
`config/loader.py`の`load_config`(M0-2で確立済み)をそのまま再利用し、このブリッジ自身が
認証情報のパース・保管ロジックを新たに持たない。ツールの引数・戻り値・ログには認証情報を
一切含めない。

## 決定

### GitLab AdapterをMCPサーバー(stdio)でラップし、`--mcp-config`で渡す

`src/gitlab_ai_platform/mcp_bridge/`に、`mcp`パッケージ(MCP Python SDK。`pyproject.toml`の
`dependencies`に追加)を使ったstdio MCPサーバーを実装する。`python -m
gitlab_ai_platform.mcp_bridge`で起動できるようにし(`docs/architecture.md`の「Windows」側で
人間が使うツールとして動く)、対話型Claude Code側の`--mcp-config`にこの起動コマンドを登録する
運用を前提とする(`docs/specs/gitlab-mcp-bridge.md`参照)。

`docs/architecture.md`の「MVP → AI Platformへの成長パス」表がGitLab Adapterについて
「同じProtocolの上でMCP実装に差し替え可能になる」と述べているのは、GitLab Adapter**自体**の
実装(REST→MCPクライアント)の話であり、本ADRのMCP Tool Bridge(GitLab AdapterをMCP**サーバー**
として公開する層)とは向きが逆であることに注意。両者は将来共存しうる(社内GitLabが公式MCPを
サポートした場合、GitLab Adapterの実装がRESTからMCPクライアントに差し替わっても、本ブリッジは
そのAdapterインスタンスをラップし続けるだけで変更不要)。

### 実装はMCP Python SDKの高レベルAPI(`MCPServer`)を使う

インストールされた`mcp`パッケージ(2.x系)の`mcp.server.mcpserver.MCPServer`
(旧バージョン系列での`FastMCP`に相当する高レベルAPI)を使う。デコレータ/`add_tool`で
Pythonの関数をそのままツールとして登録でき、引数・戻り値の型ヒントからJSON Schemaが
自動生成される。低レベルAPI(`mcp.server.lowlevel.Server`)によるプロトコルメッセージの
手組みは行わない。

### ツール一覧は「1メソッド=1ファクトリ関数」のマッピングテーブルで持つ

`Protocol`のメソッド一覧を`inspect`等で完全自動列挙する方式ではなく、
`src/gitlab_ai_platform/mcp_bridge/tools.py`の`TOOL_FACTORIES: dict[str, ToolFactory]`という
明示的な対応表を採用した。

```python
TOOL_FACTORIES: dict[str, ToolFactory] = {
    "get_version": _make_get_version,
    "list_merge_requests": _make_list_merge_requests,
    # ...(9エントリ)
}
```

各ファクトリ(`_make_get_version(adapter) -> Callable`)は、`GitLabAdapter`の対応する
1メソッドだけに委譲する、プリミティブ型/`dict`/`list`のみを入出力するクロージャを返す。
`server.py`の`create_server`はこの対応表を走査して`MCPServer.add_tool`を呼ぶだけで、
権限の実体(何を許可するか)は`tools.py`の対応表そのものが担う。

**M2-10([#47](https://github.com/AtsushiNi/gitlab-ai-platform/issues/47))マージ後のTODO**:
`GitLabReader`/`GitLabWriter`に`list_issues`/`get_issue`/`create_issue`/`update_issue`/
`update_merge_request`が追加された時点で、以下を行う必要がある。

1. `tools.py`に各メソッド用の`_make_xxx`ファクトリ関数を追加し、`TOOL_FACTORIES`/
   `TOOL_DESCRIPTIONS`に登録する
2. `tests/gitlab_ai_platform/mcp_bridge/test_server.py`の
   `_EXPECTED_ALLOWED_TOOL_NAMES`(現在9個決め打ち)を新しいメソッド集合に更新する
   (更新しない限り`test_allowed_tool_names_matches_gitlab_adapter_protocol_exactly`が
   意図的に失敗し続け、追従漏れに気づける設計にしてある)
3. 本specの「対象ツール」節・入出力スキーマを追記する

この対応表方式にしたのは、Protocol由来の完全自動列挙も検討したが「却下した選択肢」の通り
型変換の信頼性の問題があったため。ただし対応表への追加自体は1メソッドあたり数行の
機械的な差分で済むため、[#62のタスク指示](https://github.com/AtsushiNi/gitlab-ai-platform/issues/62)
が求める「最小限の差分での拡張しやすさ」は満たしている。

### 入出力はプリミティブ型/`dict`/`list`のみとし、Adapterのdataclassを直接公開しない

`gitlab_adapter/types.py`の`MergeRequest`等のdataclassをMCPツールの引数・戻り値の型として
そのまま使わず、`serialization.to_jsonable`で再帰的にdictへ変換してから返す。理由:

- MCPクライアント(対話型Claude Code)側はJSON Schemaでツールを認識するため、Adapter独自の
  dataclass型をそのまま型ヒントに使うと、SDKの自動スキーマ生成がdataclass/Enumの組み合わせを
  正しく扱えるかどうかに実装が依存してしまう(検証済みの単純な型だけを境界に置きたい)
- `push_file_changes`の`actions`引数(`Sequence[CommitAction]`)のように、Adapter側は
  Enum(`CommitActionType`)を含む型を要求するメソッドがある。これをMCP経由でも文字列
  (`"create"`/`"update"`/`"delete"`)として受け取り、ツール関数内で`CommitActionType(...)`に
  変換する

### 認証情報はconfig層を再利用し、ツールの引数・戻り値・ログに含めない

エントリポイント(`mcp_bridge/main.py`)は`config.load_config`をそのまま呼び、
`GitLabRestAdapter(config.gitlab_url, config.gitlab_token)`を構築する。`mcp_bridge`パッケージ
自身は`.env`のパース・環境変数の読み取りロジックを持たない。ツール関数(`tools.py`)はいずれも
`adapter`のメソッドを呼ぶだけで、認証情報そのもの(トークン文字列)を引数・戻り値として
扱う経路が存在しない。標準出力はMCPのstdioプロトコル専用に使われるため、
`main.py`は`setup_logging(console=False)`でコンソールへのログ出力を止め、ログはファイルのみに
出す。

## 却下した選択肢

- **Protocolのメソッド一覧を`inspect`で完全自動列挙し、型ヒントからツールを生成する**:
  `GitLabWriter.push_file_changes`のように、Adapter独自のEnum/dataclassを含む型
  (`Sequence[CommitAction]`)を持つメソッドがあり、MCP SDKの自動スキーマ生成にそのまま
  委ねると、生成されるJSON Schemaの形が保証されずMCPクライアント側の使い勝手・デバッグ性が
  下がる懸念があった。将来的に自動列挙へ寄せる余地は残すが(`TOOL_FACTORIES`のキー集合を
  Protocolのメソッド集合と突き合わせるテストは既に用意している)、M2-12時点では明示的な
  マッピングテーブルを優先した。
- **Claude Code Runner(`build_prompt`)の埋め込みプロンプトを拡張し、GitLab操作の指示も
  文字列として含める**: 「背景・制約」で述べた通り、静的プロンプト埋め込みでは実行中の
  能動的な呼び出しができない。M2-4/M2-11が要求する「対話中に追加でGitLab操作を呼ぶ」という
  要件そのものを満たせないため不採用。
- **MCPサーバー側で新しい書き込み操作(コメント編集・ラベル変更等)を追加する**:
  「新しい権限を一切追加しない」という本ADRの前提(セキュリティ上の考慮)に反する。
  必要になった場合は、まずGitLab Adapter側(ADR-0002の許可リスト)を拡張するIssueを起票し、
  そちらが先に決定されてから本ブリッジに反映する順序を守る。
- **`--dangerously-skip-permissions`相当の全許可でMCPサーバー自体を無制限にする**:
  ADR-0002・[ADR-0005](0005-claude-code-runner-design.md)と同じ考え方で、危険な操作への
  近道をコード上用意しない方針を踏襲し不採用。

## セキュリティ上の考慮(新規権限を追加しないことの担保)

| 担保したい性質 | 実装 | テスト |
|---|---|---|
| merge・branch削除・管理操作がこのブリッジ経由で呼び出せない | `tools.py`の`TOOL_FACTORIES`に該当メソッドを追加しない(Adapter自体にメソッドが存在しないため追加しようがない) | `tests/.../test_server.py::test_allowed_tool_names_does_not_include_forbidden_operations`、`test_call_tool_rejects_forbidden_operations_as_unknown`(未知のツールとして拒否されることを確認) |
| 公開ツール集合がAdapterの許可リストを超えない | `ALLOWED_TOOL_NAMES = frozenset(TOOL_FACTORIES)`を、現時点の9メソッドという決め打ち集合、および`GitLabReader`/`GitLabWriter`のProtocol由来の集合の両方と突き合わせる | `test_allowed_tool_names_matches_current_nine_method_allow_list`、`test_allowed_tool_names_matches_gitlab_adapter_protocol_exactly` |
| 認証情報がツールの引数・戻り値・エラーメッセージに含まれない | ツール関数はAdapterのメソッド呼び出しに徹し、トークンを一切扱わない。`main.py`はconfig層のみからトークンを取得しAdapter構築にのみ使う | `tests/.../test_secrets.py`(ダミートークンを使い、成功時レスポンス・エラーメッセージ・ツール説明文のいずれにも含まれないことを検証) |

## 影響

- `pyproject.toml`の`dependencies`に`mcp`を追加する(MCP Python SDK)。
- `docs/specs/gitlab-mcp-bridge.md`を新設し、`docs/README.md`の一覧に追記する。
- `docs/architecture.md`の「コンポーネントの責務と境界」表に`mcp_bridge`の行を追加する。
- M2-4(追加調査モード)・M2-11(要件→Issue分解ワークフロー)は、本ブリッジの起動コマンドを
  対話型Claude Codeの`--mcp-config`に登録することを前提に設計できるようになる。
- M2-10([#47](https://github.com/AtsushiNi/gitlab-ai-platform/issues/47))マージ後、
  「決定」節のTODOに従って`tools.py`・テスト・specを追従させる必要がある(このADR自体も
  対象メソッド数の記述を更新すること)。
