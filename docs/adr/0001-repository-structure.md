# ADR-0001: リポジトリ構成と依存方針

- Issue: [#1](https://github.com/AtsushiNi/gitlab-ai-platform/issues/1)
- 状態: 決定

## 背景・制約

- 言語はPython(`references/タスク整理.md`で確定済み)
- 実行環境はWindows・管理者権限なし・外部ダウンロード制限ありが前提(将来のM3以降はLinux/Dockerだが、共通コードは両環境で動く必要がある)

## 決定

- **Pythonバージョン**: 3.11以上(型ヒント`X | None`・`tomllib`が使え、Windowsでも配布インストーラで入手しやすい)
- **パッケージ管理**: pip + venv(標準ライブラリのみ)。`uv`等の追加バイナリは、外部ダウンロード制限のあるWindows環境での導入可否が不透明なため使わない
- **外部依存は最小限**: `requests`(GitLab REST API呼び出し)・`pytest`(テスト)・`mcp`(MCP Python SDK、GitLab MCP Tool Bridge用)のみ許可。新しい依存を追加する場合は、Windowsのオフライン制約下で入手可能かを確認してから追加する
- **ディレクトリレイアウト**: `src/`レイアウトを採用(`src/gitlab_ai_platform/`配下にモジュールごとのパッケージ、`tests/`は`src/`の構成をミラー)。モジュール境界は`references/タスク整理.md`のarea/*ラベルに対応させる。まだ存在しないモジュールディレクトリは、対応するIssueに着手する時点で作る
- **テスト**: `pytest`

## 却下した選択肢

- **uv**: 速度面では魅力的だが、Windows環境での導入可否が不透明なため見送り。Linux/Docker側限定での採用余地は残す
- **flatレイアウト**: importの事故(テストがインストール前のソースを誤って拾う等)を避けるため`src/`レイアウトを優先
- **Poetry等のフル依存管理ツール**: 依存が最小限である間はオーバースペック

## 影響

以降の全Issueは本ADRのモジュール境界・依存方針に従う。

## 追記(M2-12)

対話型Claude CodeがGitLab操作をツールとして呼び出せるようにするため、`mcp`(MCP Python SDK)を依存に追加した(GitLab MCP Tool Bridge)。`pydantic`・`starlette`等の重量級の推移的依存を伴う点、Windowsオフライン環境での入手可否は実運用投入時に別途確認が必要な既知のリスクとして残る。
