# ADR-0035: Issue→MRパイプラインのオーケストレーション

- Issue: [#116](https://github.com/AtsushiNi/gitlab-ai-platform/issues/116) (M4-10)
- 状態: 決定

## 背景・制約

- M4-1からM4-9までで、無人実行トラックの5フェーズ(`issue-analysis` → `design` → `plan` → `implement` → `push`、それぞれ独立した`JobType`)が個別に実装・マージ済みになった。各フェーズは既に`build_<次フェーズ>_job_payload`という変換関数を用意しており、本Issueのスコープはこれらを正しい順序で呼び出す「接着剤」の実装
- Jobが`DONE`になる経路は2つある: (a)`RunnerDispatcher._process`が`handler`成功後に`complete`を呼ぶ経路、(b)`respond_to_job`が`WAITING_HUMAN`から人間の回答を経て`DONE`にする経路。両方で次フェーズへの連鎖が必要
- `WAITING_HUMAN`・`FAILED`になったJobは連鎖させてはいけない

## 決定

### 論点1: 完了検知と次フェーズ投入は`orchestrator.pipeline.advance_pipeline`という1つの関数に集約する

`JobType`ごとの「次フェーズ」対応表(`_NEXT_JOB_TYPE`、`issue-analysis → design → plan → implement → push`の連鎖)を持ち、`advance_pipeline(job_repo, completed_job) -> Job | None`が次フェーズJobを`enqueue`する。`RunnerDispatcher._process`・`respond_to_job`の両方に、Job完了確定の**直後**に呼ぶ任意のフック`on_job_completed`を追加し、合成ルートがこのフックとして`advance_pipeline`を束縛して渡す。`RunnerDispatcher`/`respond_to_job`はフェーズ順序を一切知らないまま([ADR-0022](0022-runner-process-separation.md)の設計原則を維持)、連鎖という横断的関心事を実現する。

### 論点2: `WAITING_HUMAN`・`FAILED`との境界は「呼び出しタイミング」で表現する

`advance_pipeline`を呼ぶタイミングを「Jobが成功した直後」に限定することで境界を表現する。加えて`advance_pipeline`自身にも`completed_job.status is not JobStatus.DONE`のガードを入れる(冗長だが安価な防御)。フック自体が送出しうる例外はログのみに変換し、呼び出し元へ伝播させない(既に成功した今回のJobの完了確定を、後続フェーズ投入の失敗で巻き戻す理由がないため)。

### 論点3: 成果物の永続化は既存のJob Queueで足りる。新しい索引は追加しない

Issue単位で「どのフェーズまで進んだか」を横断的に追跡する専用テーブルは追加しない。各フェーズのJob payload/resultが既に`project`/`issue_iid`を持ち、既存の`get`/`list_by_status`で追跡可能。

### 論点4: `pipeline.py`は`orchestrator/__init__.py`から再エクスポートしない(循環import回避)

`pipeline.py`は各フェーズパッケージへ依存する一方、各フェーズパッケージは`orchestrator`パッケージ本体へ依存している。`orchestrator/__init__.py`が`pipeline.py`をimportすると、実行時のimport順序に依存して循環importが解決できたりできなかったりする脆い状態になる。呼び出し側はサブモジュールを明示的にimportする。

## 却下した選択肢

- **`RunnerDispatcher`/`respond_to_job`にフェーズ遷移ルールを直接埋め込む**: ADR-0022の「種別固有ロジックを一切知らない」という設計原則に反する
- **`orchestrator/__init__.py`から`pipeline.py`を再エクスポートする**: 循環importになりうるため見送った
- **Issue単位の進捗索引テーブルを新設する**: 既存のJob payload/resultと一覧機能で追跡できるため過剰な作り込みを避けた
- **`advance_pipeline`の失敗を呼び出し元に伝播させ`FAILED`にする**: 既に`DONE`として確定したJobを後続フェーズ投入の失敗で巻き戻すのは意味的に誤り
- **`on_job_completed`の例外を握りつぶさず伝播させる**: `RunnerDispatcher`は無人・ヘッドレスに動き続けることを前提とし、1件の失敗が他のJob処理を止めない設計を既に貫いているため

## 影響

- `orchestrator/pipeline.py`(新規)に`advance_pipeline`/`advance_pipeline_hook`を追加
- `cli/dispatcher.py`・`cli/respond.py`に`on_job_completed`引数を追加し、成功確定直後に呼ぶ
- M4(Issue駆動開発、無人実行トラック)の5フェーズが実際に連鎖するようになった。無人実行ラベル付きIssueがPollerに検出されてから、途中で人間の確認が必要な場合を除き、MRが作成されるまで人手を介さずに進む
