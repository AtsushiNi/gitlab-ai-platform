from __future__ import annotations

from gitlab_ai_platform.gitlab_adapter.types import Issue
from gitlab_ai_platform.plan import PlanInput, build_plan_instructions
from gitlab_ai_platform.runner import IssueContext, build_issue_prompt


def _plan_input(**overrides) -> PlanInput:
    kwargs = dict(
        design_document="# 責務\n設計文書の本文です。",
        assumed_uncertainties=("エラーメッセージの文言は? → 一般的な文言を使う",),
    )
    kwargs.update(overrides)
    return PlanInput(**kwargs)


def test_build_plan_instructions_returns_str():
    instructions = build_plan_instructions(_plan_input())

    assert isinstance(instructions, str)
    assert instructions.strip() != ""


def test_build_plan_instructions_is_deterministic():
    plan_input = _plan_input()

    assert build_plan_instructions(plan_input) == build_plan_instructions(plan_input)


def test_includes_plan_input_contents():
    plan_input = _plan_input()
    instructions = build_plan_instructions(plan_input)

    assert "# 責務\n設計文書の本文です。" in instructions
    assert "エラーメッセージの文言は? → 一般的な文言を使う" in instructions


def test_empty_assumed_uncertainties_does_not_raise_and_uses_placeholder_text():
    plan_input = _plan_input(assumed_uncertainties=())

    instructions = build_plan_instructions(plan_input)

    assert isinstance(instructions, str)
    assert "ありません" in instructions


def test_mentions_unattended_track_only():
    instructions = build_plan_instructions(_plan_input())

    assert "無人実行トラック" in instructions


def test_mentions_repository_access_constraint():
    instructions = build_plan_instructions(_plan_input())

    assert "リポジトリを参照できません" in instructions


def test_instructs_severity_classification():
    instructions = build_plan_instructions(_plan_input())

    assert "critical" in instructions
    assert "minor" in instructions


def test_includes_structured_json_schema_matching_plan_result():
    instructions = build_plan_instructions(_plan_input())

    assert "```json" in instructions
    keywords = [
        "plan_document",
        "tasks",
        "title",
        "description",
        "open_questions",
        "question",
        "severity",
        "assumption",
    ]
    for keyword in keywords:
        assert keyword in instructions


def test_instructs_exactly_one_trailing_json_block():
    instructions = build_plan_instructions(_plan_input())

    assert "必ず1つだけ" in instructions


def test_does_not_contain_issue_specific_data():
    """Issue固有のデータはRunner側(`build_issue_prompt`)が追記するため、ここには含めない。"""
    instructions = build_plan_instructions(_plan_input())

    assert "## Issue" not in instructions


def test_combines_with_issue_prompt_without_duplicating_sections():
    """`runner.build_issue_prompt`と組み合わせても、Issue情報が指示の後に1回だけ現れること。"""
    issue = Issue(
        project="group/project",
        iid=7,
        title="Add feature Y",
        description="We need feature Y.",
        state="opened",
        author="carol",
        labels=("設計待ち",),
    )
    context = IssueContext(issue=issue)
    plan_input = _plan_input()

    prompt = build_issue_prompt(build_plan_instructions(plan_input), context)

    assert prompt.count("## Issue") == 1
    assert prompt.index(build_plan_instructions(plan_input)) < prompt.index("## Issue")
    assert "Add feature Y" in prompt
