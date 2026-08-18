# ADR-0026: Job Queue経由での`WAITING_HUMAN`遷移の設計

- Issue: [#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109) (M4-3)
- 状態: 決定

## 背景・制約

- `job/protocol.py`(M3-1、ADR-0016)には`JobStatus.WAITING_HUMAN`と、それに至る許可遷移
  (`RUNNING → WAITING_HUMAN`、`WAITING_HUMAN → RUNNING/FAILED`)が既に`update_status`の
  遷移表として定義済みだった。しかし`update_status`は非リース方式の経路
  (`cli/single_run.py`の`execute_review_job`が使う、`enqueue`直後に同一プロセス内で
  同期処理する経路)向けのメソッドで、リース方式(M3-2、ADR-0017、`claim`/`heartbeat`/
  `complete`/`fail`)を使う`RunnerDispatcher`(M3-3、`cli/dispatcher.py`)からは呼べない
  設計だった。`RunnerDispatcher._process`は`handler`の実行結果を`complete`(成功)か
  `fail`(例外送出時)にしか振り分けられず、`WAITING_HUMAN`へ遷移させる経路が存在しなかった
- M4-3(要求分析フェーズ、Job種別`issue-analysis`)は、`orchestrator.judge_uncertainties`/
  `requires_human`(M4-4、ADR-0024)の判定結果に応じて、無人実行を継続できないJobを
  `WAITING_HUMAN`へ遷移させる必要がある最初のフェーズ。この遷移をリース方式の
  `RunnerDispatcher`からどう実現するかを決定する必要があった
- `RunnerDispatcher`は`review`固有・`issue-analysis`固有のロジックを一切知らない
  (ADR-0022「Job受け渡しプロトコル」)。`JobHandler`(`Callable[[Job], dict[str, Any] | None]`)は
  「成功して`complete`する結果」か「例外を送出する(`fail`させる)」の二値しか呼び出し元へ
  伝える手段を持たず、「`WAITING_HUMAN`にしてほしい」という第三の意図を伝える契約が
  存在しなかった
- 人間が回答した後にJobを再開する具体的な仕組み(M4-5、[#111](https://github.com/AtsushiNi/gitlab-ai-platform/issues/111))は本Issueのスコープ外だが、本ADRの設計が
  その土台になる。特に「質問内容(`ask_judgments`が返す`UncertaintyJudgment`のリスト)を
  Jobのどこにどう記録するか」は、M4-5がその情報を読み出して人間に提示する前提となるため、
  ここで仕様化しておく必要がある

## 決定

### `JobRepository`に`complete`と対になる`wait_for_human`を追加する

`job/protocol.py`のProtocolに、`claim`/`heartbeat`/`complete`/`fail`と同じ「リース方式」の
メソッドとして`wait_for_human`を追加する。

```python
def wait_for_human(
    self, job_id: str, worker_id: str, result: dict[str, Any] | None = None
) -> Job:
    """claim済みJobが人間の確認を必要とすると報告し`WAITING_HUMAN`へ遷移させる。

    `RUNNING → WAITING_HUMAN`へ遷移させたうえでリース情報をクリアする。`worker_id`が
    現在のリース所有者と一致しない場合は`LeaseLostError`を送出する。
    """
    ...
```

SQLite実装(`job/sqlite.py`)は`complete`と全く同じ構造(`_require_leased_locked`で
リース所有権を検証→`update_status`で許可済みの遷移を実行→`_clear_lease_locked`で
リース情報をクリア)で、遷移先だけが`WAITING_HUMAN`になる。`WAITING_HUMAN`は`claim`の
対象外の状態のため、`complete`/`fail`と同様にリース情報(`lease_owner`/`lease_token`/
`lease_expires_at`)をクリアする。

`update_status`の遷移表(ADR-0016)は変更しない。`wait_for_human`は`RUNNING → WAITING_HUMAN`
という**既に許可されている遷移**を、リース方式の呼び出し規約(`worker_id`検証・リースクリア)
でラップするだけで、新しい遷移を追加するものではない。

### `JobHandler`は`WaitingForHumanError`という専用の例外で意図を伝える

`RunnerDispatcher`(`cli/dispatcher.py`)が`NotImplementedError`(未対応のJobType、ADR-0016)を
特別扱いしているのと同じパターンで、`WaitingForHumanError`という専用の例外を追加する。

```python
class WaitingForHumanError(Exception):
    """JobHandlerが人間の確認を必要とすると判断したことを表す。

    `result`には`wait_for_human`にそのまま渡す辞書(質問一覧等)を持つ。
    """

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("人間の確認が必要です(WAITING_HUMAN)")
        self.result = result
```

`RunnerDispatcher._process`は`handler(job)`の呼び出しを次のように振り分ける
(`NotImplementedError`のcatchより前に置く。`WaitingForHumanError`は`NotImplementedError`の
サブクラスではないため順序自体はどちらが先でもよいが、「成功に近い」意味を持つため
`else`(complete)に近い位置に置く):

- 正常終了(`result`を返す) → `complete(job_id, worker_id, result=result)`
- `WaitingForHumanError`を送出 → `wait_for_human(job_id, worker_id, result=exc.result)`
- `NotImplementedError`を送出(未対応JobType) → `fail(..., retry=False)`(即デッドレター化)
- その他の例外 → `fail(..., retry=True)`(リトライ判定は`JobRepository`に委ねる)

「新しいJob Repositoryメソッドを追加する」案と「JobHandlerが例外で意図を伝える」案は
どちらか一方を選ぶものではなく、組み合わせて使う: 例外(`WaitingForHumanError`)は
**JobHandlerとRunnerDispatcherの間の契約**(「このJobは今回はここで止める」という意図の
伝達手段)を担い、新メソッド(`wait_for_human`)は**RunnerDispatcherとJobRepositoryの間の
契約**(リース方式での実際の状態遷移)を担う。責務の階層が異なるため、両方が必要。

### 質問内容は`Job.result`にそのまま記録する(専用フィールドは追加しない)

`ask_judgments`が返す`UncertaintyJudgment`のリストは、`complete`の`result`と全く同じ
`Job.result: dict[str, Any] | None`フィールドに記録する。`Job`抽象に`questions`のような
専用フィールドを追加しない。

具体的なキー構成は呼び出し側(`issue_analysis/job.py`の`build_issue_analysis_job_result`)が
定義する(ADR-0016「payload/resultは種別非依存のdictとして扱う」を`WAITING_HUMAN`の場合にも
一貫して適用する)。issue-analysisの場合:

```python
{
    "project": str,
    "issue_iid": int,
    "requirements": list[str],  # 要求
    "acceptance_criteria": list[str],  # 受入条件
    "assumptions": list[str],  # 前提(Claude Codeが分析時点で述べたもの)
    "assumed_uncertainties": [  # ASSUME判定された不明点(M4-9で使用予定)
        {"question": str, "severity": "minor", "assumption": str},
        ...,
    ],
    "questions": [  # ASK判定された不明点。WAITING_HUMANのときのみ非空
        {"question": str, "severity": "critical"},
        ...,
    ],
}
```

`complete`(`requires_human`が`False`)・`wait_for_human`(`True`)のどちらでも同じ関数
(`build_issue_analysis_job_result`)・同じ構造を使う。`questions`は`WAITING_HUMAN`のときのみ
非空になり、`complete`のときは必ず空配列になる(`requires_human`が`False`の場合のみ
`complete`を呼ぶため)。M4-5は`Job.result["questions"]`を読み出して人間に提示し、回答を
受け取ったら(具体的な取り込み方法はM4-5のスコープ)`update_status(job_id, RUNNING)`で
Jobを再開する想定(既存の`WAITING_HUMAN → RUNNING`遷移をそのまま使う)。

## 却下した選択肢

- **`Job`に`questions`等の専用フィールドを追加する**: `review`/`issue-analysis`/`design`/
  `implement`でフェーズごとに必要な情報の形が異なりうる中、Job抽象自体に特定フェーズ向けの
  フィールドを持たせると、ADR-0016が決定した「payload/resultは種別非依存」という設計原則に
  反する。`complete`の`result`と同じ枠組みを使えば、Job抽象への変更なしに拡張できる
- **`JobHandler`の戻り値の型を`dict | WaitingForHumanSignal`のようなUnion型にする**:
  `JobHandler = Callable[[Job], dict[str, Any] | None]`という既存の型契約を変更せずに済む
  例外ベースの方式のほうが、`RunnerDispatcher`側の変更が「新しいexcept節を1つ足すだけ」で
  済み、`NotImplementedError`と一貫したパターンになる。戻り値のUnion型は呼び出し側
  (`build_review_handler`等の既存handler)にも型シグネチャの変更を強制してしまう
- **`update_status`をそのまま`RunnerDispatcher`から呼ぶ(新メソッドを追加しない)**: `claim`済み
  Jobは`lease_owner`/`lease_expires_at`を持つため、`update_status`で状態だけ変えると
  リース情報が残ったままになる(`WAITING_HUMAN`はclaim対象外の状態のため実害は小さいが、
  `complete`/`fail`が徹底しているリースクリアの一貫性が崩れる)。M3-2導入時と同じ厳密さで
  `wait_for_human`を追加する方が、`complete`/`fail`との対称性を保てる

## 影響

- `job/protocol.py`(Protocol定義)・`job/sqlite.py`(SQLite実装)に`wait_for_human`を追加。
  `job/test_protocol.py`(メソッド集合の完全一致テスト)・`job/test_sqlite.py`
  (`wait_for_human`の遷移・リースクリア・`LeaseLostError`のテスト)を更新
- `cli/dispatcher.py`に`WaitingForHumanError`・`build_issue_analysis_handler`を追加し、
  `RunnerDispatcher._process`に`WaitingForHumanError`の分岐を追加
- M4-6(設計フェーズ、Job種別`design`)・M4-8(実装フェーズ、Job種別`implement`)も、
  同じ`WaitingForHumanError`/`wait_for_human`の組み合わせで`WAITING_HUMAN`遷移を実現できる
  (`issue-analysis`固有の実装ではなく、`JobHandler`一般の契約として設計したため)
- M4-5(人間への質問提示と回答の取り込み)は、`Job.result["questions"]`を質問一覧の
  取得元として使う想定
