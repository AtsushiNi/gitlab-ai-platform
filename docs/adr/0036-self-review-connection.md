# ADR-0036: 自己レビュー接続

- Issue: [#117](https://github.com/AtsushiNi/gitlab-ai-platform/issues/117) (M4-11)
- 状態: 決定

## 背景・制約

- M4-10([ADR-0035](0035-pipeline-orchestration.md))で`issue-analysis → design → plan → implement → push`の5フェーズ連鎖を実現した。`push`完了後は次フェーズが無く`None`を返すだけだった
- Issue本文は「無人実行で作られたMRを、そのままレビューJob(M1で完成済み)に流し、人間レビュー前の一次チェックとする」がスコープ
- Issue本文には「implement完了後」という記述があるが、フェーズ再編前の古い呼び方を引きずっている可能性を疑い、実装前にコードを検証した

## 決定

### 論点1: 接続点は「push完了後」であり「implement完了後」ではない

`_NEXT_JOB_TYPE`に`JobType.PUSH: JobType.REVIEW`を追加し、push完了後に`review`Jobを自動投入する。`review`Jobの投入に必須の`mr_iid`は、GitLab上に実際にMRが作成されて初めて存在する値であり、それを作るのはpushフェーズ。`implement`完了時点では`mr_iid`が存在せず構造的に不可能なため、Issue本文の記述はそのまま採用しなかった。

`review`Jobの`sha`には、push完了時の`result`が持つ`pushed_commit_sha`を明示的に渡す(省略時は「MR取得時点の最新commit」が使われるが、無人実行トラックではpush完了時点でレビュー対象commitが確定しているため)。

### 論点2: 無人実行由来の`review`Jobと人間が手動投入する`review`Jobを区別しない

`build_review_job_payload`のシグネチャは変更しない。対象の`(project, mr_iid, sha)`が同じであれば処理内容は完全に同一であり、State Storeが既に二重レビュー防止の役割を担っているため、投入元を区別する仕組みを追加する理由がない。

### 論点3: `review`フェーズが`WAITING_HUMAN`/`FAILED`になっても連鎖の前提は崩れない

`review`のJobHandlerは`WaitingForHumanError`を送出する分岐を持たない(不明点の判断を伴わない機械的な処理のため)。`review`は最終フェーズであり、完了後にさらに連鎖させる必要はない。

## 却下した選択肢

- **`implement`完了後に`review`Jobを投入する(Issue本文どおり)**: `mr_iid`が存在せず構造的に不可能
- **`review`Jobのpayloadに投入元(無人実行/手動)を示すフィールドを追加する**: 処理内容が投入元で変わらないため区別する実利がなく、既存スキーマへの変更は本Issueのスコープを超える
- **`review`完了後もさらに何らかのフェーズへ連鎖させる(自動マージ判断等)**: レビュー結果を見た後の判断は引き続き人間が行う設計を変更する理由がない

## 影響

- `orchestrator/pipeline.py`: `_NEXT_JOB_TYPE`に`JobType.PUSH: JobType.REVIEW`を追加
- これでM4(Issue駆動開発、無人実行トラック)の全フェーズ(`issue-analysis → design → plan → implement → push → review`)が連鎖するようになった。無人実行ラベル付きIssueがPollerに検出されてから、MRが作成され、その一次レビューが完了するまで人手を介さずに進む
