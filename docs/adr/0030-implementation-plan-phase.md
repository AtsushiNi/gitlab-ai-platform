# ADR-0030: 実装計画フェーズのJob種別設計

- Issue: [#113](https://github.com/AtsushiNi/gitlab-ai-platform/issues/113) (M4-7)
- 状態: 決定

## 背景・制約

- M4-6(設計フェーズ、Job種別`design`、[#112](https://github.com/AtsushiNi/gitlab-ai-platform/issues/112)、
  [ADR-0029](0029-design-phase.md))が、要求分析結果を元に実装前の設計をレビュー可能な
  成果物(`design_document`というMarkdown)として出力する仕組みを確立した。本Issueはその次の
  フェーズ: 設計を実装可能な粒度のタスクへ具体化する(タスク分解する)フェーズを実装する
- `job/protocol.py`の`JobType`(M3-1、[ADR-0016](0016-job-abstraction.md))は
  `review`/`issue-analysis`/`design`/`implement`の4値のみを予約している。ADR-0016策定時点では
  M4のフェーズ構成が「要求分析→設計→実装」の3段階として構想されており、「実装計画生成/
  タスク分解」を独立したフェーズとして切り出す設計(`references/タスク整理.md`のM4-6
  「実装計画の生成とタスク分解」)は反映されていなかった。そのため「実装計画生成」専用の
  `JobType`が存在せず、本Issueで最初に決める必要があった
- 検討した選択肢:
  - **選択肢A**: `JobType`に`PLAN = "plan"`を新規追加し、`issue-analysis`/`design`と全く同じ
    「1フェーズ1Job種別」パターン(専用パッケージ・`build_job_handlers`への登録・
    `WaitingForHumanError`/`wait_for_human`の再利用・`respond`の`_RESULT_RESOLVERS`拡張)を
    踏襲する
  - **選択肢B**: 独立した`JobType`を追加せず、実装計画生成のロジック(プロンプト生成・パース・
    型定義)のみを再利用可能なモジュールとして作り、実際の呼び出しは将来のM4-8(実装フェーズ、
    Job種別`implement`、未実装)のJobHandler内部の最初のステップとして組み込む前提にする

## 決定

### 選択肢Aを採用: `JobType.PLAN = "plan"`を追加する

`job/protocol.py`の`JobType`に`PLAN = "plan"`を追加し、`issue-analysis`/`design`と同じ
「1フェーズ1Job種別」のパターンをそのまま踏襲する。`src/gitlab_ai_platform/plan/`
(新規パッケージ)に`types.py`/`prompts.py`/`parser.py`/`errors.py`/`job.py`を用意し、
`cli/dispatcher.py`に`build_plan_handler`を追加して`build_job_handlers`が`JobType.PLAN`に
対応付ける。`WAITING_HUMAN`遷移(`WaitingForHumanError`/`JobRepository.wait_for_human`)・
`respond`サブコマンドでの回答統合(`_RESULT_RESOLVERS`)も`issue-analysis`/`design`と同じ
枠組みでそのまま拡張する。

理由:

- **既存4フェーズとの一貫性**: `review`/`issue-analysis`/`design`/`implement`はいずれも
  「1フェーズ1Job種別」で設計されており(ADR-0016「決定」)、`implement`は未実装ながら
  既に`JobType`に予約済みである。実装計画フェーズだけを選択肢Bのように「独立したJobを持たない
  内部ステップ」として扱うと、パイプライン全体(Issue→要求分析→設計→
  **実装計画**→実装→push/MR)の中でこのフェーズだけがJob Queue上で観測できない
  (`list_by_status`・`list_dead_letters`・HTTP API経由の状態確認の対象外になる)非対称な設計
  になり、`docs/architecture.md`が描くJob単位の進捗管理・再試行・デッドレターの恩恵を
  受けられない
- **`WAITING_HUMAN`の再利用**: タスク分解の過程でも要求分析・設計と同様に「この粒度で
  実装を進めてよいか」「このタスク分割の前提でよいか」といった不明点が生じうる
  (`orchestrator.judge_uncertainties`が対象とする典型的なケース)。独立したJobとして
  `WaitingForHumanError`/`wait_for_human`を再利用できる選択肢Aの方が、これまでの3フェーズと
  全く同じ仕組みで人間の確認を挟める。選択肢Bでは、この判断を実装フェーズ(M4-8)の
  JobHandler内部に埋め込むことになり、「タスク分解の不明点」と「実装自体の不明点」が
  同じJobの`WAITING_HUMAN`に混在し、`respond`で提示する質問がどちらに由来するか
  区別しにくくなる
- **将来のオーケストレーション(M4-10)との整合性**: ADR-0029は「M4-10実装時に
  `issue-analysis`完了→`design`投入の橋渡しコードが必要になる」と予告しており、実際に
  本ADRでも同じパターン(`design`完了→`plan`投入)を踏襲する。パイプライン全体が
  「あるフェーズの`Job.result`から次のフェーズの`payload`を組み立てて`enqueue`する」という
  一貫した橋渡しコードの連鎖として構成できる。選択肢Bを採用すると、実装計画フェーズだけ
  この連鎖から外れ、M4-8のJobHandlerが「設計結果を受け取ってタスク分解し、そのまま実装する」
  という2フェーズ分の責務を1つのJobHandler・1回のJob実行に持つことになり、
  ADR-0022が定めた「JobHandlerはJobType非依存の薄い処理単位」という設計意図から外れる
- **Enum変更のリスクは小さい**: `job/test_protocol.py`の`test_job_type_values`は
  `JobType.REVIEW == "review"`のように個々の値を確認しているだけで、値集合の網羅性
  (「4値しかないこと」)を検証していないため、新しい値を追加しても既存テストは壊れない。
  また`job/sqlite.py`の`jobs`テーブルは`job_type`列を`TEXT NOT NULL`とし、許容値を列挙する
  `CHECK`制約を持たない(値は`JobType(...).value`として文字列で保存・読み出しされるのみ)ため、
  新しい列挙値の追加によるDBスキーマ変更・マイグレーションは不要

### `plan`フェーズもWorkspace Manager(worktree)を使わず`run_prompt`のまま実行する

`build_plan_handler`は`issue-analysis`/`design`と同じく`ClaudeCodeRunner.run_prompt`を使い、
Workspace Managerは引数に取らない。理由はADR-0029が設計フェーズについて述べた理由
(Workspace Managerの`prepare(project, mr_iid, ref)`がMR用に設計されており、Issue単位の
フェーズに転用するには追加の拡張が必要)と全く同じであり、本Issueのスコープでも変わらない。
入力は設計フェーズが確定させた`design_document`(既にリポジトリ探索を経ずに書かれた方針レベルの
文書)であるため、タスク分解自体もリポジトリ探索を必須としない。

### 対象プロジェクトへの実際のコミットは行わない

設計フェーズ(ADR-0029)と同じ理由で、タスク一覧を構造化データとして`Job.result`に持たせる
ところまでとし、GitLabへの書き込みは行わない。実際にタスクを元にbranch作成・実装・commitを
行うのはM4-8(実装フェーズ)の責務。

## 却下した選択肢

- **選択肢B(独立したJobTypeを持たず、M4-8のJobHandler内部のステップとして実装する)**:
  「決定」節に記載の理由(Job Queueでの観測性の非対称、`WAITING_HUMAN`の混在、
  オーケストレーションの一貫性、ADR-0022の設計意図との不整合)により見送った。実装コストは
  選択肢Bの方が本Issue単体では小さい(Enum変更・handler配線・respond拡張が不要)が、
  M4-8実装時に結局「設計結果→タスク分解→実装」という3段階の処理を1つのJobHandlerに
  詰め込む複雑さとして跳ね返ってくると判断した
- **`build_resolved_plan_job_result`を`issue_analysis`/`design`の同名関数と共通化する**:
  ADR-0029の「却下した選択肢」と全く同じ理由(3つ目の重複時点で共通化を検討する方針を
  ADR-0029が既に予告している)で、本Issueでも見送った

## 影響

- `job/protocol.py`の`JobType`に`PLAN = "plan"`を追加。`job/test_protocol.py`の
  `test_job_type_values`に対応するassertionを追加(既存の網羅性を検証しないテスト構造は
  そのまま維持)
- `src/gitlab_ai_platform/plan/`(新規)に`types.py`(`PlanInput`/`PlanTask`/`PlanResult`)・
  `prompts.py`(`build_plan_instructions`)・`parser.py`(`parse_plan_output`)・
  `errors.py`(`PlanError`/`PlanOutputParseError`)・`job.py`
  (`build_plan_job_payload`/`plan_job_payload_to_args`/`build_plan_job_result`/
  `build_resolved_plan_job_result`)を追加。`design`パッケージと同一の構成・命名パターン
- `src/gitlab_ai_platform/cli/dispatcher.py`に`build_plan_handler`を追加し、
  `build_job_handlers`が`JobType.PLAN`に対応付ける(`WaitingForHumanError`/`wait_for_human`を
  そのまま再利用)
- `src/gitlab_ai_platform/cli/respond.py`の`_RESULT_RESOLVERS`に`JobType.PLAN`を追加
- M4-10(Issue→MRパイプラインのオーケストレーション)実装時、`design`完了Jobの`result`から
  `plan.build_plan_job_payload`で`plan`種別Jobのpayloadを組み立てて投入する橋渡しコードが
  必要になる(本Issueでは投入者=Poller相当は実装しない、`design`と同じ位置づけ)
- M4-8(実装フェーズ、Job種別`implement`)実装時、`plan`完了Jobの`result`(`tasks`の一覧)を
  入力として実装を進める橋渡しコードが必要になる
