"""「質問する / 仮定して進める」の判断ロジック本体(M4-4、ADR-0024)。

要求分析(M4-3)・設計(M4-6)・実装(M4-8)フェーズが検出した `Uncertainty` を受け取り、
`JudgmentAction.ASK`(処理を止めて人間に確認する)か `JudgmentAction.ASSUME`(仮定を明示して
処理を継続する)かを判定する。呼び出し側(M4-3以降の各フェーズ、Runner Dispatcher)は
`ASK`判定が1件でもあればJobを`WAITING_HUMAN`へ遷移させる(`job/protocol.py`の状態機械)。

判定基準そのものは`JudgmentPolicy`(重要度→アクションの対応付け)として外出しし、
呼び出し側が差し替え可能にしている(ADR-0024「判定基準はJudgmentPolicyとして外出しする」)。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .errors import MissingAssumptionError
from .types import JudgmentAction, Uncertainty, UncertaintyJudgment, UncertaintySeverity


@dataclass(frozen=True)
class JudgmentPolicy:
    """重要度→アクションの対応付け(判定基準そのもの)。

    `ask_severities`に含まれる重要度は`ASK`、含まれない重要度は`ASSUME`と判定する。
    既定では`CRITICAL`のみを`ASK`対象とする。呼び出し側がインスタンスを差し替えることで、
    将来「重要度をもっと細分化する」「キーワードベースの自動判定を挟む」といった調整を、
    このモジュール自体の変更なしに行える(ADR-0024)。
    """

    ask_severities: frozenset[UncertaintySeverity] = field(
        default_factory=lambda: frozenset({UncertaintySeverity.CRITICAL})
    )


# 既定の判定基準。M4-1でラベルにより無人実行可能と事前判断済みのIssueのみが対象になるため、
# 初期実装は「CRITICALは必ず質問、MINORは必ず仮定して継続」という単純な二値ルールで十分
# (Issue #110のスコープ、`references/タスク整理.md` M4-4)
DEFAULT_POLICY = JudgmentPolicy()


def judge_uncertainty(
    uncertainty: Uncertainty, policy: JudgmentPolicy = DEFAULT_POLICY
) -> UncertaintyJudgment:
    """1件の`Uncertainty`を判定する。

    `ASSUME`判定になるのに`uncertainty.assumption`が`None`の場合は`MissingAssumptionError`
    を送出する(MRに残す仮定の文言が無いまま処理を継続させないため)。
    """
    if uncertainty.severity in policy.ask_severities:
        return UncertaintyJudgment(uncertainty=uncertainty, action=JudgmentAction.ASK)

    if uncertainty.assumption is None:
        raise MissingAssumptionError(
            f"仮定して継続する不明点にはassumptionが必須です: {uncertainty.question!r}"
        )
    return UncertaintyJudgment(
        uncertainty=uncertainty,
        action=JudgmentAction.ASSUME,
        assumption_note=uncertainty.assumption,
    )


def judge_uncertainties(
    uncertainties: Sequence[Uncertainty], policy: JudgmentPolicy = DEFAULT_POLICY
) -> list[UncertaintyJudgment]:
    """複数の`Uncertainty`をまとめて判定する。順序は入力の順序を維持する。"""
    return [judge_uncertainty(u, policy) for u in uncertainties]


def requires_human(judgments: Sequence[UncertaintyJudgment]) -> bool:
    """1件でも`ASK`判定があれば`True`。

    呼び出し側がJobを`WAITING_HUMAN`へ遷移させるべきかどうかを判断するのに使う想定
    (`job/protocol.py`の`RUNNING → WAITING_HUMAN`遷移)。
    """
    return any(j.action is JudgmentAction.ASK for j in judgments)


def ask_judgments(
    judgments: Sequence[UncertaintyJudgment],
) -> list[UncertaintyJudgment]:
    """`ASK`判定のみを抽出する(人間へ提示する質問一覧の元、M4-5で使用予定)。"""
    return [j for j in judgments if j.action is JudgmentAction.ASK]


def assume_judgments(
    judgments: Sequence[UncertaintyJudgment],
) -> list[UncertaintyJudgment]:
    """`ASSUME`判定のみを抽出する(MRに残す仮定一覧の元、M4-9で使用予定)。"""
    return [j for j in judgments if j.action is JudgmentAction.ASSUME]


__all__ = [
    "DEFAULT_POLICY",
    "JudgmentPolicy",
    "ask_judgments",
    "assume_judgments",
    "judge_uncertainties",
    "judge_uncertainty",
    "requires_human",
]
