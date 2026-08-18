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
| D  | ドキュメント | 体系だったドキュメント。人間とAIの両方が読める形で維持する | 進行中 (16/20) |
| S  | Spike | 先に潰すべき技術的不確実性の検証 | 進行中 (3/4) |
| M1 | レビュー自動化MVP | レビュー待ちMRを自動検出→AIレビュー→ローカル保存まで一気通貫 | 完了 (12/12) |
| M2 | 実運用強化 | 並列・再レビュー・人間のレビュー体験・GitLabへの選択投稿 | 進行中 (5/12) |
| M3 | AI Platform基盤化 | Job/Queue/Runner分離、Linux+Docker、PostgreSQL | 完了 (8/8) |
| M4 | Issue駆動開発 | Issue→要求分析→設計→実装→MR、WAITING_HUMAN による停止 | 完了 (11/11) |
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
| [#40](https://github.com/AtsushiNi/gitlab-ai-platform/issues/40) | M1-12 MVP のE2E動作確認 | 完了 |

## M2. 実運用強化

`references/タスク整理.md` には M2-1〜M2-12 の12タスクが記載されており、すべてIssue化済み。
M2-3〜M2-9 はIssueは存在するが未着手。

| Issue | タスク | 状態 |
|-------|--------|------|
| [#80](https://github.com/AtsushiNi/gitlab-ai-platform/issues/80) | M2-1 並列レビュー実行 | 完了 |
| [#81](https://github.com/AtsushiNi/gitlab-ai-platform/issues/81) | M2-2 再レビュー対応 | 完了 |
| [#82](https://github.com/AtsushiNi/gitlab-ai-platform/issues/82) | M2-3 レビュー結果の確認UX | 未着手 |
| [#83](https://github.com/AtsushiNi/gitlab-ai-platform/issues/83) | M2-4 追加調査モード | 未着手 |
| [#84](https://github.com/AtsushiNi/gitlab-ai-platform/issues/84) | M2-5 GitLabへの選択的コメント投稿(要否はM2着手時に判断・保留中) | 未着手 |
| [#85](https://github.com/AtsushiNi/gitlab-ai-platform/issues/85) | M2-6 指摘の重要度分類とフィルタ | 未着手 |
| [#86](https://github.com/AtsushiNi/gitlab-ai-platform/issues/86) | M2-7 レビュー品質の評価とプロンプト改善ループ | 未着手 |
| [#87](https://github.com/AtsushiNi/gitlab-ai-platform/issues/87) | M2-8 可観測性: 実行履歴とエラーの可視化 | 未着手 |
| [#88](https://github.com/AtsushiNi/gitlab-ai-platform/issues/88) | M2-9 レビュー完了通知 | 未着手 |
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

全8タスクをIssue化済み。並行実行のためのトラック分けは [ADR-0016](adr/0016-job-abstraction.md)
および [#91](https://github.com/AtsushiNi/gitlab-ai-platform/issues/91)(M3-1)のIssue本文を参照。

| Issue | タスク | 状態 |
|-------|--------|------|
| [#91](https://github.com/AtsushiNi/gitlab-ai-platform/issues/91) | M3-1 Job 抽象の導入 | 完了 |
| [#92](https://github.com/AtsushiNi/gitlab-ai-platform/issues/92) | M3-2 Job Queue の実装 | 完了 |
| [#93](https://github.com/AtsushiNi/gitlab-ai-platform/issues/93) | M3-3 Runner のプロセス分離 | 完了 |
| [#94](https://github.com/AtsushiNi/gitlab-ai-platform/issues/94) | M3-4 Linux/Docker 実行環境の構築 | 完了 |
| [#95](https://github.com/AtsushiNi/gitlab-ai-platform/issues/95) | M3-5 Store の PostgreSQL 対応 | 完了 |
| [#96](https://github.com/AtsushiNi/gitlab-ai-platform/issues/96) | M3-6 Webhook 受信対応(任意有効化) | 完了 |
| [#97](https://github.com/AtsushiNi/gitlab-ai-platform/issues/97) | M3-7 最小限の HTTP API / サーバ層 | 完了 |
| [#98](https://github.com/AtsushiNi/gitlab-ai-platform/issues/98) | M3-8 AI用GitLabアカウントとトークンスコープの設計 | 完了 |

## M4. Issue駆動開発

`references/タスク整理.md` のM4-1〜M4-10を、GitHub障害時にAIと検討した内容を踏まえて
再設計した11タスクとしてIssue化した。主な変更点:

- 「無人実行に向くタスクかどうか」をAIに判定させるのではなく、Issueへのラベル付与(人間の
  事前判断)で無人実行トラックへ振り分ける(M1-5 MR Pollerと同じパターンをM4-1として横展開)
- Issue取得(GitLab Adapter側)やIssue分解(対話型)は M2-10・M2-11・M2-12(GitLab Adapter
  MCP Server)で既に対応済みのため、M4側は無人実行パイプライン向けの薄い部分のみを扱う
- 対話が多く必要なタスクや、要件からIssueへの分解は、Windows VS Code拡張のClaude Code +
  GitLab Adapter MCP Server(M2-12)で対応する対話型トラックとし、M4(無人実行トラック)には
  含めない

| Issue | タスク | 状態 |
|-------|--------|------|
| [#107](https://github.com/AtsushiNi/gitlab-ai-platform/issues/107) | M4-1 Issue用ラベルポーリング(Issue Poller) | 完了 |
| [#108](https://github.com/AtsushiNi/gitlab-ai-platform/issues/108) | M4-2 Issue取得とRunnerプロンプトへの正規化 | 完了 |
| [#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109) | M4-3 要求分析フェーズ(Job種別 issue-analysis) | 完了 |
| [#110](https://github.com/AtsushiNi/gitlab-ai-platform/issues/110) | M4-4 質問する/仮定して進める判断ロジック | 完了 |
| [#111](https://github.com/AtsushiNi/gitlab-ai-platform/issues/111) | M4-5 人間への質問提示と回答の取り込み | 完了 |
| [#112](https://github.com/AtsushiNi/gitlab-ai-platform/issues/112) | M4-6 設計フェーズ(Job種別 design) | 完了 |
| [#113](https://github.com/AtsushiNi/gitlab-ai-platform/issues/113) | M4-7 実装計画の生成とタスク分解 | 完了 |
| [#114](https://github.com/AtsushiNi/gitlab-ai-platform/issues/114) | M4-8 実装フェーズ(Job種別 implement) | 完了 |
| [#115](https://github.com/AtsushiNi/gitlab-ai-platform/issues/115) | M4-9 push と MR 作成 | 完了 |
| [#116](https://github.com/AtsushiNi/gitlab-ai-platform/issues/116) | M4-10 Issue→MRパイプラインのオーケストレーション | 完了 |
| [#117](https://github.com/AtsushiNi/gitlab-ai-platform/issues/117) | M4-11 自己レビュー接続 | 完了 |

M4-8/M4-9の実装過程で、自動実行系トークンのスコープ設計(ADR-0019)が書き込み操作の追加により
前提から外れていることが判明し、フォローアップとして[#127](https://github.com/AtsushiNi/gitlab-ai-platform/issues/127)
(自動実行系GitLabトークンのスコープ再設計)を起票した(`references/タスク整理.md`には項目が
存在しない派生タスクのため、独立した行は起こさず注記のみとする)。対応は
ADR-0037で完了した(自動実行系アカウントを
Developerロール・`api`スコープへ引き上げ)。

## X. 横断

X-1・X-2 はいずれも未起票。`references/タスク整理.md` の「X. 横断」節を参照。
セキュリティレビュー(禁止操作が機構として不可能なことのテスト)とコスト・使用量の記録が含まれる。

## 関連ドキュメント

- [architecture.md](architecture.md) — アーキテクチャ概要(MVP → AI Platformへの成長パス)
- [adr/](adr/) — 設計判断の記録
- `references/タスク整理.md` — 各タスクの詳しい説明、クリティカルパス、ラベル案の一次資料
