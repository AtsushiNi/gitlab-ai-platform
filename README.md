# gitlab-ai-platform

社内GitLab × Claude Code(Amazon Bedrock経由)を中心に、**MRレビュー自動化**から**Issue駆動開発**
までを支えるAI開発基盤。

## これは何をするツールか

1. **コードレビューの一次対応をAIに任せられる**
   MRを出したら、ローカルにcheckoutしたりClaude Codeに手動で頼んだりしなくても、AIが先に
   レビューしてくれる。人間は指摘を確認するところから始められる
2. **Issueを書くだけでAIが実装まで進めてくれる**
   要件をIssueに書くと、AIが分析→設計→実装計画→実装→pushまで自律的に進める。本当に判断が
   必要な場面だけ人間に聞く
3. **AI自身がGitLabを操作できる**
   Claude CodeはGitLab上のIssueやMRを自分で調べたり、ブランチ作成・コメント投稿を自分の
   判断で実行したりできる。人間がAIの提案を代わりにGitLab上で操作する必要がない

ただしAIの指摘を勝手に投稿したり、勝手にマージしたりはしない。最終判断は常に人間が行う。

詳しい背景・要件は [docs/requirements.md](docs/requirements.md) を参照。

## 全体像・使用フロー

コンポーネント構成と実際のデータフローは [docs/architecture.md](docs/architecture.md) の
「全体図(現在の構成)」にまとめている。ざっくり言うと:

1. Windows側: MRの検出・レビューをバックグラウンドで行う。人間はAIの処理を待たずに済み、
   結果ができた頃に確認してレビューを始めれば良い
2. Linux/Docker側: コンテナ上のアプリが対応必要なIssueを取ってきて、設計→実装→レビューを進め、
   MRの作成まで自動で行う
3. Windows上のClaude Code(VS Code拡張)からGitLabのIssueや
   MRを自分で調べたり操作したりできるMCPサーバーを提供する。

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
