# ADR-0024: 「質問する / 仮定して進める」判断ロジックの設計

- Issue: [#110](https://github.com/AtsushiNi/gitlab-ai-platform/issues/110) (M4-4)
- 状態: 決定

## 背景・制約

- 無人実行では「分からない情報をAIが勝手に補完して進める」危険が大きい。重要な不明点があれば処理を止めて人間に確認し、軽微な疑問は仮定を明示した上で処理を継続する判断が必要
- `job/protocol.py`には既に`JobStatus.WAITING_HUMAN`とその遷移が定義済み。本Issueのスコープはこの遷移を「いつ発動させるか」を判断するロジックであり、状態遷移の実行自体は対象外
- 要求分析・設計・実装フェーズは未実装のため、判断ロジックは独立したモジュールとして設計する。過剰な作り込みは避け、初期実装はシンプルにする(M4-1のラベルポーリングで事前に無人実行可能と判断済みのIssueのみが対象のため)

## 決定

### 「不明点」と「判定結果」を種別非依存の型として定義する

新設する`orchestrator`パッケージに以下を置く:

```python
class UncertaintySeverity(str, Enum):
    CRITICAL = "critical"  # 重要な不明点。ASK対象
    MINOR = "minor"  # 軽微な疑問。ASSUME対象

class JudgmentAction(str, Enum):
    ASK = "ask"
    ASSUME = "assume"

@dataclass(frozen=True)
class Uncertainty:
    question: str
    severity: UncertaintySeverity
    assumption: str | None = None
    phase: str | None = None
```

`issue-analysis`/`design`/`implement`のどのフェーズからも使える種別非依存の型とする。

### 重要度は呼び出し側が`Uncertainty`作成時に明示する(キーワード自動判定はしない)

呼び出し側が不明点の文脈を最も把握しているため。キーワードベースの自動判定は表層的な語彙に頼ることになり過剰な作り込みになる。

### 重要度→アクションの対応付けは`JudgmentPolicy`として外出しし、差し替え可能にする

既定の`DEFAULT_POLICY`は「`CRITICAL`は必ずASK、`MINOR`は必ずASSUME」という単純な二値ルール。判断基準を調整したい場合は呼び出し側が別の`JudgmentPolicy`インスタンスを渡すだけでよく、判定ロジック本体は変更不要。

### `ASSUME`判定には仮定の文言を必須とする

`Uncertainty.assumption`が`None`の場合は`MissingAssumptionError`を送出する。仮定の文言はMR本文の「○○と仮定して実装した」という記述の元になる情報であり、欠けたまま継続すると無人実行の成果物から「何を仮定したか」が失われる。

### `WAITING_HUMAN`への遷移そのものはこのモジュールでは行わない

`requires_human(judgments) -> bool`という補助関数のみを提供し、Jobの状態遷移は呼び出し側の責務のままとする。

## 却下した選択肢

- **キーワードベースで重要度を自動判定する**: 誤判定時のデバッグが難しくなる
- **重要度を`bool`にする**: 将来の3値化を見越して`Enum`にした
- **`JudgmentPolicy`をクラス継承(Strategyパターン)にする**: frozen dataclass 1つで足りる。過剰な作り込みを避けた
- **本モジュールがJobの状態遷移を直接呼び出す**: Job Queueの呼び出し規約を本モジュールが知る必要はなく、疎結合を優先した

## 影響

- M4-3/M4-6/M4-8は、検出した不明点を`Uncertainty`として組み立て、`judge_uncertainties`→`requires_human`の結果でJobを`WAITING_HUMAN`へ遷移させるか判断できる
- M4-5は`ask_judgments`が返す質問文を使える
- M4-9は`assume_judgments`が返す`assumption_note`をMR本文の記述の元として使える
- 詳細仕様は`docs/specs/orchestrator.md`に文書化する
