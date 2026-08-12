これまでの複数スレッドを統合すると、話していたのは単なる「AIレビュー」ではなく、**社内GitLab × Claude Code/Bedrockを中心に、レビューから将来のIssue駆動開発までを支えるAI開発基盤**だと整理できます。

## 1. まず大きなニーズ

大きく3段階あります。

### A. 直近：MRレビューを高速化したい

現状の一番強い課題。

* 複数GitLabプロジェクトを横断してレビューする
* 同時に10件程度のMRを行き来することがある
* `レビュー待ち` ラベルが付いたMRがレビュー対象
* GitLabでMRを探す
* 対象ブランチを特定する
* checkoutする
* Claude Codeにレビューを依頼する
* Claude Codeの応答を待つ
* VS Code / GitLab / Claude Codeを行き来する

この**人間がやっている機械的な部分を極力なくしたい**。

ただし、

> AIが見つけた指摘を、そのまま全部GitLabへ投稿する

のは望んでいない。

理想は、

**AIが裏でレビュー → 人間がAI結果を確認・追加調査 → 本当に必要なものだけ人間がGitLabにコメント**

です。

---

## 2. AIレビューに求めていること

単なるdiffレビューでは足りません。

AIには、

* MRタイトル・説明
* MRコメント
* diff
* 変更されたファイル
* 周辺コード
* 既存実装
* テストコード

など、**リポジトリ全体を必要に応じて探索してレビューしてほしい**。

つまり、

> `git diff` をLLM APIへ投げるだけ

ではなく、

> **対象ブランチをworktree等に展開してClaude Code自身にコードベースを探索させる**

方向が合っています。

特にレビューでは、

* 致命的なバグ
* 仕様との不整合
* 既存コードとの不整合
* デグレ
* テスト不足
* 将来問題になる設計
* 明らかな実装ミス

を重視する。

細かい好みや大量のノイズをAIにGitLabへ書かせることは避けたい。

---

## 3. レビュー時の人間の役割

AIだけでレビューを完結させるつもりではありません。

イメージは、

```text
GitLab
  ↓
レビュー待ちMR検出
  ↓
AIが事前レビュー
  ↓
レビュー結果をローカル保存
  ↓
人間がVS CodeでMRをレビュー
  ↓
必要ならClaude Codeと追加相談
  ↓
人間がGitLabにコメント
  ↓
実装者が修正
  ↓
再レビュー
```

です。

VS CodeのGitLab拡張はかなり便利なので、**人間の最終レビューUIとして活用したい**。

一方で、AIレビュー結果をGitLab MRコメントへ自動投稿すると、実装者にも通知されてしまうため、基本的には避けたい。

---

# 4. Issue駆動でAIに設計・実装させたい

レビューだけが最終目的ではありません。

```text
Issue
 ↓
AIが内容を理解
 ↓
必要なら設計
 ↓
実装計画
 ↓
branch作成
 ↓
実装
 ↓
テスト
 ↓
commit
 ↓
push
 ↓
MR作成
 ↓
AIレビュー
 ↓
人間レビュー
```

まで持っていきたい。

しかも今回の新規システムでは、単純なコーディングだけではなく、**設計自体もAIにかなり担当させたい**。

そのため、「Issueの文章をそのままClaude Codeへ渡す」だけではなく、IssueをAIが分析し、

```text
Issue
 ↓
要求分析
 ↓
不足情報の検出
 ↓
実装可能な粒度への具体化
 ↓
設計
 ↓
実装
```

というオーケストレーションが必要になる想定です。

---

# 5. AIが分からないことを勝手に推測する問題

ここも重要なニーズ。

Issueを自動処理すると、対話型Claude Codeよりも

> 分からない情報をAIが勝手に補完して進める

危険があります。

そのため将来的にはJobに例えば、

```text
PENDING
RUNNING
WAITING_HUMAN
DONE
FAILED
```

のような状態を持たせ、

**重要な不明点があれば `WAITING_HUMAN` にして止める**

仕組みを検討していました。

一方、軽微な疑問ならMRまで作り、

> 「ここは○○と仮定して実装した」

とレビュー対象にする方法も候補です。

つまり「質問するか進めるか」の判断もAI Platform側の責務になってきます。

---

# 6. GitLabについての前提

社内GitLabを利用。

重要な制約として、

* 現在のGitLabは公式MCPを利用できるバージョンではない
* `glab` も導入できない
* REST APIは利用可能
* Personal Access Tokenによる認証を想定
* 将来的にGitLabがアップグレードされ、MCPが利用可能になる可能性はある

があります。

したがって、

**現在のGitLab MCPを前提にアーキテクチャを作ってはいけない。**

ただし将来MCPへ交換できる構造にはしたい。

---

# 8. Claude Code / LLMについての前提

普段使っているのはClaude Code。

そして、

**Amazon Bedrock経由でClaude Codeを利用している。**

したがって基本的には、

```text
Claude Code
   ↓
Amazon Bedrock
```

を維持したい。

独自に、

```text
Python
 ↓
Bedrock Converse API
 ↓
Claude
```

を大量実装するより、**Claude Codeが持っているAgent能力・コード探索能力を利用したい**。

---

# 9. Windows環境の制約

現在の開発・レビュー環境にはかなり制約があります。

* Windows
* 管理者権限なし
* WSL不可
* Docker Desktop不可
* 外部ダウンロード制限あり
* Git Bashあり
* Pythonは利用可能
* Claude Code利用可能
* VS Code利用可能
* GitLab VS Code拡張利用可能
* `glab` は利用不可

そのため、直近のレビュー自動化については、

**Windows上で動くPython等の小さなツール**

が現実的。

例えば、

```text
Windows

review-watcher
 ├─ GitLab REST API
 ├─ MR Poller
 ├─ git worktree管理
 ├─ Claude Code Runner
 └─ review結果保存
```

という構成です。

---

# 10. 一方、将来の自動AI基盤はLinux/Docker

Issue→実装のような本格的な自動処理までWindows端末に背負わせる必要はありません。

将来的には、

```text
GitLab
   ↓
Webhook / Poller
   ↓
AI Platform
   ↓
Job Queue
   ↓
AI Runner
   ↓
Claude Code
   ↓
Bedrock
```

という構成を想定。

AI RunnerはLinux/Docker。

レビュー、Issue分析、実装などをJobとして処理します。

つまり、

**Windows = 人間との対話・レビュー**

**Linux/Docker = 無人AI処理**

という分担が自然、というところまで話していました。

---

# 11. Webhookに固執しない

当初は、

```text
GitLab Webhook
 → Docker
 → Claude Code
```

も検討しました。

ただ、現在のレビュー用途では

> `レビュー待ち` ラベル付きMRを定期走査

で十分。

例えば、

```text
30～60秒ごと

GitLab REST API
 ↓
レビュー待ちMR検索
 ↓
未処理commit SHAか確認
 ↓
レビューJob開始
```

です。

この方が社内GitLab側の設定変更も少なく、MVPとしてシンプル。

将来的に必要ならWebhookへ変えればよい。

---

# 12. 多重実行・状態管理が必要

MRレビューでは同じMRを何度もレビューしないように、

```text
project
MR IID
commit SHA
review status
reviewed_at
```

などを保存する。

例えば、

```text
MR !123
abc123 → DONE

新しいpush

MR !123
def456 → PENDING
```

なら再レビュー。

最初はSQLiteでも十分で、本格的なAI PlatformになったらPostgreSQL等へ移行可能。

---

# 13. 並列実行したい

複数プロジェクト・多数MRを扱うため、

```text
MR A ─ Claude Code
MR B ─ Claude Code
MR C ─ Claude Code
MR D ─ Claude Code
```

のように並列化したい。

ただし同じworking treeを共有すると危険なので、

**MR単位のgit worktree / workspace**

を用意する方向。

これによってブランチ切替時間もほぼ人間のレビュー作業から消せます。

---

# 14. セキュリティ上の重要な制約

AIにはGitLab操作をさせたい一方で、**勝手なマージなどは絶対にさせたくない**。

Claude Codeへ人間と同じ強いGitLab権限をそのまま与えるのは避けたい。

将来的には、

```text
AI
 ↓
許可されたGitLab操作
 ↓
GitLab Adapter
```

として、

* read
* branch作成
* push
* MR作成
* コメント

など必要な操作だけ許可し、

* merge
* protected branchへの直接push
* branch削除
* 管理操作

などを制限する考え方が重要です。

---

# 15. 最重要の設計方針

ここまでの話を一言でまとめると、

> **最初から巨大なAI開発プラットフォームを作るのではなく、今必要なMRレビュー自動化を作る。ただし、その部品が将来のIssue→設計→実装→MRというAI Platformへそのまま成長できる構造にする。**

です。

なので現時点の全体像はこう整理するのがかなりきれいです。

```text
              ┌──────────────┐
              │    GitLab    │
              └──────┬───────┘
                     │
              GitLab REST API
                     │
            ┌────────▼────────┐
            │ GitLab Adapter  │
            └────────┬────────┘
                     │
       ┌─────────────┴─────────────┐
       │                           │
┌──────▼──────┐             ┌──────▼──────┐
│ Review Tool │             │ AI Platform │
│  Windows    │             │ Linux       │
└──────┬──────┘             └──────┬──────┘
       │                           │
       │                     Job / Runner
       │                           │
       └──────────┬────────────────┘
                  ▼
             Claude Code
                  │
                  ▼
            Amazon Bedrock
```
