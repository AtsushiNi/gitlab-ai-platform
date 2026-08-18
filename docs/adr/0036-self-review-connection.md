# ADR-0036: 自己レビュー接続

- Issue: [#117](https://github.com/AtsushiNi/gitlab-ai-platform/issues/117) (M4-11)
- 状態: 決定

## 背景・制約

- M4-10(Issue→MRパイプラインのオーケストレーション、[#116](https://github.com/AtsushiNi/gitlab-ai-platform/issues/116)、
  [ADR-0035](0035-pipeline-orchestration.md))で、`issue-analysis → design → plan → implement →
  push`という5フェーズの連鎖を`orchestrator.pipeline.advance_pipeline`で実現した。`push`完了後は
  次フェーズが無く、`_NEXT_JOB_TYPE`に対応が無いため`None`を返すだけだった
- Issue #117本文は「無人実行で作られたMRを、そのままレビューJob(M1で完成済み)に流し、
  人間レビュー前の一次チェックとする」がスコープで、「既存のレビューパイプライン(`review`
  Job種別)を再利用するだけで、新規実装は接続部分のみ」と明記している
- Issue #117本文には「implement完了後」という記述があるが、一次資料
  (`references/タスク整理.md`)のフェーズ名がM4-10で再編される前の古い呼び方を引きずっている
  可能性を疑い、実装前に`push/job.py`・`review/job.py`を実際に読んで検証した(下記「論点1」)
- `review`Jobの`payload`は`build_review_job_payload(project, mr_iid, *, sha=None)`
  (`review/job.py`、M1・M3-1で確立済み、変更なし)で組み立てる。MR IIDを引数に要求する

## 決定

### 論点1: 接続点は「push完了後」であり「implement完了後」ではない

`_NEXT_JOB_TYPE`に`JobType.PUSH: JobType.REVIEW`を追加し、`push`完了後に`review`Jobを
自動投入する設計にした。Issue本文の「implement完了後」という記述はそのまま採用しなかった。

理由:

- `review`Jobの投入に必須の`mr_iid`(`build_review_job_payload`の必須引数)は、GitLab上に
  実際にMRが作成されて初めて存在する値。MRを作成するのは`push`フェーズ
  (`push/job.py`の`build_push_job_result`が`GitLabWriter.create_merge_request`の戻り値
  `MergeRequest`から`merge_request_iid`を組み立てて`result`に含める)であり、`implement`
  フェーズはIssue単位のworktree上でローカルcommitを作るだけで、GitLab上には何も書き込まない
  (ADR-0033「実際のGitLabへのpushをどこにも実装しない」)
- したがって`implement`完了時点では`mr_iid`が存在せず、`implement`完了後に`review`Jobを
  組み立てることは構造的に不可能。「push完了後」が唯一実現可能な接続点
- Issue本文の「implement完了後」は、一次資料(`references/タスク整理.md`)のM4-10フェーズ
  再編前の古いフェーズ名(pushとMR作成がimplementに含まれていた頃の呼び方)を引きずった
  記述だと判断した。一次資料自体は改変せず、この判断はADR側に確定事項として残す
  (`CLAUDE.md`「`references/`は一次資料であり正式ドキュメントではない」)

`review`Jobの`sha`には、`push`完了時の`result`が持つ`pushed_commit_sha`
(GitLab上に新しく作成されたcommitのsha。worktree内のローカルcommit shaとは別物、
`push/job.py`のdocstring参照)を明示的に渡す。`sha`省略時は`execute_review`が
「MR取得時点の最新commit」を使う設計(`review/job.py`)だが、無人実行トラックでは
push完了時点でどのcommitをレビュー対象にすべきかが確定しているため、省略せず明示する。

### 論点2: 無人実行由来の`review`Jobと人間が手動投入する`review`Jobを区別しない

`build_review_job_payload(project, mr_iid, *, sha=None)`のシグネチャに変更を加えず、
投入元(無人実行パイプライン経由か、人間がCLI/API経由で手動投入したか)を区別する仕組みは
追加しなかった。

理由:

- `review`Jobの実行内容(`execute_review`: GitLab Adapter→Workspace Manager→Claude Code
  Runner→Review→State Store)は、投入元によって処理を分岐させる必要が無い。対象の
  `(project, mr_iid, sha)`が同じであれば、無人実行由来でも人間の手動投入でも「そのMRの
  そのcommitをレビューする」という処理内容は完全に同一
- State Store(`(project, mr_iid, commit_sha)`単位での二重レビュー防止)が既に「同じ
  commitに対する重複レビューを防ぐ」という役割を担っており、投入元を区別する新しい仕組みを
  重ねる理由が無い(既存設計の再利用、過剰な作り込みを避ける方針はADR-0035にも通底する)
- `Job`の`payload`自体に「どのフェーズから来たか」を刻む案も検討したが、`review/job.py`
  (M1・M3-1で確立済み)のスキーマ変更が必要になり、本Issueのスコープ(「既存のレビュー
  パイプラインを再利用するだけ」)を超える。将来、投入元によって挙動を変える具体的な要求が
  生じた場合に改めて設計する

### 論点3: `review`フェーズが`WAITING_HUMAN`/`FAILED`になっても連鎖の前提は崩れない

`review/`パッケージ・`cli/dispatcher.py`の`build_review_handler`を確認した結果、`review`の
`JobHandler`(`_handle`)は`execute_review`を呼んで`build_review_job_result`を返すだけで、
`WaitingForHumanError`を送出する分岐を持たない(`issue-analysis`/`design`/`plan`/`implement`と
異なり、不明点の判断を伴わない機械的な処理のため)。したがって`review`Jobは常に`DONE`または
(GitLab API障害等での)`FAILED`のいずれかで完結し、`WAITING_HUMAN`で宙に浮くケースは無い。

`review`は`_NEXT_JOB_TYPE`の次フェーズを持たない最終フェーズのため、`review`完了後に
さらに何かを連鎖させる必要は無い。`FAILED`になった場合も、既存の再試行機構(ADR-0017)・
デッドレター(`list_dead_letters`)で検知可能であり、`advance_pipeline`側で特別なハンドリングは
不要と判断した。

## 却下した選択肢

- **`implement`完了後に`review`Jobを投入する(Issue本文どおり)**: 論点1の通り、`implement`
  完了時点では`mr_iid`が存在せず構造的に不可能。Issue本文の記述をそのまま実装すると
  `KeyError`(または存在しないフィールドへのアクセス)になる
- **`review`Jobのpayloadに投入元(無人実行/手動)を示すフィールドを追加する**: 論点2の通り、
  `execute_review`の処理内容が投入元で変わらないため、区別する実利が無い。既存の`review/job.py`
  スキーマへの変更は本Issueのスコープ(接続部分のみ)を超える
- **`review`完了後もさらに何らかのフェーズへ連鎖させる(例: 自動マージ判断)**: Issue #117の
  スコープは「一次チェックとして`review`に流すこと」のみで、レビュー結果を見た後の判断
  (マージするか、修正が必要か)は引き続き人間が行う設計(`review/`パッケージの既存方針、
  `docs/architecture.md`「GitLabへの自動投稿はしない。最終判断は人間」)を変更する理由が無い

## 影響

- `src/gitlab_ai_platform/orchestrator/pipeline.py`: `_NEXT_JOB_TYPE`に
  `JobType.PUSH: JobType.REVIEW`を追加。`_build_next_job_payload`に`JobType.REVIEW`の分岐を
  追加し、`push`完了Jobの`result`(`project`/`merge_request_iid`/`pushed_commit_sha`)から
  `build_review_job_payload(project, mr_iid, sha=...)`(`review`パッケージ既存、変更なし)を
  呼ぶ
- `src/gitlab_ai_platform/orchestrator/__init__.py`のdocstringを更新
  (6フェーズの連鎖になったことを明記)
- `docs/specs/orchestrator.md`・`docs/specs/push-phase.md`・`docs/specs/cli.md`・
  `docs/architecture.md`を更新
- `docs/roadmap.md`のM4-11を完了に更新
- `cli/dispatcher.py`・`cli/respond.py`・`review/job.py`・`push/job.py`は変更しない
  (既存の`build_review_job_payload`/`build_push_job_result`をそのまま呼ぶだけの接続のため)
- これでM4(Issue駆動開発、無人実行トラック)の全フェーズ(`issue-analysis → design → plan →
  implement → push → review`)が連鎖するようになった。無人実行ラベル付きIssueがPollerに
  検出されてから、MRが作成され、その一次レビューが完了するまで人手を介さずに進む
