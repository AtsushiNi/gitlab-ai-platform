# ドキュメント地図

読み手別に4種類に分けている(背景は `references/タスク整理.md` の「D. ドキュメント」参照)。

| 読み手 | 誰 | 置き場所 |
|--------|-----|----------|
| 作る人 | このリポジトリの開発者 | `docs/` 直下・`adr/`・`specs/` |
| 使う人 | レビュアー / 実装者(MRを出す側) / Issue起票者 | `docs/guide/` |
| 動かす人 | 導入・設定・障害対応をする人 | `docs/operations/` |
| Claude Code 自身 | AI Runner | `CLAUDE.md`・`docs/specs/` |

## 一覧

### 作る人向け

- [requirements.md](requirements.md) — 要件定義
- [architecture.md](architecture.md) — アーキテクチャ概要
- [roadmap.md](roadmap.md) — マイルストーンとIssue対応表
- [adr/](adr/) — 設計判断の記録。新しい決定は `adr/template.md` を複製して書く

### 使う人向け (`guide/`)

- [guide/getting-started.md](guide/getting-started.md) — 何ができるか・何をしないか・最初の一歩
- [guide/review-workflow.md](guide/review-workflow.md) — 日々のレビュー運用フロー
- [guide/reading-results.md](guide/reading-results.md) — レビュー結果の読み方
- [guide/cli-reference.md](guide/cli-reference.md) — コマンド一覧
- [guide/limitations.md](guide/limitations.md) — AIレビューの限界・既知の弱点
- [guide/writing-issues.md](guide/writing-issues.md) — AIが実装できるIssueの書き方 (M4)
- [guide/ai-generated-mr.md](guide/ai-generated-mr.md) — AI生成MRのレビュー方法 (M4)
- [guide/faq.md](guide/faq.md) — FAQ

### 動かす人向け (`operations/`)

- [operations/setup-windows.md](operations/setup-windows.md) — Windowsセットアップ手順
- [operations/configuration.md](operations/configuration.md) — 設定リファレンス
- [operations/security.md](operations/security.md) — 許可/禁止操作、トークン管理
- [operations/troubleshooting.md](operations/troubleshooting.md) — トラブルシューティング

### AI自身向け

- `/CLAUDE.md`(リポジトリ直下) — このリポジトリ自体の開発規約
- [specs/template.md](specs/template.md) — コンポーネント仕様のフォーマット定義(D-6)。
  新しいコンポーネントの仕様を書くときはこれを複製する。1コンポーネント1ファイル、
  M4でAIが実装の根拠として読むことを想定した粒度で書く
- [specs/gitlab-adapter.md](specs/gitlab-adapter.md) — GitLab Adapter(M1-1)の仕様。
  最初の実例
- [specs/state-store.md](specs/state-store.md) — State Store(M1-4)の仕様
- [specs/workspace-manager.md](specs/workspace-manager.md) — Workspace Manager(M1-6)の仕様
- [specs/claude-code-runner.md](specs/claude-code-runner.md) — Claude Code Runner(M1-7)の仕様

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
