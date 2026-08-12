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
- Issueの粒度・マイルストーン構成(M0〜M4、S、D、X)は `references/タスク整理.md` が一次資料

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

## テスト方針

- 実装コード(M1以降)は `pytest` を使う。`tests/` 配下に `src/` と対応する構成でミラーする
  (詳細は [docs/adr/0001-repository-structure.md](docs/adr/0001-repository-structure.md))
- 外部依存(GitLab API等)に触れるテストはモック/フィクスチャを使い、実サービスへは繋がない
- 挙動を変える変更にはテストを追加・更新する。テストなしでコード変更をコミットしない

## ドキュメント更新義務

このリポジトリ自体のドキュメント体系は [docs/README.md](docs/README.md) に読み手別
(作る人/使う人/動かす人/AI自身)の一覧がある。作業前にまずそこを見て、関連文書の有無を確認する。
更新ルールの要点(詳細は `docs/README.md` の「更新ルール」節):

1. **コードの挙動を変える変更は、対応する `docs/specs/` または `docs/operations/` の記述も
   同じPR/コミットで更新する。** 別Issueに先送りしない
   (`docs/specs/` はフォーマット定義済み[D-6/#10]。該当コンポーネントの仕様ファイルが
   まだ無ければ [docs/specs/template.md](docs/specs/template.md) を複製して新規作成する)
2. **設計判断は ADR([docs/adr/](docs/adr/))に残す。** 迷ったら書く
3. **`references/` は一次資料であり正式ドキュメントではない。** 設計判断や仕様として確定した
   内容は `docs/` 側に昇格させる(`references/` 側は改変せず記録として残す)
4. `docs/README.md` の一覧にない文書は「存在しないもの」として扱う。新規文書を追加したら
   まずそこに追記する

## 禁止事項

- force push、`main` の履歴書き換え
- `--no-verify` などのフック・チェックのスキップ
- ユーザーの明示的な許可なしに実施する破壊的操作(ブランチ削除、`git reset --hard` 等)

## ドキュメントの置き場所・参照ポインタ

- `references/` — 一次資料(要件の会話ログ、Spikeの調査結果など)。整形せず、素朴な記録として残す
- `docs/` — 読み手別(作る人/使う人/動かす人/AI自身)の正式ドキュメント。全体像は
  [docs/README.md](docs/README.md) を参照。特によく参照するもの:
  - [docs/requirements.md](docs/requirements.md) — 要件定義
  - [docs/architecture.md](docs/architecture.md) — アーキテクチャ概要
  - [docs/roadmap.md](docs/roadmap.md) — マイルストーンとIssue対応表
  - [docs/adr/](docs/adr/) — 設計判断の記録

このファイルは D-5([#9](https://github.com/AtsushiNi/gitlab-ai-platform/issues/9))で
正式版とした。以降もリポジトリの実態(ディレクトリ構成・規約)からずれたら随時更新する。
