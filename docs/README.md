# ドキュメント地図

読み手別に4種類に分けている(背景は `references/タスク整理.md` の「D. ドキュメント」参照)。

| 読み手 | 誰 | 置き場所 |
|--------|-----|----------|
| 作る人(Contributor Guide) | このリポジトリにコードを書く開発者 | `docs/` 直下・`adr/`・`specs/` |
| 使う人(User Guide) | MRを出す/レビューする開発者、Issue起票者(基盤の機能を使う側) | `docs/guide/` |
| 動かす人(Administrator Guide) | 基盤を導入・設定・保守する管理者(基盤を動かし続ける側) | `docs/operations/` |
| Claude Code 自身 | AI Runner | `CLAUDE.md`・`docs/specs/` |

## 一覧

### 作る人向け

- [requirements.md](requirements.md) — 要件定義
- [architecture.md](architecture.md) — アーキテクチャ概要
- [roadmap.md](roadmap.md) — マイルストーンとIssue対応表
- [adr/](adr/) — 設計判断の記録。新しい決定は `adr/template.md` を複製して書く

### 使う人向け — User Guide (`guide/`)

- [guide/getting-started.md](guide/getting-started.md) — 何ができるか・何をしないか・最初の一歩
- [guide/review-workflow.md](guide/review-workflow.md) — 日々のレビュー運用フロー
- [guide/reading-results.md](guide/reading-results.md) — レビュー結果の読み方
- [guide/cli-reference.md](guide/cli-reference.md) — コマンド一覧
- [guide/limitations.md](guide/limitations.md) — AIレビューの限界・既知の弱点
- [guide/writing-issues.md](guide/writing-issues.md) — AIが実装できるIssueの書き方 (M4)
- [guide/ai-generated-mr.md](guide/ai-generated-mr.md) — AI生成MRのレビュー方法 (M4)
- [guide/faq.md](guide/faq.md) — FAQ

### 動かす人向け — Administrator Guide (`operations/`)

- [operations/setup-windows.md](operations/setup-windows.md) — Windowsセットアップ手順
- [operations/docker-runtime.md](operations/docker-runtime.md) — Linux/Docker実行環境セットアップ手順(M3-4)
- [operations/configuration.md](operations/configuration.md) — 設定リファレンス
- [operations/security.md](operations/security.md) — 許可/禁止操作、トークン管理
- [operations/troubleshooting.md](operations/troubleshooting.md) — トラブルシューティング

### AI自身向け

- `/CLAUDE.md`(リポジトリ直下) — このリポジトリ自体の開発規約
- [specs/template.md](specs/template.md) — コンポーネント仕様のフォーマット定義。
  新しいコンポーネントの仕様はこれを複製して書く(1コンポーネント1ファイル)
- [specs/gitlab-adapter.md](specs/gitlab-adapter.md) — GitLab Adapter(M1-1)の仕様
- [specs/state-store.md](specs/state-store.md) — State Store(M1-4)の仕様
- [specs/workspace-manager.md](specs/workspace-manager.md) — Workspace Manager(M1-6)の仕様
- [specs/claude-code-runner.md](specs/claude-code-runner.md) — Claude Code Runner(M1-7)の仕様
- [specs/prompts.md](specs/prompts.md) — レビュープロンプト(M1-8)の仕様
- [specs/review-output.md](specs/review-output.md) — レビュー結果スキーマと保存レイアウト(M1-9)の仕様
- [specs/poller.md](specs/poller.md) — MR Poller(M1-5)の仕様
- [specs/cli.md](specs/cli.md) — CLI(M1-10)の仕様
- [specs/adapter-mcp-server.md](specs/adapter-mcp-server.md) — GitLab Adapter MCP Server(M2-12)の仕様
- [specs/job-model.md](specs/job-model.md) — Job抽象・状態機械(M3-1/M3-2)の仕様
- [specs/webhook-receiver.md](specs/webhook-receiver.md) — Webhook受信対応(M3-6)の仕様
- [specs/http-api.md](specs/http-api.md) — 最小限のHTTP API(M3-7)の仕様
- [specs/issue-poller.md](specs/issue-poller.md) — Issue Poller(M4-1)の仕様
- [specs/orchestrator.md](specs/orchestrator.md) — Orchestrator(M3-7, M4-1〜M4-11)の仕様
- [specs/issue-analysis.md](specs/issue-analysis.md) — 要求分析フェーズ(M4-3)の仕様
- [specs/design-phase.md](specs/design-phase.md) — 設計フェーズ(M4-6)の仕様
- [specs/plan-phase.md](specs/plan-phase.md) — 実装計画フェーズ(M4-7)の仕様
- [specs/implement-phase.md](specs/implement-phase.md) — 実装フェーズ(M4-8)の仕様
- [specs/push-phase.md](specs/push-phase.md) — pushとMR作成フェーズ(M4-9)の仕様

## 更新ルール

ドキュメントは書いた時点で陳腐化が始まる。以下を守ることで最小限の鮮度を保つ。

1. **コードの挙動を変える変更は、対応する `specs/` または `operations/` の記述も同じPR/コミットで
   更新する。** 別Issueに先送りしない。
2. **設計判断(選択肢が複数あった/後から理由が分からなくなりそうなもの)は ADR に残す。**
   ADRを書くべきかどうか迷ったら書く。
3. **`references/` は一次資料であり、正式ドキュメントではない。** 会話ログやSpikeの調査結果は
   `references/` に置いたままでよいが、そこから設計判断や仕様として確定した内容は `docs/` 側に
   昇格させる(内容をコピーしてよい。`references/` 側は改変せず記録として残す)。
4. **このファイル(`docs/README.md`)にない文書は「存在しないもの」として扱う。** 新しい文書を
   追加したら、まずこの一覧に追記する。
5. 上記の「ステータス: 未着手」の文書は、対応するIssueに着手するタイミングで内容を書く
   (先回りして全部書こうとしない)。
