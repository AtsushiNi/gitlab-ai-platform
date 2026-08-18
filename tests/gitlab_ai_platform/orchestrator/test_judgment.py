import pytest

from gitlab_ai_platform.orchestrator import (
    DEFAULT_POLICY,
    JudgmentAction,
    JudgmentPolicy,
    MissingAssumptionError,
    Uncertainty,
    UncertaintySeverity,
    ask_judgments,
    assume_judgments,
    judge_uncertainties,
    judge_uncertainty,
    requires_human,
)


def _uncertainty(
    severity: UncertaintySeverity,
    *,
    assumption: str | None = None,
    question: str = "不明点",
) -> Uncertainty:
    return Uncertainty(question=question, severity=severity, assumption=assumption)


def test_critical_uncertainty_is_judged_as_ask():
    uncertainty = _uncertainty(UncertaintySeverity.CRITICAL)

    judgment = judge_uncertainty(uncertainty)

    assert judgment.action is JudgmentAction.ASK
    assert judgment.assumption_note is None
    assert judgment.uncertainty is uncertainty


def test_minor_uncertainty_with_assumption_is_judged_as_assume():
    uncertainty = _uncertainty(
        UncertaintySeverity.MINOR, assumption="Aのケースを対象と仮定した"
    )

    judgment = judge_uncertainty(uncertainty)

    assert judgment.action is JudgmentAction.ASSUME
    assert judgment.assumption_note == "Aのケースを対象と仮定した"


def test_minor_uncertainty_without_assumption_raises_missing_assumption_error():
    uncertainty = _uncertainty(UncertaintySeverity.MINOR, assumption=None)

    with pytest.raises(MissingAssumptionError):
        judge_uncertainty(uncertainty)


def test_judge_uncertainties_preserves_input_order():
    uncertainties = [
        _uncertainty(UncertaintySeverity.CRITICAL, question="質問1"),
        _uncertainty(UncertaintySeverity.MINOR, assumption="仮定2", question="質問2"),
        _uncertainty(UncertaintySeverity.CRITICAL, question="質問3"),
    ]

    judgments = judge_uncertainties(uncertainties)

    assert [j.uncertainty.question for j in judgments] == ["質問1", "質問2", "質問3"]
    assert [j.action for j in judgments] == [
        JudgmentAction.ASK,
        JudgmentAction.ASSUME,
        JudgmentAction.ASK,
    ]


def test_requires_human_true_when_any_ask_judgment_present():
    judgments = judge_uncertainties(
        [
            _uncertainty(UncertaintySeverity.MINOR, assumption="仮定"),
            _uncertainty(UncertaintySeverity.CRITICAL),
        ]
    )

    assert requires_human(judgments) is True


def test_requires_human_false_when_all_assume():
    judgments = judge_uncertainties(
        [
            _uncertainty(UncertaintySeverity.MINOR, assumption="仮定1"),
            _uncertainty(UncertaintySeverity.MINOR, assumption="仮定2"),
        ]
    )

    assert requires_human(judgments) is False


def test_requires_human_false_for_empty_judgments():
    assert requires_human([]) is False


def test_ask_and_assume_judgments_filter_correctly():
    uncertainties = [
        _uncertainty(UncertaintySeverity.CRITICAL, question="質問1"),
        _uncertainty(UncertaintySeverity.MINOR, assumption="仮定2", question="質問2"),
    ]
    judgments = judge_uncertainties(uncertainties)

    ask_only = ask_judgments(judgments)
    assume_only = assume_judgments(judgments)

    assert [j.uncertainty.question for j in ask_only] == ["質問1"]
    assert [j.uncertainty.question for j in assume_only] == ["質問2"]


def test_custom_policy_can_treat_minor_as_ask_too():
    # ADR-0024: 判定基準(JudgmentPolicy)は呼び出し側が差し替え可能。CRITICAL/MINOR
    # 両方をASK対象にする、より慎重なポリシーへ切り替えられることを確認する
    cautious_policy = JudgmentPolicy(
        ask_severities=frozenset(
            {UncertaintySeverity.CRITICAL, UncertaintySeverity.MINOR}
        )
    )
    uncertainty = _uncertainty(UncertaintySeverity.MINOR, assumption="仮定")

    judgment = judge_uncertainty(uncertainty, cautious_policy)

    assert judgment.action is JudgmentAction.ASK


def test_custom_policy_can_treat_critical_as_assume_when_assumption_given():
    # 逆方向の差し替え例: CRITICALをASK対象から外すと、assumption付きならASSUME判定になる
    lenient_policy = JudgmentPolicy(ask_severities=frozenset())
    uncertainty = _uncertainty(UncertaintySeverity.CRITICAL, assumption="仮定")

    judgment = judge_uncertainty(uncertainty, lenient_policy)

    assert judgment.action is JudgmentAction.ASSUME
    assert judgment.assumption_note == "仮定"


def test_default_policy_only_asks_for_critical():
    assert DEFAULT_POLICY.ask_severities == frozenset({UncertaintySeverity.CRITICAL})
