# ADR-0001: リポジトリ構成と依存方針

- Issue: [#1](https://github.com/AtsushiNi/gitlab-ai-platform/issues/1)
- 状態: 決定

## 背景・制約

- 言語は Python で確定済み(`references/タスク整理.md`)
- 実行環境はWindows・管理者権限なし・外部ダウンロード制限あり、が前提(レビューツール側)。
  将来のAI Platform本体(M3以降)はLinux/Dockerだが、共通コードは両環境で動く必要がある
- 現時点でこのIssueの担当は将来的にM0-2/M0-3(設定管理・ロガー実装)がすぐ後に続くため、
  ADRの決定だけでなく、それらが乗れる最小限の骨組みも一緒に作る

## 決定

### Pythonバージョン

**3.11以上**を対象とする。型ヒント(`X | None`等)とtomllib(標準ライブラリでのTOML読み込み)が
使え、Windows環境でも配布インストーラで入手しやすいバージョン帯。

### パッケージ管理

**pip + venv(標準ライブラリのみ)** を採用する。`uv`等の追加バイナリは使わない。

理由: Windows環境は「外部ダウンロード制限あり」が明記された制約であり、`uv`自体の導入が
その制約に抵触する可能性がある。`pip`は標準Pythonに同梱されており追加の導入ステップが要らない。
将来Linux/Docker側(M3以降)で`uv`を使う判断はあり得るが、共通コードは`pip`のみで
成立する形を維持する。

外部依存は最小限に絞る。現時点で許可するのは以下のみ:

- `requests` — GitLab REST API呼び出し用。標準ライブラリの`urllib`でも代替可能だが、
  リトライ・エラーハンドリングの書きやすさを優先して許可する
- `pytest` — テスト用(開発依存)
- `mcp`(MCP Python SDK) — GitLab MCP Tool Bridge(M2-12)用。詳細・注記は本ページ末尾の
  「追記(M2-12、#62)」、設計判断は[ADR-0010](0010-gitlab-mcp-tool-bridge.md)を参照

新しい依存を追加する場合は、Windowsのオフライン制約下で入手可能か(事前に社内ミラー等へ
配置できるか)を確認してからにする。

### ディレクトリレイアウト

`src/` レイアウトを採用する。

```text
gitlab-ai-platform/
├── pyproject.toml
├── src/
│   └── gitlab_ai_platform/
│       ├── __init__.py
│       ├── config/         設定・シークレット管理 (M0-2)
│       ├── logging_/       ログ (M0-3, `logging`標準モジュールとの名前衝突を避けるため logging_)
│       ├── gitlab_adapter/ GitLab Adapter (M1-1, M1-2, M1-3)
│       ├── store/          State Store (M1-4)
│       ├── poller/         MR Poller (M1-5)
│       ├── workspace/      Workspace Manager (M1-6)
│       ├── runner/         Claude Code Runner (M1-7)
│       ├── review/         レビュープロンプト・結果スキーマ (M1-8, M1-9)
│       └── cli/            CLI (M1-10, M1-11)
├── tests/
│   └── (src/ の構成をミラーする)
├── docs/
├── references/
└── CLAUDE.md
```

モジュール境界は`references/タスク整理.md`のarea/*ラベルに対応させている。
まだ存在しないモジュールディレクトリは、対応するIssueに着手する時点で作る
(空ディレクトリを先回りしては作らない)。

### テスト

`pytest`。`tests/`配下に`src/`と対応する構成でミラーする。

## 却下した選択肢

- **uv**: 速度面では魅力的だが、外部ダウンロード制限のあるWindows環境での導入可否が
  不透明なため見送り。将来Linux/Docker側限定で採用する余地は残す
- **flat レイアウト(`src/`なしでリポジトリ直下にパッケージを置く)**: importの事故
  (テストがインストール前のソースを誤って拾う等)を避けるため`src/`レイアウトを優先
- **Poetry等のフル依存管理ツール**: 依存が最小限である間はオーバースペック。
  依存が増えてきたら再検討する

## 影響

M0-2・M0-3はこの構成の上に実装する。以降のIssueも本ADRのモジュール境界に従う。

## 追記(M2-12、[#62](https://github.com/AtsushiNi/gitlab-ai-platform/issues/62))

対話型Claude CodeがGitLab操作をツールとして呼び出せるようにするため、`mcp`(MCP Python SDK)を
依存に追加した([ADR-0010](0010-gitlab-mcp-tool-bridge.md))。この用途はGitLab Adapterの
既存操作をMCPサーバーとして公開することそのものが目的であり、`requests`同様「標準ライブラリでは
代替が困難(MCPプロトコルの実装をゼロから書くのは非現実的)」という基準で許可した。

一点、本ADRの「Windowsのオフライン制約下で入手可否を確認してから」という基準に対する注記:
`mcp`は`pydantic`・`starlette`・`cryptography`・`uvicorn`・`opentelemetry-api`等、比較的
重量級の推移的依存を伴う(このIssue実装時点のインストールで約20パッケージ追加)。この開発
セッションでは`pip install`でインターネット経由の導入可否のみ確認しており、実際のWindows
オフライン環境(社内ミラー等)での入手可否は未検証。導入時(M2-12の実運用投入時)に別途
確認が必要な既知のリスクとしてここに残す。
