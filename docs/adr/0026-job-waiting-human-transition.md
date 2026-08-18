# ADR-0026: Job Queue経由での`WAITING_HUMAN`遷移の設計

- Issue: [#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109) (M4-3)
- 状態: 決定

## 背景・制約

- `job/protocol.py`には`JobStatus.WAITING_HUMAN`とその許可遷移が定義済みだったが、それを実行する`update_status`は非リース方式の経路向けであり、リース方式([ADR-0017](0017-job-queue.md))を使う`RunnerDispatcher`([ADR-0022](0022-runner-process-separation.md))からは呼べなかった
- `RunnerDispatcher`は種別固有ロジックを一切知らない設計であり、`JobHandler`は「成功して`complete`する」か「例外を送出して`fail`させる」の二値しか伝える手段を持たず、「`WAITING_HUMAN`にしてほしい」という第三の意図を伝える契約が存在しなかった
- M4-3(要求分析フェーズ)が、[ADR-0024](0024-ask-or-assume-judgment.md)の判定結果に応じてJobを`WAITING_HUMAN`へ遷移させる必要がある最初のフェーズ

## 決定

### `JobRepository`に`complete`と対になる`wait_for_human`を追加する

`claim`/`heartbeat`/`complete`/`fail`と同じリース方式のメソッドとして追加する。`RUNNING → WAITING_HUMAN`へ遷移させたうえでリース情報をクリアする(`worker_id`不一致は`LeaseLostError`)。この遷移は[ADR-0016](0016-job-abstraction.md)が既に許可済みの遷移であり、新しい遷移を追加するものではない。

### `JobHandler`は`WaitingForHumanError`という専用の例外で意図を伝える

`RunnerDispatcher`が`NotImplementedError`を特別扱いしているのと同じパターン。

```python
class WaitingForHumanError(Exception):
    def __init__(self, result: dict[str, Any]) -> None: ...
```

`RunnerDispatcher._process`の振り分け:

- 正常終了 → `complete`
- `WaitingForHumanError` → `wait_for_human(job_id, worker_id, result=exc.result)`
- `NotImplementedError` → `fail(..., retry=False)`
- その他の例外 → `fail(..., retry=True)`

例外(`JobHandler`↔`RunnerDispatcher`間の意図伝達)と新メソッド(`RunnerDispatcher`↔`JobRepository`間のリース方式の状態遷移)は責務の階層が異なるため、両方が必要。

### 質問内容は`Job.result`にそのまま記録する(専用フィールドは追加しない)

`ask_judgments`が返す不明点のリストは、`complete`の`result`と同じ`Job.result: dict[str, Any] | None`に記録する。具体的なキー構成は呼び出し側が定義する(ADR-0016の「payload/resultは種別非依存のdict」をWAITING_HUMANの場合にも一貫して適用)。`questions`は`WAITING_HUMAN`のときのみ非空になる。

## 却下した選択肢

- **`Job`に`questions`等の専用フィールドを追加する**: フェーズごとに必要な情報の形が異なるため、Job抽象自体に持たせるとADR-0016の設計原則に反する
- **`JobHandler`の戻り値を`dict | WaitingForHumanSignal`のUnion型にする**: 既存handlerにも型シグネチャ変更を強制してしまう。例外ベースの方が変更が小さい
- **`update_status`をそのまま`RunnerDispatcher`から呼ぶ**: リース情報が残ったままになり、`complete`/`fail`が徹底しているリースクリアの一貫性が崩れる

## 影響

- `job/protocol.py`・`job/sqlite.py`に`wait_for_human`を追加
- `cli/dispatcher.py`に`WaitingForHumanError`・`build_issue_analysis_handler`を追加
- M4-6(設計フェーズ)・M4-8(実装フェーズ)も同じ`WaitingForHumanError`/`wait_for_human`の組み合わせで`WAITING_HUMAN`遷移を実現できる
- M4-5(人間への質問提示と回答の取り込み)は`Job.result["questions"]`を質問一覧の取得元として使う
