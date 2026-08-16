# ロードマップ

- ステータス: 完了
- 対応Issue: [#15](https://github.com/AtsushiNi/gitlab-ai-platform/issues/15) (D-11)
- 一次資料: [references/タスク整理.md](../references/タスク整理.md)(マイルストーン構成・各タスクの
  詳しい説明はこちら。このファイルはその後継として、GitHub Issue番号・現在の状態と対応付けた表を提供する)

## このドキュメントについて

マイルストーン(M0〜M4、S、D、X)ごとに、`references/タスク整理.md` の各タスクと
GitHub Issue番号・状態(完了/未着手など)を対応付けた一覧。`references/タスク整理.md` 自体は
一次資料として改変せず残し(CLAUDE.md の方針)、このファイルはそこから昇格させた
「今どこまで進んでいるか」の正式な状態表という位置づけ。

**このファイルはスナップショットであり、最新性を保証しない。** 状態は更新のたびに古くなる。
今まさに次に何へ着手すべきかは、GitHub Projects「GitLab AI Platform」(プロジェクト番号1、
owner: `AtsushiNi`)の**着手順**フィールドを直接参照するのが正:

```sh
gh project item-list 1 --owner AtsushiNi --format json --limit 100
```

CLAUDE.mdの「次に着手すべきIssueはGitHub Projectsの着手順フィールドで確認する」という運用を
このファイルが代替するものではない。このファイルは着手順の細部までは追わず、
マイルストーン単位で「どこまで進んでいるか」の全体像を示すことを目的にする。

状態の表記:

- **完了** — GitHub Projects上のステータスが `Done`(Issueがクローズ済み)
- **未着手** — Issueは存在するがステータスが `Todo` など未完了
- **未起票** — `references/タスク整理.md` にタスクの記載はあるが、まだGitHub Issue化されていない

## マイルストーン一覧

| ID | 名前 | ゴール | 状態 |
|----|------|--------|------|
| M0 | 土台整備 | 開発を始められる状態(構成・設定・ログ・CI) | 完了 (4/4) |
| D  | ドキュメント | 体系だったドキュメント。人間とAIの両方が読める形で維持する | 進行中 (14/20) |
| S  | Spike | 先に潰すべき技術的不確実性の検証 | 進行中 (3/4) |
| M1 | レビュー自動化MVP | レビュー待ちMRを自動検出→AIレビュー→ローカル保存まで一気通貫 | 進行中 (11/12) |
| M2 | 実運用強化 | 並列・再レビュー・人間のレビュー体験・GitLabへの選択投稿 | 進行中 (起票済み3件中3件完了、9件未起票) |
| M3 | AI Platform基盤化 | Job/Queue/Runner分離、Linux+Docker、PostgreSQL | 未起票 (0/8) |
| M4 | Issue駆動開発 | Issue→要求分析→設計→実装→MR、WAITING_HUMAN による停止 | 未起票 (0/10) |
| X  | 横断 | セキュリティ・コスト・可観測性 | 未起票 (0/2) |

## M0. 土台整備

| Issue | タスク | 状態 |
|-------|--------|------|
| [#1](https://github.com/AtsushiNi/gitlab-ai-platform/issues/1) | M0-1 リポジトリ構成と依存方針を決める (ADR-0001) | 完了 |
| [#2](https://github.com/AtsushiNi/gitlab-ai-platform/issues/2) | M0-2 設定・シークレット管理の設計と実装 | 完了 |
| [#3](https://github.com/AtsushiNi/gitlab-ai-platform/issues/3) | M0-3 ログ/エラーハンドリング方針とロガー実装 | 完了 |
| [#4](https://github.com/AtsushiNi/gitlab-ai-platform/issues/4) | M0-4 CI (GitHub Actions) 整備 | 完了 |

## D. ドキュメント

### 基盤・作る人向け (D-1〜D-12)

| Issue | タスク | 状態 |
|-------|--------|------|
| [#5](https://github.com/AtsushiNi/gitlab-ai-platform/issues/5) | D-1 ドキュメント体系の定義と `docs/` の骨組み作成 | 完了 |
| [#6](https://github.com/AtsushiNi/gitlab-ai-platform/issues/6) | D-2 要件定義書の作成 (`docs/requirements.md`) | 完了 |
| [#7](https://github.com/AtsushiNi/gitlab-ai-platform/issues/7) | D-3 アーキテクチャドキュメント (`docs/architecture.md`) | 完了 |
| [#8](https://github.com/AtsushiNi/gitlab-ai-platform/issues/8) | D-4 ADR の運用開始とテンプレート整備 | 完了 |
| [#9](https://github.com/AtsushiNi/gitlab-ai-platform/issues/9) | D-5 CLAUDE.md の整備 | 完了 |
| [#10](https://github.com/AtsushiNi/gitlab-ai-platform/issues/10) | D-6 コンポーネント仕様書のフォーマット定義 (`docs/specs/`) | 完了 |
| [#11](https://github.com/AtsushiNi/gitlab-ai-platform/issues/11) | D-7 セットアップ手順書 (`docs/operations/setup-windows.md`) | 完了 |
| [#12](https://github.com/AtsushiNi/gitlab-ai-platform/issues/12) | D-8 設定リファレンス (`docs/operations/configuration.md`) | 完了 |
| [#13](https://github.com/AtsushiNi/gitlab-ai-platform/issues/13) | D-9 セキュリティドキュメント (`docs/operations/security.md`) | 完了 |
| [#14](https://github.com/AtsushiNi/gitlab-ai-platform/issues/14) | D-10 運用・トラブルシューティングガイド (`docs/operations/troubleshooting.md`) | 完了 |
| [#15](https://github.com/AtsushiNi/gitlab-ai-platform/issues/15) | D-11 ロードマップとIssue対応表の維持 (`docs/roadmap.md`、本ファイル) | 完了(本PRでクローズ) |
| [#16](https://github.com/AtsushiNi/gitlab-ai-platform/issues/16) | D-12 ドキュメントのCI | 未着手 |

### 利用者向け (D-13〜D-20)

| Issue | タスク | 状態 |
|-------|--------|------|
| [#17](https://github.com/AtsushiNi/gitlab-ai-platform/issues/17) | D-13 利用者向けガイドの入口 (`docs/guide/getting-started.md`) | 完了 |
| [#18](https://github.com/AtsushiNi/gitlab-ai-platform/issues/18) | D-14 レビュー運用ガイド (`docs/guide/review-workflow.md`) | 完了 |
| [#19](https://github.com/AtsushiNi/gitlab-ai-platform/issues/19) | D-15 レビュー結果の読み方 (`docs/guide/reading-results.md`) | 完了 |
| [#20](https://github.com/AtsushiNi/gitlab-ai-platform/issues/20) | D-16 CLIリファレンス (`docs/guide/cli-reference.md`) | 完了 |
| [#21](https://github.com/AtsushiNi/gitlab-ai-platform/issues/21) | D-17 AIレビューの限界と既知の弱点 (`docs/guide/limitations.md`) | 完了 |
| [#22](https://github.com/AtsushiNi/gitlab-ai-platform/issues/22) | D-18 AIが実装できるIssueの書き方ガイド (`docs/guide/writing-issues.md`)(M4) | 未着手 |
| [#23](https://github.com/AtsushiNi/gitlab-ai-platform/issues/23) | D-19 AI生成MRのレビューガイド (`docs/guide/ai-generated-mr.md`)(M4) | 未着手 |
| [#24](https://github.com/AtsushiNi/gitlab-ai-platform/issues/24) | D-20 社内展開ガイド | 未着手 |

## S. Spike(先に潰す不確実性)

| Issue | タスク | 状態 |
|-------|--------|------|
| [#25](https://github.com/AtsushiNi/gitlab-ai-platform/issues/25) | S-1 Claude Code のヘッドレス実行方式の検証 | 完了 |
| [#26](https://github.com/AtsushiNi/gitlab-ai-platform/issues/26) | S-2 社内GitLab REST API の疎通・仕様確認 | 完了 |
| [#27](https://github.com/AtsushiNi/gitlab-ai-platform/issues/27) | S-3 Windows での git worktree 運用検証 | 完了 |
| [#28](https://github.com/AtsushiNi/gitlab-ai-platform/issues/28) | S-4 Claude Code 並列実行時の挙動確認 | 未着手 |

## M1. レビュー自動化MVP

| Issue | タスク | 状態 |
|-------|--------|------|
| [#29](https://github.com/AtsushiNi/gitlab-ai-platform/issues/29) | M1-1 GitLab Adapter のインターフェース定義 | 完了 |
| [#30](https://github.com/AtsushiNi/gitlab-ai-platform/issues/30) | M1-2 GitLab Adapter: REST 実装 | 完了 |
| [#31](https://github.com/AtsushiNi/gitlab-ai-platform/issues/31) | M1-3 GitLab Adapter: 書き込み操作の許可リスト機構 | 完了 |
| [#32](https://github.com/AtsushiNi/gitlab-ai-platform/issues/32) | M1-4 State Store のスキーマ設計と SQLite 実装 | 完了 |
| [#33](https://github.com/AtsushiNi/gitlab-ai-platform/issues/33) | M1-5 MR Poller の実装 | 完了 |
| [#34](https://github.com/AtsushiNi/gitlab-ai-platform/issues/34) | M1-6 Workspace Manager の実装 | 完了 |
| [#35](https://github.com/AtsushiNi/gitlab-ai-platform/issues/35) | M1-7 Claude Code Runner の実装 | 完了 |
| [#36](https://github.com/AtsushiNi/gitlab-ai-platform/issues/36) | M1-8 レビュープロンプトの設計 | 完了 |
| [#37](https://github.com/AtsushiNi/gitlab-ai-platform/issues/37) | M1-9 レビュー結果スキーマと保存レイアウトの定義 | 完了 |
| [#38](https://github.com/AtsushiNi/gitlab-ai-platform/issues/38) | M1-10 CLI: 単発レビュー実行 | 完了 |
| [#39](https://github.com/AtsushiNi/gitlab-ai-platform/issues/39) | M1-11 CLI: 常駐(watch)モード | 完了 |
| [#40](https://github.com/AtsushiNi/gitlab-ai-platform/issues/40) | M1-12 MVP のE2E動作確認 | 未着手 |

## M2. 実運用強化

`references/タスク整理.md` には M2-1〜M2-12 の12タスクが記載されているが、Issue化されているのは
M2-10〜M2-12 のみ(M2-1〜M2-9 は未起票)。

| Issue | タスク | 状態 |
|-------|--------|------|
| — | M2-1 並列レビュー実行 | 未起票 |
| — | M2-2 再レビュー対応 | 未起票 |
| — | M2-3 レビュー結果の確認UX | 未起票 |
| — | M2-4 追加調査モード | 未起票 |
| — | M2-5 GitLabへの選択的コメント投稿(要否はM2着手時に判断・保留中) | 未起票 |
| — | M2-6 指摘の重要度分類とフィルタ | 未起票 |
| — | M2-7 レビュー品質の評価とプロンプト改善ループ | 未起票 |
| — | M2-8 可観測性: 実行履歴とエラーの可視化 | 未起票 |
| — | M2-9 レビュー完了通知 | 未起票 |
| [#47](https://github.com/AtsushiNi/gitlab-ai-platform/issues/47) | M2-10 GitLab Adapter: Issue/MR操作の拡充(取得・作成・更新) | 完了 |
| [#48](https://github.com/AtsushiNi/gitlab-ai-platform/issues/48) | M2-11 要件→Issue分解ワークフロー(CLI・対話型) | 完了 |
| [#62](https://github.com/AtsushiNi/gitlab-ai-platform/issues/62) | M2-12 Claude CodeからGitLab操作を呼び出すツールブリッジ | 完了 |

M2-12 は完了後にフォローアップIssueが複数派生している(いずれも完了・クローズ済み):
[#65](https://github.com/AtsushiNi/gitlab-ai-platform/issues/65)(Issue/MR操作ツールの追加)、
[#67](https://github.com/AtsushiNi/gitlab-ai-platform/issues/67)(GitLab Adapter MCP Serverへの改称)、
[#69](https://github.com/AtsushiNi/gitlab-ai-platform/issues/69)(起動時cwdからのproject自動解決)。
これらは `references/タスク整理.md` には項目として存在しない、実装中に生まれた派生タスクのため、
このファイルでも独立した行は起こさず注記のみとする。

## M3. AI Platform 基盤化

M3-1〜M3-8 はいずれも未起票。`references/タスク整理.md` の「M3. AI Platform 基盤化」節を参照。
Job抽象の導入、Job Queue、Runnerのプロセス分離、Linux/Docker実行環境、PostgreSQL対応、
Webhook受信対応、HTTP API/サーバ層、AI用GitLabアカウントとトークンスコープ設計が含まれる。

## M4. Issue駆動開発

M4-1〜M4-10 はいずれも未起票。`references/タスク整理.md` の「M4. Issue駆動開発」節を参照。
Issue取得・要求分析・質問判断・設計・実装計画・実装・push/MR作成・パイプライン統合・自己レビュー接続
が含まれる。

## X. 横断

X-1・X-2 はいずれも未起票。`references/タスク整理.md` の「X. 横断」節を参照。
セキュリティレビュー(禁止操作が機構として不可能なことのテスト)とコスト・使用量の記録が含まれる。

## 関連ドキュメント

- [architecture.md](architecture.md) — アーキテクチャ概要(MVP → AI Platformへの成長パス)
- [adr/](adr/) — 設計判断の記録
- `references/タスク整理.md` — 各タスクの詳しい説明、クリティカルパス、ラベル案の一次資料
