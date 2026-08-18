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
- [operations/docker-runtime.md](operations/docker-runtime.md) — Linux/Docker実行環境セットアップ手順(M3-4)
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
- [specs/prompts.md](specs/prompts.md) — レビュープロンプト(M1-8)の仕様
- [specs/review-output.md](specs/review-output.md) — レビュー結果スキーマと保存レイアウト
  (M1-9)の仕様
- [specs/poller.md](specs/poller.md) — MR Poller(M1-5)の仕様
- [specs/cli.md](specs/cli.md) — CLI(M1-10)の仕様。単発レビュー実行(`review`サブコマンド)、
  常駐実行(`watch`サブコマンド)、要件→Issue分解の対話型実行(`decompose`サブコマンド、M2-11)、
  `WAITING_HUMAN`後の回答取り込み(`respond`サブコマンド、M4-5)
- [specs/adapter-mcp-server.md](specs/adapter-mcp-server.md) — GitLab Adapter MCP Server(M2-12)の仕様。
  対話型Claude CodeがGitLab Adapterの許可された操作をツールとして呼び出すための経路
- [specs/job-model.md](specs/job-model.md) — Job抽象・状態機械(M3-1)の仕様。既存レビュー処理を
  `review`種別のJobとして再構成する経路、取得の排他・可視性タイムアウト・リトライ・
  デッドレター(M3-2)を含む
- [specs/webhook-receiver.md](specs/webhook-receiver.md) — Webhook受信対応(M3-6、任意有効化)の
  仕様。MR Pollerと共存し、二重起票防止ロジック(`ticket_if_unprocessed`)を共有する
- [specs/http-api.md](specs/http-api.md) — 最小限のHTTP API(M3-7)の仕様。`JobRepository`への
  Job投入・状態/結果参照・一覧取得を提供する独立した`api`サブコマンド
- [specs/issue-poller.md](specs/issue-poller.md) — Issue Poller(M4-1)の仕様。無人実行ラベルの
  付いたIssueを検出し、専用のIssue Ticket Storeで二重投入を防ぎながら`issue-analysis`種別の
  Jobを投入する
- [specs/orchestrator.md](specs/orchestrator.md) — Orchestrator(M3-7, M4-1〜M4-6, M4-9〜M4-10)の仕様。
  「質問する / 仮定して進める」判断ロジック(M4-4)に加え、`issue-analysis → design → plan →
  implement → push`のフェーズ連鎖(M4-10、`advance_pipeline`)を実装済み
- [specs/issue-analysis.md](specs/issue-analysis.md) — 要求分析フェーズ(M4-3、Job種別
  `issue-analysis`)の仕様。Issueを分析し要求・受入条件・前提・不足情報を構造化して出力する。
  `WAITING_HUMAN`遷移(`job/protocol.py`の`wait_for_human`)・Runnerへの組み立て済みプロンプト
  実行(`run_prompt`)、`WAITING_HUMAN`後の回答取り込み・Job完了(M4-5、`respond`サブコマンド)を含む
- [specs/design-phase.md](specs/design-phase.md) — 設計フェーズ(M4-6、Job種別`design`)の仕様。
  要求分析フェーズの結果を元に、実装前の設計をレビュー可能な成果物(Markdown、D-6フォーマット)
  として出力する。無人実行トラック限定・worktreeを使わない設計(ADR-0029)、`WAITING_HUMAN`
  遷移・`respond`サブコマンド対応を含む
- [specs/plan-phase.md](specs/plan-phase.md) — 実装計画フェーズ(M4-7、Job種別`plan`)の仕様。
  設計フェーズの結果を元に、実装可能な粒度のタスクへ分解し実装順に並べた計画として出力する。
  無人実行トラック限定・worktreeを使わない設計(ADR-0030)、`WAITING_HUMAN`遷移・`respond`
  サブコマンド対応を含む
- [specs/implement-phase.md](specs/implement-phase.md) — 実装フェーズ(M4-8、Job種別
  `implement`)の仕様。実装計画フェーズの結果を元に、実際にファイルを編集しテストを実行し
  ローカルにcommitする。無人実行トラック限定・Issue単位の実際のworktreeを使う設計
  (ADR-0031)、Claude CodeへのEdit/Write/Bash権限付与(ADR-0033)、`WAITING_HUMAN`遷移・
  `respond`サブコマンド対応を含む。GitLabへの実際のpush/MR作成は含まない(M4-9)
- [specs/push-phase.md](specs/push-phase.md) — push と MR 作成フェーズ(M4-9、Job種別
  `push`)の仕様。実装フェーズがworktreeに残したローカルcommitをGitLab Commits API経由で
  実際にpushし、MRを作成する。このフェーズで初めてGitLabへの実際の書き込みが発生する。
  `ClaudeCodeRunner`を呼び出さない初めてのフェーズ(ADR-0034)、`git merge-base`によるdiffの
  base決定、MR本文テンプレート(「対応Issue」「設計要約」「○○と仮定して実装した」)、
  push成功後のworktree後片付け(`discard_for_issue`)を含む

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
