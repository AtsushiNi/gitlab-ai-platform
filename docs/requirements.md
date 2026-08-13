# 要件定義

- 対応Issue: [#6](https://github.com/AtsushiNi/gitlab-ai-platform/issues/6) (D-2)
- 一次資料: [references/AIとやりとりした履歴.md](../references/AIとやりとりした履歴.md)、[references/タスク整理.md](../references/タスク整理.md)

## 1. 背景

社内では GitLab を使って開発している。レビュー・実装のワークフローに Claude Code(Amazon Bedrock 経由)を組み込み、機械的な作業を減らしたいというニーズが繰り返し話題になった。その内容を一次資料(`references/AIとやりとりした履歴.md`)としてまとめた結果、単発の「AIレビューツール」ではなく、**社内GitLab × Claude Code/Bedrock を中心に、MRレビューから将来のIssue駆動開発までを支えるAI開発基盤**であると整理された。本書はその会話ログを仕様として参照できる形に構造化したものである。

このプロジェクト自体の開発方針(基本方針)は次の通り:

> 最初から巨大なAI開発プラットフォームを作るのではなく、今必要なMRレビュー自動化を作る。ただし、その部品が将来のIssue→設計→実装→MRというAI Platformへそのまま成長できる構造にする。

マイルストーン構成(M0〜M4、S、D、X)の詳細は `references/タスク整理.md` を参照。全体アーキテクチャの詳細設計は D-3([#7](https://github.com/AtsushiNi/gitlab-ai-platform/issues/7))で別途 `docs/architecture.md` にまとめる。本書はその前提となる要件のみを扱う。

## 2. 現状の課題

MRレビューにおいて、次のような機械的な作業が人間の手間になっている。

- 複数のGitLabプロジェクトを横断してレビュー対象を探す
- 同時に10件程度のMRを行き来する必要がある
- `レビュー待ち` ラベルが付いたMRを都度GitLab上で探す
- 対象ブランチを特定し、ローカルへcheckoutする
- Claude Codeにレビューを依頼し、応答を待つ
- VS Code / GitLab / Claude Code を行き来する

これらは「レビューの質を上げる作業」ではなく「レビューに辿り着くまでの機械的な作業」であり、ここを人間から極力取り除きたい。

一方で、**AIが見つけた指摘をそのまま全部GitLabへ投稿する**運用は望んでいない。AIの指摘には過検知・過剰なノイズが含まれる前提であり、最終判断は常に人間が行う。

## 3. ニーズ

### A. MRレビューの高速化(直近・最優先)

**目的**: 上記「現状の課題」にある機械的な作業をなくし、人間はAIの事前レビュー結果を確認・要否判断するところから始められるようにする。

理想のフロー:

```
GitLab
  ↓
レビュー待ちMR検出
  ↓
AIが事前レビュー
  ↓
レビュー結果をローカル保存
  ↓
人間がVS Codeでレビュー
  ↓
必要ならClaude Codeと追加相談
  ↓
人間がGitLabにコメント
  ↓
実装者が修正 → 再レビュー
```

AIレビューに求める性質:

- 単なる `git diff` の投げ込みではなく、対象ブランチをworktree等に展開し、**Claude Code自身にコードベースを探索させる**(MRタイトル・説明・コメント・diff・変更ファイル・周辺コード・既存実装・テストコードを必要に応じて参照)
- 重視する観点: 致命的なバグ / 仕様との不整合 / 既存コードとの不整合 / デグレ / テスト不足 / 将来問題になる設計 / 明らかな実装ミス
- 抑制する観点: 個人の好みレベルの指摘、大量のノイズ
- レビュー結果はGitLabへ自動投稿せず、まずローカルに保存する(実装者に無断で通知が飛ぶことを避ける)。VS CodeのGitLab拡張を人間の最終レビューUIとして活用する

実現に必要な性質(詳細はマイルストーンM1・M2、`references/タスク整理.md` 該当セクション参照):

- 同一MR・同一commit SHAの二重レビューを避ける状態管理
- 複数MRを並列にレビューできること(MR単位でworking treeを分離する)
- `レビュー待ち` ラベルの定期ポーリングによる検出(Webhookは当面不要、必要になれば後から追加)

### B. Issue駆動開発(将来)

**目的**: レビューだけでなく、Issueの起票から実装・MR作成までをAIに担わせる。

理想のフロー:

```
Issue
 ↓
AIが内容を理解
 ↓
必要なら設計
 ↓
実装計画
 ↓
branch作成 → 実装 → テスト → commit → push → MR作成
 ↓
AIレビュー → 人間レビュー
```

単純に「Issueの文章をそのままClaude Codeへ渡す」のではなく、次のオーケストレーションを想定する。

```
Issue → 要求分析 → 不足情報の検出 → 実装可能な粒度への具体化 → 設計 → 実装
```

特に重要なのは、**AIが分からないことを勝手に推測してしまう問題への対処**である。対話型のClaude Codeと異なり、自動処理では「分からない情報をAIが勝手に補完して進める」危険が大きい。そのため、Jobに次のような状態を持たせ、重要な不明点があれば `WAITING_HUMAN` として処理を止める仕組みを想定する。

```
PENDING → RUNNING → WAITING_HUMAN → DONE / FAILED
```

軽微な疑問については、処理を止めずに「○○と仮定して実装した」と明示した上でMRまで作成し、人間レビューの対象とする方法も候補とする。「質問して止めるか、仮定して進めるか」の判断ロジック自体がAI Platformの責務になる。

このニーズは M4(Issue駆動開発)で扱う。M1〜M3で作る部品(GitLab Adapter / Workspace / Runner / Store / Job)が、そのままM4の土台として使える境界で設計されていることが前提となる。

### C. 新規の開発要件をIssueへ分解する(Windows・対話型)

**目的**: 何か新しい開発要件が出てきたとき、それを個々のGitLab Issueへ分解・起票する作業をAIに担わせる。このリポジトリ自体の開発を `references/タスク整理.md` からGitHub Issueへ人手で分解してきたのと同じ作業を、対象プロジェクトに対してAI支援で行えるようにする。

Bとの違い: Bは「既にあるIssue」を起点に要求分析・設計・実装・MR作成まで進める、主に無人実行(M3以降Linux/Docker)のパイプラインである。Cはその前段、すなわち**Issueがまだ存在しない状態**から始まる。要件の大きさ・優先度・依存関係の切り方には人間の判断が本質的に必要であり、`docs/architecture.md`の設計原則(人間が介在する処理はWindows、無人で回すAI処理はLinux/Docker)に従い、**Windows端末上でCLIを介した対話型の機能**として提供する(M4のLinux/Docker無人トラックには含めない)。

このニーズを満たすには、GitLab AdapterがMR関連操作に加えてIssueの読み取り・作成にも対応している必要がある(現時点の許可リストにはIssue操作が一切存在しない)。

## 4. スコープと非スコープ

### スコープ(このプロジェクトが対象とするもの)

- 社内GitLabの `レビュー待ち` ラベル付きMRを検出し、AIによる事前レビューを行う(A)
- レビュー結果のローカル保存・人間による確認導線
- 複数プロジェクト・複数MRにまたがる並列レビュー
- 将来的な、Issueを起点とした要求分析・設計・実装・MR作成の自動化(B。M4で着手)
- 新規の開発要件をGitLab Issueへ分解・起票する対話型の支援(C。M2で着手)
- 上記を支えるGitLab操作の抽象化(Adapter)、Job/状態管理、実行環境(Runner)

### 非スコープ(このプロジェクトが対象としないもの・意図的にやらないこと)

- **AIによるGitLab MRコメントの自動投稿**。指摘は人間が確認・取捨選択してから投稿する(M2-5で選択的投稿を検討する余地はあるが、既定では自動投稿しない)
- **AIによるマージ操作**。`merge` はAdapter層で機構として禁止する(プロンプト上の約束事に依存しない)
- **protected branchへの直接push、branch削除、GitLabの管理操作**。これらもAdapter層で禁止する
- **現行GitLabバージョンでの公式GitLab MCPサーバーの利用**。現在のGitLabバージョンでは利用できない前提でアーキテクチャを組む(§6 制約を参照)。ただし将来MCPが利用可能になった際に差し替えられる抽象は用意する
- **`glab` CLIへの依存**。Windows環境に導入できないため使わない
- **Bedrock Converse API等を直接叩く独自LLM実装の大規模な作り込み**。Claude Codeが持つAgent能力・コード探索能力をそのまま利用する方針とし、Bedrock APIを直接叩く実装は極力書かない
- **初期段階からの巨大なJob Queue基盤・マルチテナント化**。M3(AI Platform基盤化)以降で必要になった時点で導入する

## 5. 非機能要件

### 並列性

- 複数プロジェクト・複数MRを同時にレビューできること。目安として同時10件程度のMRを扱う運用を想定する
- 並列実行時にworking treeを共有しない。**MR単位のgit worktree / workspace**を用意し、ブランチ切替の待ち時間を人間の作業から実質的に消す
- 将来的にRunnerをLinux/Docker上でプロセス分離し、Job Queue経由でスケールできる構造にする(M3)。ただしMVP段階で過剰な抽象化はしない

### 状態管理・冪等性

- 同一 `(project, MR IID, commit SHA)` の組み合わせを二重にレビューしない
- 新しいpushがあれば当該MRを再レビュー対象とする
- MVP段階ではSQLiteで十分とし、後にPostgreSQLへ移行可能な形に抽象化する(M3-5)

### 実行環境の分離

- **人間との対話・レビュー用途はWindows端末上で動作する**(レビュー自動化のPollerやRunnerを含む)
- **無人でのAI処理(Issue駆動開発など、M3以降の本格的な自動処理)はLinux/Docker上で動作する想定**とし、Windows端末に背負わせない
- 両環境で共通して動くコードは、Windows環境の制約(§6)を満たす形で書く

### 検出方式

- MVP段階ではGitLab Webhookを使わず、30〜60秒間隔のポーリングで `レビュー待ち` ラベル付きMRを検出する。理由: 社内GitLab側の設定変更が不要でシンプルなため。将来的に必要になればWebhook受信を追加で有効化できる形にする(M3-6)

### セキュリティ

- Claude Code(AI)に人間と同等の強いGitLab権限を渡さない
- GitLab Adapter層で、許可する操作(read / branch作成 / push / MR作成・コメント / Issue参照・作成)と禁止する操作(merge / protected branchへの直接push / branch削除 / 管理操作)を機構として分離する。プロンプト上の指示のみに依存しない(Issue参照・作成はC向けにM2で追加。既存の許可リストにはまだ含まれない)
- PAT(Personal Access Token)などのシークレットをログに出力しない

## 6. 制約

### GitLabのバージョン・API

- 社内GitLabの現行バージョンは、**公式GitLab MCPサーバーを利用できるバージョンではない**
- `glab` CLIは導入できない
- **REST APIのみ利用可能**。Personal Access Tokenによる認証を前提とする
- 将来GitLabがアップグレードされ、MCPが利用可能になる可能性はある。そのため、現在のREST API実装を前提にしつつも、将来MCP実装へ差し替え可能な抽象(Adapterインターフェース)を用意する
- GitLabの詳細なバージョン番号・API仕様(ラベル検索、diff取得、ページング、レート制限、PATに必要なスコープ等)はSpike S-2([#26](https://github.com/AtsushiNi/gitlab-ai-platform/issues/26))で検証し、結果をADRまたは`references/`に残す

### Windows環境(レビュー自動化ツール側)

現在のレビュー・開発環境は次の制約がある。

- Windows、管理者権限なし
- WSL不可、Docker Desktop不可
- 外部ダウンロード制限あり(パッケージ管理方針は [ADR-0001](adr/0001-repository-structure.md) を参照)
- Git Bashは利用可能
- Python、Claude Code、VS Code、GitLab VS Code拡張は利用可能
- `glab` は利用不可

このため、直近のレビュー自動化(M1・M2)は「Windows上で動く、依存の少ないPythonツール」として実現する。

### LLM実行経路

- 利用するLLMはClaude Code、実行経路は **Amazon Bedrock経由**で固定する
- `Python → Bedrock Converse APIを直接叩く独自実装` は大量には作らず、**Claude Codeが持つAgent能力・コード探索能力を活用する**方針を維持する
- Claude Codeのヘッドレス実行方式(起動方法、構造化出力の可否、権限設定、タイムアウト、Bedrock認証の引き回し、Windows/Git Bashでの起動可否)はSpike S-1([#25](https://github.com/AtsushiNi/gitlab-ai-platform/issues/25))で検証する

### 実装言語

- Python(3.11以上)で確定。詳細は [ADR-0001](adr/0001-repository-structure.md) を参照

## 7. 用語定義

| 用語 | 定義 |
|------|------|
| MR (Merge Request) | GitLabにおけるプルリクエスト相当。本文書では「MR」と表記する |
| `レビュー待ち` ラベル | AIレビューの対象としてMR Pollerが検出するGitLabラベル。ラベル名は設定可能とする想定 |
| Review Tool / レビュー自動化ツール | Windows上で動作する、MR検出〜AIレビュー〜結果保存までを行うM1〜M2のコンポーネント群 |
| AI Platform | Issue駆動開発(M3〜M4)を含む、より広い自動化基盤としての将来像。Review Toolはこの一部として成長する |
| GitLab Adapter | GitLab操作(read/write)を抽象化するコンポーネント。REST実装とMCP実装を差し替え可能にする層。書き込み操作の許可リストを機構として持つ |
| Workspace Manager | プロジェクトのbare cloneとMR単位のgit worktreeを管理するコンポーネント |
| Claude Code Runner | worktree上でClaude Codeをヘッドレス実行し、レビューやIssue処理を行わせるコンポーネント |
| MR Poller | 対象プロジェクトを定期走査し、`レビュー待ち` ラベル付きMRの未処理commitを検出してJobを起票するコンポーネント |
| State Store | MR・commit・レビュー状態を保持し、二重レビューを防ぐための永続化層。MVPはSQLite |
| Job | レビューやIssue処理などの処理単位。M3以降 `PENDING` / `RUNNING` / `WAITING_HUMAN` / `DONE` / `FAILED` の状態機械として扱う |
| `WAITING_HUMAN` | Jobの状態の一つ。AIが重要な不明点を検出し、人間の回答を待つために処理を一時停止している状態 |
| PAT (Personal Access Token) | GitLab REST APIの認証に用いるトークン。ログへの出力を禁止する |

## 8. 関連ドキュメント

- 全体アーキテクチャ・成長パス: `docs/architecture.md`(D-3、[#7](https://github.com/AtsushiNi/gitlab-ai-platform/issues/7)、未着手)
- 設計判断の記録: `docs/adr/`(D-4、[#8](https://github.com/AtsushiNi/gitlab-ai-platform/issues/8))
- マイルストーンとIssue対応表: `docs/roadmap.md`(D-11、[#15](https://github.com/AtsushiNi/gitlab-ai-platform/issues/15)、未着手)
- 一次資料(会話ログ、整形せず凍結): [references/AIとやりとりした履歴.md](../references/AIとやりとりした履歴.md)、[references/タスク整理.md](../references/タスク整理.md)
