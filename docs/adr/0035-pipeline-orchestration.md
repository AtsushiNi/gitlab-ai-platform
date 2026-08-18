# ADR-0035: Issue→MRパイプラインのオーケストレーション

- Issue: [#116](https://github.com/AtsushiNi/gitlab-ai-platform/issues/116) (M4-10)
- 状態: 決定

## 背景・制約

- M4-1(Issue Poller、[#107](https://github.com/AtsushiNi/gitlab-ai-platform/issues/107))から
  M4-9(push と MR 作成、[#115](https://github.com/AtsushiNi/gitlab-ai-platform/issues/115))までで、
  無人実行トラックの5フェーズ(`issue-analysis` → `design` → `plan` → `implement` → `push`、
  それぞれ独立した`JobType`)が個別に実装・マージ済みになった。各フェーズの実装コメント
  (`design/job.py`・`plan/job.py`・`implement/job.py`・`push/job.py`のモジュールdocstring)は
  一貫して「このフェーズを投入する自動化された検出器はまだ存在しない(オーケストレーション本体は
  M4-10のスコープ)」と書いており、本Issueがその「投入する」部分を実装する
- 各フェーズは`build_<次フェーズ>_job_payload(project, issue_iid, 前フェーズのpayload/result)`と
  いう変換関数を既に用意している(`design.build_design_job_payload`/
  `plan.build_plan_job_payload`/`implement.build_implement_job_payload`/
  `push.build_push_job_payload`)。本Issueのスコープはこれらを正しい順序で呼び出す「接着剤」の
  実装であり、新しい変換ロジックを書くことではない
- Jobが`DONE`になる経路は2つある: (a)`RunnerDispatcher._process`(`cli/dispatcher.py`)が
  `handler`成功後に`complete`を呼ぶ経路、(b)`respond_to_job`(`cli/respond.py`)が
  `WAITING_HUMAN`から人間の回答を経て`update_status(..., DONE)`を呼ぶ経路。両方の経路で
  次フェーズへの連鎖が起きる必要がある
- `WAITING_HUMAN`(人間の回答待ち)・`FAILED`(失敗)になったJobは連鎖させてはいけない
- `orchestrator/__init__.py`のdocstringが「フェーズ間の状態遷移そのもの(M4-1〜M4-6,
  M4-9〜M4-10)は将来ここに追加していく」と予告しており、既にM4-4の判断ロジック
  (`judgment.py`、`ask_judgments`/`assume_judgments`/`requires_human`)がこのパッケージにある

## 決定

### 論点1: 完了検知と次フェーズ投入は`orchestrator.pipeline.advance_pipeline`という1つの関数に集約する

`src/gitlab_ai_platform/orchestrator/pipeline.py`に、`JobType`ごとの「次フェーズ」対応表
(`_NEXT_JOB_TYPE`)と、それを見て次フェーズJobを`enqueue`する関数を追加した。

```python
def advance_pipeline(job_repo: JobRepository, completed_job: Job) -> Job | None: ...
```

`_NEXT_JOB_TYPE`は`issue-analysis → design → plan → implement → push`の連鎖そのものを表す唯一の
対応表で、`review`(別トラック)と`push`(最終フェーズ)は含まない(次が無ければ`None`)。
次フェーズのpayloadは各フェーズが既に持つ`build_<次フェーズ>_job_payload`をそのまま呼び出すだけで
組み立てる(`_build_next_job_payload`)。`implement → push`のみ、`completed_job`の`payload`と
`result`の両方を必要とする(`build_push_job_payload`のシグネチャ、ADR-0034「論点2」)。

`RunnerDispatcher._process`(経路a)・`respond_to_job`(経路b)の両方に、Job完了確定の**直後**に
呼ぶ任意のフック`on_job_completed: Callable[[Job], None]`を追加した。合成ルート
(`run_dispatcher`/`run_respond`)は`job_repo`を束縛した`advance_pipeline`(後述の
`advance_pipeline_hook`)をこのフックとして渡す。これにより:

- `RunnerDispatcher`/`respond_to_job`はフェーズ順序を一切知らないまま(ADR-0022の設計原則を
  維持)、パイプラインの連鎖という「横断的関心事」を実現できる
- MR Poller(`poller/poller.py`)の`on_detected`コールバック注入パターンと同じ形の設計であり、
  既存コードベースの慣習に沿う

`advance_pipeline`の戻り値`Job | None`と、`on_job_completed`の契約`Callable[[Job], None]`の
型が合わないため(mypy検出)、変換だけを行う薄いヘルパー`advance_pipeline_hook(job_repo) ->
Callable[[Job], None]`を追加し、合成ルートはこちらを渡す。

### 論点2: `WAITING_HUMAN`・`FAILED`との境界は「呼び出しタイミング」で表現する

`advance_pipeline`を呼ぶタイミング自体を「Jobが`complete`/`WAITING_HUMAN → DONE`で成功した
直後」に限定することで、`WAITING_HUMAN`・`FAILED`を連鎖させない境界を表現した。

- `RunnerDispatcher._process`: `on_job_completed`は`self._job_repo.complete(...)`が成功した
  `else`節の中でのみ呼ぶ。`WaitingForHumanError`捕捉節(`wait_for_human`を呼ぶ)、
  `NotImplementedError`/`Exception`捕捉節(`fail`を呼ぶ)では呼ばない
- `respond_to_job`: `on_job_completed`は`job_repo.update_status(job.id, JobStatus.DONE, ...)`が
  例外を送出せず成功した後にのみ呼ぶ。`except BaseException`節(`FAILED`へ倒す経路)では呼ばない

これに加えて、`advance_pipeline`自身にも`completed_job.status is not JobStatus.DONE`の場合に
`None`を返すガードを入れた。呼び出し側が正しく「成功後にのみ呼ぶ」設計になっていれば冗長だが、
将来の呼び出し追加や実装ミスに対する安価な防御として残す。

フック(`advance_pipeline`)自体が送出しうる例外(次フェーズ投入の失敗)は、`RunnerDispatcher`・
`respond_to_job`の両方でログのみに変換し、呼び出し元へ伝播させない。**既に成功した今回のJobの
完了確定を、後続フェーズ投入の失敗で巻き戻す理由が無い**ため
(`respond_to_job`の場合、`DONE`確定後に例外を伝播させると呼び出し元の`except BaseException`が
誤って`FAILED`更新を試み、既に`DONE`のJobへの不正な状態遷移`InvalidJobTransitionError`を
引き起こしかねない)。`advance_pipeline`自身も、次フェーズ`enqueue`が`JobError`で失敗した場合は
ログのみで`None`を返す(Issue Poller の`ticket_issue_if_unprocessed`と同じ「握りつぶして
ログに残す」パターン)。

### 論点3: 成果物の永続化は既存のJob Queueで足りる。新しい索引は追加しない

Issue単位で「どのフェーズまで進んだか」を横断的に追跡する専用の索引テーブルは追加しなかった。
各フェーズのJob `payload`/`result`が既に`project`/`issue_iid`を持っており(全フェーズの
`build_..._job_result`が含む)、`JobRepository`の既存機能(`get`/`list_by_status`/
`list_dead_letters`、および将来の`api`サブコマンド経由の一覧取得)で追跡可能なため。
「あるIssueが今どのフェーズにいるか」は、そのIssueに対応する一連のJob(`issue-analysis`→
`design`→…と`enqueue`された各レコード)を辿ることで再構成できる。Issue #116本文の
「過剰な作り込みは避け、既存のJob Queueで足りるならそれで済ませる」方針に従った。

### 論点4: `pipeline.py`は`orchestrator/__init__.py`から再エクスポートしない(循環import回避)

`pipeline.py`は`design`/`plan`/`implement`/`push`各パッケージ(の`job.py`)へ依存する。一方で
これらのパッケージの`job.py`は`from ..orchestrator import UncertaintyJudgment, ask_judgments,
assume_judgments`という形で`orchestrator`パッケージ本体(`__init__.py`)へ依存している
(M4-3〜M4-9で既に確立済みの依存方向)。

もし`orchestrator/__init__.py`が`pipeline.py`をimportすると、
`design`/`plan`/`implement`/`push`のどのモジュールが実行時に最初にimportされるかという
順序に依存して、循環importが解決できたりできなかったりする脆い状態になる
(`design.job`が先に一部だけ実行された状態で`orchestrator.__init__`経由で`pipeline.py`に
戻ってくると、`design.job`からまだエクスポートされていない名前をimportしようとして
`ImportError`になりうる)。

これを避けるため、`pipeline.py`は`orchestrator/__init__.py`から意図的に再エクスポートしない。
呼び出し側(`cli/dispatcher.py`・`cli/respond.py`)は
`from ..orchestrator.pipeline import advance_pipeline_hook`とサブモジュールを明示的にimportする。
`pipeline.py`自体は他のどのモジュールからも通常のimport時に到達されない葉モジュールのため、
この構成であれば実行順序に関わらず安全であることを確認した(手動でのimport順序入れ替えテストで
検証済み)。

## 却下した選択肢

- **`RunnerDispatcher`/`respond_to_job`にフェーズ遷移ルールを直接埋め込む**: ADR-0022の
  「`RunnerDispatcher`はJob種別ごとの固有ロジックを一切知らない」という設計原則に反する。
  `respond_to_job`についても同様に、`_RESULT_RESOLVERS`のような種別非依存のマッピング機構を
  すでに持っており、そこにフェーズ順序という別の関心事を混ぜたくない
- **`orchestrator/__init__.py`から`pipeline.py`を再エクスポートする**: 論点4の通り、
  `design`等のimport順序に依存する脆い循環importになりうるため見送った
- **Issue単位の進捗索引テーブルを新設する(例: `issue_pipeline_state`テーブル)**: 論点3の通り、
  既存のJob `payload`/`result`の`project`/`issue_iid`と`JobRepository`の一覧機能で追跡できるため、
  過剰な作り込みを避けた。将来、HTTP API(`api/`)がIssue単位のパイプライン状態を1回のクエリで
  返す必要が生じた場合に改めて検討する
- **`advance_pipeline`の失敗(次フェーズ投入失敗)を呼び出し元に伝播させ、`respond_to_job`側で
  何らかの形でJobを`FAILED`にする**: 既に`DONE`として確定したJobを、後続フェーズ投入の失敗を
  理由に`FAILED`へ巻き戻すのは意味的に誤り(そのフェーズ自体は成功している)。ログに残した上で
  諦め、次フェーズJobが投入されていないことは`list_by_status`等で検知可能にする方針にした
- **`on_job_completed`の例外を握りつぶさず伝播させる(`MrPoller.run`の`on_detected`と同じ契約)**:
  `MrPoller.run`は「1件のレビュー失敗を握りつぶして継続するかどうかは呼び出し側の判断に委ねる」
  という設計だが、`RunnerDispatcher`は`run_forever`で無人・ヘッドレスに動き続けることを前提とし
  (ADR-0022)、1件のJob処理の失敗が他のJobの処理を止めない設計を既に貫いている。
  `on_job_completed`の失敗で`worker`プロセス全体を落とすのはこの既存方針との整合性を欠くため、
  ログのみに変換する方針を採った

## 影響

- `src/gitlab_ai_platform/orchestrator/pipeline.py`(新規)に`advance_pipeline`/
  `advance_pipeline_hook`を追加
- `src/gitlab_ai_platform/orchestrator/__init__.py`のdocstringを更新(`pipeline.py`は
  意図的に再エクスポートしないことを明記)
- `src/gitlab_ai_platform/cli/dispatcher.py`: `RunnerDispatcher.__init__`に
  `on_job_completed: Callable[[Job], None] | None = None`を追加。`_process`の`complete`成功
  直後に呼ぶ(例外はログのみに変換)。`run_dispatcher`が`advance_pipeline_hook(job_repo)`を
  束縛して渡す
- `src/gitlab_ai_platform/cli/respond.py`: `respond_to_job`に同じ`on_job_completed`引数を追加し、
  `DONE`遷移成功直後に呼ぶ。`run_respond`が同じく`advance_pipeline_hook(job_repo)`を渡す
- `docs/specs/orchestrator.md`を更新し、`advance_pipeline`/`advance_pipeline_hook`の
  公開インターフェースを追記
- `docs/roadmap.md`のM4-10を完了に更新
- これでM4(Issue駆動開発、無人実行トラック)の5フェーズが実際に連鎖するようになった。
  無人実行ラベル付きIssueがPollerに検出されてから、途中で人間の確認が必要な場合を除き、
  MRが作成されるまで人手を介さずに進む
