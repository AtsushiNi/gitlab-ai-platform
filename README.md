# gitlab-ai-platform

社内GitLab × Claude Code(Amazon Bedrock経由)を中心に、**MRレビュー自動化**から**Issue駆動開発**
までを支えるAI開発基盤。

## これは何をするツールか

GitLab上の「レビュー待ち」MRを検出し、Claude Codeにヘッドレスでレビューさせ、結果をローカルに
保存する。人間はAIの事前レビュー結果を確認するところから始められ、「対象ブランチをローカルに
checkoutする」「Claude Codeに手動でレビューを依頼する」といった機械的な作業から解放される。

- AIの指摘を勝手にGitLabへ投稿することはない。マージもしない。最終判断は常に人間
- 発展形として、ラベル付きIssueを検出し「要求分析→設計→実装計画→実装→push→自己レビュー」まで
  無人で進めるパイプラインも実装済み(人間の判断が要る場面では停止して質問する)

詳しい背景・要件は [docs/requirements.md](docs/requirements.md) を参照。

## 全体像・使用フロー

コンポーネント構成と実際のデータフローは [docs/architecture.md](docs/architecture.md) の
「全体図(現在の構成)」にまとめている。ざっくり言うと:

1. Windows側: 人間の端末上でMRレビューを検出・実行し、結果を人間がVS Codeで確認する
2. Linux/Docker側: Issue駆動の無人実行パイプライン(Job Queue + Orchestrator + Runner群)

このプロジェクトの現在地(どのマイルストーンが完了しているか)は
[docs/roadmap.md](docs/roadmap.md) を参照。

## 導入するには

MRレビュー自動化を使い始めたいだけなら [docs/guide/getting-started.md](docs/guide/getting-started.md)、
Windows環境のセットアップ手順は [docs/operations/setup-windows.md](docs/operations/setup-windows.md)
を参照。

## もっと詳しく知りたい人へ

読み手別のドキュメント一覧は [docs/README.md](docs/README.md) にまとめてある。

| 知りたいこと | 読むもの |
|---|---|
| このツールを使ってMRをレビューしてもらいたい | [docs/guide/](docs/guide/) |
| 自分の環境に導入・設定したい | [docs/operations/](docs/operations/) |
| このリポジトリ自体の開発に参加したい | [CLAUDE.md](CLAUDE.md)・[docs/architecture.md](docs/architecture.md)・[docs/adr/](docs/adr/) |
