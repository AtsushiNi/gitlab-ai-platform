# GitLab MCP Tool Bridge

- 実装場所: `src/gitlab_ai_platform/mcp_bridge/`
- 対応Issue: [#62](https://github.com/AtsushiNi/gitlab-ai-platform/issues/62) (M2-12)
- 関連ADR: [ADR-0010](../adr/0010-gitlab-mcp-tool-bridge.md)
- ステータス: 実装済み(`GitLabAdapter`の許可された14メソッド全て。M2-10 [#47](https://github.com/AtsushiNi/gitlab-ai-platform/issues/47)
  分は M2-12フォローアップ [#65](https://github.com/AtsushiNi/gitlab-ai-platform/issues/65) で対応済み)

## 責務

対話型Claude Code(Windows VS Code拡張・CLI)が、実行中にエージェント自身の判断で
`GitLabAdapter`の許可された操作(read全般 + branch作成/push/MR作成/コメント +
Issue作成・更新/MR更新)を能動的に呼び出せるようにする。`GitLabAdapter`のインスタンスを
MCPサーバー(stdio)としてラップし、
`GitLabAdapter`(Protocol、[ADR-0002](../adr/0002-gitlab-adapter-interface.md))に既に
存在するメソッドだけを1メソッド=1ツールとして透過的に公開する。

Claude Code Runner(M1-7、[docs/specs/claude-code-runner.md](claude-code-runner.md))の
`build_prompt`(MRタイトル・説明・diff等を静的なプロンプト文字列に埋め込む方式)とは
**別の経路**であることに注意。Runnerの経路はClaude Code自身が実行中に能動的に呼び出す
手段を提供しない。本コンポーネントは、対話型セッションの起動時に`--mcp-config`でこの
MCPサーバーの起動コマンドを渡すことで、エージェント自身がツール呼び出しとしてGitLab操作を
行えるようにする経路である([ADR-0010](../adr/0010-gitlab-mcp-tool-bridge.md)の「背景・制約」)。

## 前提と非対象

- 前提:
  - `GitLabAdapter`(Protocol型)のインスタンスを呼び出し元が用意して渡すこと。実運用では
    `GitLabRestAdapter`(`gitlab_adapter/rest.py`)を想定するが、本コンポーネントは
    Protocol型だけを見て動作し、具象実装に依存しない
  - 認証情報(GitLab PAT等)は、MCPサーバー起動プロセスの環境変数(または`.env`)経由で
    `config.load_config`(M0-2)を通じて取得される。本コンポーネント自身は認証情報の
    パース・保管ロジックを持たない
  - MCPクライアント(対話型Claude Code)側で`--mcp-config`にこのサーバーの起動コマンド
    (`python -m gitlab_ai_platform.mcp_bridge`)を登録すること
- 非対象:
  - 新しい権限の追加。`GitLabWriter`の許可リスト(ADR-0002、M2-10で拡充: `create_branch` /
    `push_file_changes` / `create_merge_request` / `create_merge_request_comment` /
    `update_merge_request` / `create_issue` / `update_issue`)を回避・拡張する経路には
    ならない。merge・protected branchへの直push・branch削除・管理操作、Issue/MRの
    close/reopen等の状態遷移は`GitLabAdapter`自体にメソッド(または引数)として存在しない
    ため、このブリッジ経由でも呼び出し不可能(`tests/gitlab_ai_platform/mcp_bridge/test_server.py`で担保)
  - 実MCPプロトコル(stdio上のJSON-RPC)のトランスポート実装そのもの。これは`mcp`パッケージ
    (MCP Python SDK)に委譲し、本コンポーネントはツールの登録・委譲のみを行う
  - GitLab以外の外部システムとの連携

## 公開インターフェース

実装場所: `src/gitlab_ai_platform/mcp_bridge/server.py` / `tools.py`。

```python
from gitlab_ai_platform.gitlab_adapter import GitLabAdapter
from mcp.server.mcpserver import MCPServer


def create_server(adapter: GitLabAdapter, *, name: str = "gitlab-adapter") -> MCPServer:
    """`adapter`の許可された操作をツールとして公開するMCPサーバーを組み立てる。"""
```

- `TOOL_FACTORIES: dict[str, ToolFactory]`(`tools.py`) — `GitLabAdapter`のメソッド名から、
  そのメソッドに束縛されたツール関数を生成するファクトリへの対応表。1メソッド=1ファクトリ
  関数(`ToolFactory = Callable[[GitLabAdapter], Callable[..., Any]]`)。
- `ALLOWED_TOOL_NAMES: frozenset[str]`(`server.py`) — `TOOL_FACTORIES`のキー集合。
  現時点で14個(下記「対象ツール」節)。

起動用エントリポイント: `src/gitlab_ai_platform/mcp_bridge/main.py`の`main(argv=None) -> int`。
`python -m gitlab_ai_platform.mcp_bridge`(`__main__.py`)で起動する。

```
python -m gitlab_ai_platform.mcp_bridge [--config CONFIG] [--env ENV] [--log-dir LOG_DIR]
```

- `--config` / `--env`: `config.load_config`にそのまま渡す(デフォルトは`config.toml` /
  `.env`)
- `--log-dir`: ログ出力先。標準出力はMCPのstdioプロトコル専用のため、コンソールへの
  ログ出力は行わない(`setup_logging(console=False)`)

## 対象ツール(入出力スキーマ)

現時点で`GitLabAdapter`に存在する許可された14メソッドすべてを1:1でツール化している。
引数・戻り値はプリミティブ型/`dict`/`list`のみとし、`gitlab_adapter/types.py`のdataclassは
`serialization.to_jsonable`で再帰的にdictへ変換してから返す(フィールド名はdataclassの
フィールド名をそのまま使う。詳細は[gitlab-adapter.md](gitlab-adapter.md#入出力スキーマ)の
表を参照)。

| ツール名 | 引数 | 戻り値 | 対応するAdapterメソッド |
|---|---|---|---|
| `get_version` | なし | `str` | `get_version` |
| `list_merge_requests` | `project: str`, `labels: list[str] \| None = None`, `state: str = "opened"` | `list[dict]`(`MergeRequest`) | `list_merge_requests` |
| `get_merge_request` | `project: str`, `mr_iid: int` | `dict`(`MergeRequest`) | `get_merge_request` |
| `get_merge_request_diffs` | `project: str`, `mr_iid: int` | `list[dict]`(`MergeRequestDiff`) | `get_merge_request_diffs` |
| `list_merge_request_discussions` | `project: str`, `mr_iid: int` | `list[dict]`(`Discussion`。`notes`はネストした`dict`のlist) | `list_merge_request_discussions` |
| `list_issues` | `project: str`, `labels: list[str] \| None = None`, `state: str = "opened"` | `list[dict]`(`Issue`) | `list_issues` |
| `get_issue` | `project: str`, `issue_iid: int` | `dict`(`Issue`) | `get_issue` |
| `create_branch` | `project: str`, `branch_name: str`, `ref: str` | `dict`(`Branch`) | `create_branch` |
| `push_file_changes` | `project: str`, `branch: str`, `commit_message: str`, `actions: list[dict]` | `str`(新しいcommit sha) | `push_file_changes` |
| `create_merge_request` | `project: str`, `source_branch: str`, `target_branch: str`, `title: str`, `description: str = ""` | `dict`(`MergeRequest`) | `create_merge_request` |
| `create_merge_request_comment` | `project: str`, `mr_iid: int`, `body: str` | `dict`(`Note`) | `create_merge_request_comment` |
| `update_merge_request` | `project: str`, `mr_iid: int`, `title: str \| None = None`, `description: str \| None = None` | `dict`(`MergeRequest`) | `update_merge_request` |
| `create_issue` | `project: str`, `title: str`, `description: str = ""` | `dict`(`Issue`) | `create_issue` |
| `update_issue` | `project: str`, `issue_iid: int`, `title: str \| None = None`, `description: str \| None = None` | `dict`(`Issue`) | `update_issue` |

`update_merge_request`/`update_issue`は`title`/`description`のみを受け付け、
`state_event`(close/reopen/merge相当)に対応する引数はツール関数のシグネチャ自体に
存在しない。GitLab Adapter側(ADR-0002 M2-10追記)の構造的な制約をそのまま引き継いでいる。

`push_file_changes`の`actions`は、`CommitAction`(`gitlab_adapter/types.py`)を表す辞書の配列:

```json
[{"action": "create" | "update" | "delete", "file_path": "path/to/file", "content": "..." }]
```

`action`は`CommitActionType`の値の文字列。`content`は`delete`の場合省略可(`None`扱い)。
不正な`action`値は`ValueError`(→ MCP経由では`ToolError`)になる。

## エラー時の振る舞い

- ツール関数が`GitLabAdapterError`系の例外(`GitLabApiError`/`ProtectedBranchError`。
  [gitlab-adapter.md](gitlab-adapter.md#エラー時の振る舞い)参照)を送出した場合、MCP
  Python SDK(`MCPServer`)がそれを`mcp.server.mcpserver.exceptions.ToolError`として
  ラップしてMCPクライアントに伝える。本コンポーネント自身は例外を握りつぶさない
- 存在しないツール名(例: `merge`)を呼び出した場合も同様に`ToolError`(`"Unknown tool: ..."`)
  になる。`TOOL_FACTORIES`に存在しない操作はそもそもツールとして登録されていないため、
  「未知のツール」としてしか呼び出しようがない(=禁止操作が呼び出せないことの実装上の根拠)
- `push_file_changes`の`actions`に不正な`action`値(`"create"`/`"update"`/`"delete"`以外)を
  渡した場合、`CommitActionType(...)`が送出する`ValueError`が同様に`ToolError`になる
- いずれのエラーメッセージにも認証情報(GitLab PAT等)は含まれない
  (`tests/gitlab_ai_platform/mcp_bridge/test_secrets.py`で担保。詳細は
  [ADR-0010](../adr/0010-gitlab-mcp-tool-bridge.md)「セキュリティ上の考慮」の表)

## テスト方針

実装場所: `tests/gitlab_ai_platform/mcp_bridge/`(`src/`をミラー、
[ADR-0001](../adr/0001-repository-structure.md))。実MCPプロトコル通信(stdioソケット・
パイプ)・実GitLabのどちらへも繋がない(CLAUDE.mdのテスト方針)。

- `conftest.py`: `GitLabAdapter`Protocolを満たし、呼び出し引数を記録する
  `FakeGitLabAdapter`を提供する
- `test_tools.py`: `TOOL_FACTORIES`の各エントリが生成するツール関数を直接呼び出し(MCP
  サーバーは経由しない)、対応する`FakeGitLabAdapter`のメソッドへ正しい引数で委譲される
  こと、dataclassの戻り値がJSON安全な`dict`/`list`に変換されること、
  `push_file_changes`の`actions`(dictの配列)が`CommitAction`に正しく変換されることを検証する
- `test_server.py`:
  - `ALLOWED_TOOL_NAMES`が現時点の14メソッドという決め打ちの集合と完全一致することを検証する
    (`test_allowed_tool_names_matches_current_fourteen_method_allow_list`)
  - `ALLOWED_TOOL_NAMES`が`GitLabReader`/`GitLabWriter`(Protocol)の公開メソッド集合と
    完全一致することを検証する(`test_allowed_tool_names_matches_gitlab_adapter_protocol_exactly`)。
    Adapter側にさらにメソッドが増えると、このテストが先に落ちて追従漏れに気づける
  - `create_server`が`MCPServer`インスタンス上に登録するツール名の集合(`list_tools()`)が
    期待集合と一致することを検証する
  - merge・delete_branch等の禁止操作名を`call_tool`で呼び出すと、登録されていないため
    `ToolError`(未知のツール)になることを検証する
- `test_secrets.py`: `GitLabRestAdapter`をダミートークン+フェイクセッションで構築し、
  正常系のツール呼び出し結果・異常系のエラーメッセージ・ツールの説明文
  (`TOOL_DESCRIPTIONS`)のいずれにもトークン文字列が含まれないことを検証する
- `test_main.py`: エントリポイント(`main.py`)が設定エラー時にMCPサーバーを起動せず
  エラー終了すること、エラーメッセージにトークンが含まれないことを検証する
  (正常系の`server.run(transport="stdio")`は標準入力を読み続けるため呼ばない)

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表
- [ADR-0010: GitLab MCP Tool Bridge の設計](../adr/0010-gitlab-mcp-tool-bridge.md)
- [gitlab-adapter.md](gitlab-adapter.md) — ラップ対象の`GitLabAdapter`(Protocol/REST実装)の仕様
- [claude-code-runner.md](claude-code-runner.md) — 別経路であるClaude Code Runnerの仕様
- ソースコード: `src/gitlab_ai_platform/mcp_bridge/`
  (`server.py` / `tools.py` / `serialization.py` / `main.py` / `__main__.py` / `__init__.py`)
