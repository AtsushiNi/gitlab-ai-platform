# CLAUDE.md

このリポジトリで作業する Claude Code(このプロジェクト自体の開発を行うセッション)への指示。

> 注意: このプロジェクトが**作るもの**(社内GitLab向けAI開発基盤)の話ではなく、
> **このリポジトリ自体の開発**をどう進めるかのルール。前者は `references/タスク整理.md` を参照。

## このプロジェクトについて

社内GitLab × Claude Code/Bedrock を中心とした、MRレビュー自動化からIssue駆動開発までを
支えるAI開発基盤を作る。背景・要件は `references/AIとやりとりした履歴.md`、
タスク一覧は `references/タスク整理.md` を参照。

## Issue駆動で進める

- このリポジトリの開発自体は **GitHub Issue** で管理する(社内GitLab用のツールを作っているが、
  このツール自体の開発はGitHub)
- 作業は基本的に何らかのIssue番号に対応させる。コミットメッセージやPRには対応Issue番号を含める
- 次に着手すべきIssueは GitHub Projects「GitLab AI Platform」の **着手順** フィールドで確認する

## 並行セッション・worktreeの運用(重要)

複数セッションを並行させる場合、各セッションは**割り当てられたディレクトリ(worktree)から
絶対に動かない**こと。作業開始前に `pwd` と `git branch --show-current` を確認し、
想定のディレクトリ・ブランチであることを確かめてから着手する。

> 過去に、並行起動した3セッションがそれぞれの worktree ではなく元の `main` ディレクトリで
> 作業してしまい、意図せず `main` に直接コミットされた事故があった。多くの場合は
> 「新しいターミナルタブを開いたつもりが、実は同じシェルのまま `claude` を起動していた」
> といった環境側のミスなので、セッション開始直後に `pwd` で確認する癖をつけること。

## ブランチ・コミット規約

- Spike/調査用ブランチ: `spike/<issue番号>-<slug>` (例: `spike/25-claude-code-headless`)
- 実装用ブランチ: `feature/<issue番号>-<slug>` (例: `feature/29-gitlab-adapter-interface`)
- 調査・ドキュメントのみの変更は、レビュー負荷が低ければ `main` への直接コミットも許容する。
  実装コード(M1以降)は原則ブランチ→PR→レビューを経る
- コミットメッセージは「何を」より「なぜ」を書く。対応するIssue番号があれば触れる

## 禁止事項

- force push、`main` の履歴書き換え
- `--no-verify` などのフック・チェックのスキップ
- ユーザーの明示的な許可なしに実施する破壊的操作(ブランチ削除、`git reset --hard` 等)

## ドキュメントの置き場所

- `references/` — 一次資料(要件の会話ログ、Spikeの調査結果など)。整形せず、素朴な記録として残す
- `docs/` — まだ存在しない。D-1([#5](https://github.com/AtsushiNi/gitlab-ai-platform/issues/5))着手後に
  読み手別(作る人/使う人/動かす人/AI自身)の体系だったドキュメントをここに作る。
  それまでは `references/` に置く

このファイル自体も暫定版。D-5([#9](https://github.com/AtsushiNi/gitlab-ai-platform/issues/9))で
正式に育てる想定。
