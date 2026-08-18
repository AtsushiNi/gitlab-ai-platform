# ADR-0024: 「質問する / 仮定して進める」判断ロジックの設計

- Issue: [#110](https://github.com/AtsushiNi/gitlab-ai-platform/issues/110) (M4-4)
- 状態: 決定

## 背景・制約

- `docs/requirements.md`「B. Issue駆動開発(将来)」節が指摘する通り、対話型のClaude Codeと異なり
  無人実行では「分からない情報をAIが勝手に補完して進める」危険が大きい。重要な不明点があれば
  処理を止めて人間に確認し、軽微な疑問は仮定を明示した上で処理を継続する、という判断が必要になる
- `job/protocol.py`(M3-1・M3-2、ADR-0016/0017)には既に`JobStatus.WAITING_HUMAN`と、それに至る
  遷移(`RUNNING → WAITING_HUMAN`、`WAITING_HUMAN → RUNNING/FAILED`)が定義済み。本Issueのスコープは
  この遷移を「いつ発動させるか」を判断するロジックであり、Job状態機械そのものやJob遷移の実行
  (`update_status`の呼び出し)は対象外(呼び出し側の責務)
- 本Issueの時点(M4-4)では、要求分析(M4-3、Job種別`issue-analysis`)・設計(M4-6、Job種別`design`)
  ・実装(M4-8、Job種別`implement`)フェーズは未実装。判断ロジックはそれらから将来呼ばれる
  **独立したモジュール**として設計する必要がある(呼び出し元の具体的なプロンプト構造やJob payload
  の形はまだ確定していない)
- `references/タスク整理.md` M4-3(≒本Issue)は「判断基準そのものを設計・調整可能にする」ことを
  要求している一方、「M4-1(ラベルポーリング)で人間が事前に無人実行可能と判断してラベルを付けた
  ものだけが対象になるため、`WAITING_HUMAN`への遷移頻度は低い想定。初期実装はシンプルでよい」とも
  明記されている。過剰な作り込みは避け、拡張点を用意した上でルール自体は単純にする

## 決定

### 「不明点」と「判定結果」を種別非依存の型として定義する

`src/gitlab_ai_platform/orchestrator/types.py`に以下を置く(実装場所は`area/orchestrator`
ラベルに合わせて新設する`orchestrator`パッケージ)。

```python
class UncertaintySeverity(str, Enum):
    CRITICAL = "critical"  # 重要な不明点。ASK対象
    MINOR = "minor"  # 軽微な疑問。ASSUME対象


class JudgmentAction(str, Enum):
    ASK = "ask"  # 質問して停止する
    ASSUME = "assume"  # 仮定を明示して継続する


@dataclass(frozen=True)
class Uncertainty:
    question: str
    severity: UncertaintySeverity
    assumption: str | None = None  # ASSUMEする場合に採用する仮定の文言
    phase: str | None = None  # 生じたフェーズ(任意、ログ用)


@dataclass(frozen=True)
class UncertaintyJudgment:
    uncertainty: Uncertainty
    action: JudgmentAction
    assumption_note: str | None = None  # action=ASSUMEの場合のみ値を持つ
```

`review` Job(`review/job.py`)がJob抽象と分離しているのと同じ流儀で、Job抽象自体
(`job/protocol.py`)には一切手を入れない。`Uncertainty`/`UncertaintyJudgment`は
`issue-analysis`/`design`/`implement`のどのフェーズからも使える種別非依存の型とする。

### 重要度は呼び出し側が`Uncertainty`作成時に明示する(キーワード自動判定は今回やらない)

「不明点作成時に呼び出し側が重要度を明示する」「キーワードベースで自動判定する」の2案を検討し、
前者を採用した。理由:

- 呼び出し側(要求分析フェーズ等)は不明点の**文脈**(Issue本文のどの記述が曖昧か)を持っており、
  重要度の判断材料が最も揃っているのはその時点。キーワードベースの自動判定は「重要」「必須」等の
  表層的な語彙に頼ることになり、M4-1の事前トリアージ(ラベル付与)がある前提では過剰な作り込み
- 呼び出し側が明示する設計にしておいても、将来「キーワードベースの前段フィルタを`Uncertainty`
  生成前に挟む」「LLM自身に重要度を判定させるプロンプトにする」といった拡張は、本モジュールの
  外側(呼び出し側)に追加するだけで実現でき、本モジュールの変更を必要としない

### 重要度→アクションの対応付けは`JudgmentPolicy`として外出しし、差し替え可能にする

`src/gitlab_ai_platform/orchestrator/judgment.py`に以下を置く:

```python
@dataclass(frozen=True)
class JudgmentPolicy:
    ask_severities: frozenset[UncertaintySeverity] = field(
        default_factory=lambda: frozenset({UncertaintySeverity.CRITICAL})
    )


DEFAULT_POLICY = JudgmentPolicy()


def judge_uncertainty(
    uncertainty: Uncertainty, policy: JudgmentPolicy = DEFAULT_POLICY
) -> UncertaintyJudgment: ...


def judge_uncertainties(
    uncertainties: Sequence[Uncertainty], policy: JudgmentPolicy = DEFAULT_POLICY
) -> list[UncertaintyJudgment]: ...
```

既定の`DEFAULT_POLICY`は「`CRITICAL`は必ずASK、`MINOR`は必ずASSUME」という単純な二値ルール
(タスク整理.mdが許容する「初期実装はシンプルでよい」に対応)。「判断基準自体を設計・調整可能に
する」という要求は、`JudgmentPolicy`を呼び出し側が差し替え可能な**設定値**として関数の外に
出すことで満たす。将来「重要度をもっと細分化する(例: `MODERATE`を追加)」「特定フェーズだけ
`MINOR`もASKにする」といった調整は、呼び出し側が別の`JudgmentPolicy`インスタンスを渡すだけで
実現でき、`judge_uncertainty`本体のロジックは変更不要。

### `ASSUME`判定には仮定の文言を必須とする

`ASSUME`と判定するのに`Uncertainty.assumption`が`None`の場合は`MissingAssumptionError`を
送出する。仮定の文言はM4-9でMRに残す「○○と仮定して実装した」という記述の元になる情報であり、
これが欠けたまま処理を継続させると、無人実行の成果物から「何を仮定したか」が失われる
(`docs/requirements.md`が問題視する「AIが分からないことを勝手に推測してしまう」状態と同じ
リスクを、仮定の文言化を怠ることで再現してしまう)。呼び出し側の実装ミスを判定時点で早期検知
する目的の例外であり、複雑なエラー分類はしない(`store/errors.py`・`job/errors.py`と同じ方針)。

### `WAITING_HUMAN`への遷移そのものはこのモジュールでは行わない

`requires_human(judgments) -> bool`という補助関数のみを提供し、Jobの`update_status`呼び出しは
呼び出し側(将来のRunner Dispatcher・各フェーズのJobHandler)の責務のままとする。本モジュールは
「複数の`Uncertainty`のうち1件でも`ASK`判定があるか」を返すだけで、実際の状態遷移・人間への
質問提示(M4-5)・回答の取り込み(M4-5)には関与しない。

## 却下した選択肢

- **キーワードベースで重要度を自動判定する**: 「重要」「必須」「セキュリティ」等の語彙を
  ハードコードしても、M4-1の事前トリアージ前提では実効性が低い割に、誤判定時のデバッグが
  難しくなる。呼び出し側が文脈込みで重要度を明示する方が、現時点では単純かつ確実
- **重要度を`bool`(重要/軽微の2値)にする**: `JudgmentPolicy`による調整の余地を残すため、
  将来の3値化(例: `MODERATE`追加)を見越して`Enum`にした。`bool`だと将来の粒度変更が
  `Uncertainty`を使う全呼び出し元のシグネチャ変更を伴ってしまう
- **`JudgmentPolicy`をクラス継承(Strategyパターンの抽象基底クラス)にする**: 現時点で
  「重要度→アクション」以外の判定軸(フェーズ・キーワード等)を持ち込む具体的な要求がなく、
  frozen dataclass 1つで足りる。継承ベースの拡張性は、実際にキーワードベース判定等の要求が
  出た時点で導入すれば十分(過剰な作り込みを避けるタスク整理.mdの方針に従う)
- **本モジュールがJobの`update_status`を直接呼び出す(状態遷移まで面倒を見る)**: `job/protocol.py`
  の`JobRepository`に依存させると、判断ロジック単体でのテスト・再利用性が下がる。Job Queueの
  具体的な呼び出し規約(`worker_id`によるリース所有権チェック等、ADR-0017)を本モジュールが
  知る必要はなく、疎結合を優先した

## 影響

- M4-3(要求分析フェーズ、Job種別`issue-analysis`)・M4-6(設計フェーズ、Job種別`design`)・
  M4-8(実装フェーズ、Job種別`implement`)は、それぞれが検出した不明点を`Uncertainty`として
  組み立て、`judge_uncertainties`→`requires_human`の結果でJobを`WAITING_HUMAN`へ遷移させるか
  判断できる
- M4-5(人間への質問提示と回答の取り込み)は`ask_judgments`が返す`UncertaintyJudgment`の
  `uncertainty.question`を質問文として使える
- M4-9(pushとMR作成)は`assume_judgments`が返す`UncertaintyJudgment`の`assumption_note`を
  MR本文の「○○と仮定して実装した」記述の元として使える
- 詳細仕様は`docs/specs/orchestrator.md`として文書化する(D-6のフォーマットに従う)
