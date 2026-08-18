# ADR-0034: push と MR 作成フェーズの設計

- Issue: [#115](https://github.com/AtsushiNi/gitlab-ai-platform/issues/115) (M4-9)
- 状態: 決定

## 背景・制約

- M4-8(実装フェーズ)が、Issue単位のworktree上でClaude Codeにファイル編集・テスト実行・ローカルgit commitまでを行わせる仕組みを確立した。実際のGitLabへのpushは実装せず、本Issueがその最後のステップ(pushとMR作成)を担う
- `GitLabWriter.push_file_changes`はGitLab Commits API経由のファイル単位create/update/deleteであり、`git push`そのものではない。worktree内のローカルcommitを、変更ファイル一覧(`CommitAction`の配列)に変換する処理が新たに必要
- 判断すべき論点は4つ: (1) 差分のbase決定方法、(2) MR本文「設計要約」の情報源、(3) `JobType`新設の要否、(4) push成功後のworktree後片付け

## 決定

### 論点1: baseの決定は「pushフェーズが`git merge-base`を都度計算する」

`implement`のJob resultに新フィールドを追加するのではなく、pushフェーズが自分自身で`git merge-base <commit_sha> refs/remotes/origin/<default_branch>`を計算する(`push/git_ops.py`)。既にマージ済みの`implement`パッケージのスキーマ変更を避けられ、`ai/issue-<issue_iid>`branchはリモートへのマージ・rebaseを経ていないため計算結果は安定する。差分抽出は`git diff --no-renames --name-status`で取得し、renameは常にdelete+createの組として扱う(既存の`CommitActionType`の3値で表現可能)。

### 論点2: 「設計要約」は`implement`のJob payloadが引き継いでいる`plan_document`を使う

`design_document`を全フェーズに新たに引き回す変更はせず、`implement`のJob payload(`plan_document`)を「設計要約」として使う。3つの安定したパッケージすべてへの変更を避けるため。pushフェーズのJob payloadは、`implement`完了Jobの`payload`と`result`の**両方**を入力として組み立てる。

### 論点3: `JobType.PUSH = "push"`を新設する

「1フェーズ1JobType」パターンを踏襲する。pushフェーズは`Claude Code Runner`を呼び出さない(git diff計算とGitLab Adapter呼び出しのみ)点で他フェーズと性質が異なるが、Job抽象・再試行/デッドレター機構・パイプラインの一貫した構成はRunner呼び出しの有無に関わらず等しく当てはまる。`build_push_handler`は`runner`を引数に取らない初めてのhandlerであり、`WaitingForHumanError`も送出しない。

### 論点4: push成功後、`discard_for_issue`を呼んでworktreeを破棄する

`push_file_changes`・`create_merge_request`の両方が成功した後にのみ呼ぶ。いずれかが失敗した場合は呼ばない(Job再試行時に同じworktree・同じローカルcommitを再利用できるようにするため。`implement`が成功時に破棄しない設計と対称的な理由)。

## 却下した選択肢

- **`IssueWorktreeHandle.sha`を`implement`のJob resultに追加する**: 既にマージ済みのパッケージへの変更が必要になり影響範囲が大きい
- **`design_document`を全フェーズへ引き回す**: 3つの安定したパッケージへの変更が必要になり本Issueのスコープを大きく超える
- **独立した`JobType`を追加せず、`implement`のJobHandler内部でpush・MR作成まで行う**: Job Queueでの観測性が非対称になる。「実装の不明点」で`WAITING_HUMAN`になっている間、成功済みのcommitがpushされず宙に浮く期間が長引く
- **push失敗時も`discard_for_issue`を呼ぶ**: 再試行時にworktree・ローカルcommitを再利用できなくなり、実装からやり直すコストを払う理由がない
- **renameを`CommitActionType`に新しい値で表現する**: 本Issueのスコープ外。`--no-renames`で既存の3値のまま表現できる

## 影響

- `src/gitlab_ai_platform/push/`(新規)を追加。`JobType`に`PUSH`を追加
- `cli/dispatcher.py`に`build_push_handler`を追加(`runner`を使わない初めてのhandler)
- M4-10(パイプラインのオーケストレーション)実装時、`implement`完了Jobから`push`種別Jobを組み立てて投入する橋渡しコードが必要になる(本Issueでは投入者は実装しない)
- 自動実行系トークン([ADR-0019](0019-gitlab-token-scoping.md))が書き込みAPI(`push_file_changes`/`create_merge_request`)を呼ぶことになる。この前提の崩れはM4-8から既に生じており、トークン分離の再設計は今後の課題として残す
