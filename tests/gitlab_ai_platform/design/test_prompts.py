from __future__ import annotations

from gitlab_ai_platform.design import DesignInput, build_design_instructions
from gitlab_ai_platform.gitlab_adapter.types import Issue
from gitlab_ai_platform.runner import IssueContext, build_issue_prompt


def _design_input(**overrides) -> DesignInput:
    kwargs = dict(
        requirements=("要求1",),
        acceptance_criteria=("条件1",),
        assumptions=("前提1",),
        assumed_uncertainties=("エラーメッセージの文言は? → 一般的な文言を使う",),
    )
    kwargs.update(overrides)
    return DesignInput(**kwargs)


def test_build_design_instructions_returns_str():
    instructions = build_design_instructions(_design_input())

    assert isinstance(instructions, str)
    assert instructions.strip() != ""


def test_build_design_instructions_is_deterministic():
    design_input = _design_input()

    assert build_design_instructions(design_input) == build_design_instructions(
        design_input
    )


def test_includes_design_input_contents():
    design_input = _design_input()
    instructions = build_design_instructions(design_input)

    assert "要求1" in instructions
    assert "条件1" in instructions
    assert "前提1" in instructions
    assert "エラーメッセージの文言は? → 一般的な文言を使う" in instructions


def test_empty_design_input_does_not_raise_and_uses_placeholder_text():
    design_input = _design_input(
        requirements=(),
        acceptance_criteria=(),
        assumptions=(),
        assumed_uncertainties=(),
    )

    instructions = build_design_instructions(design_input)

    assert isinstance(instructions, str)
    assert "ありません" in instructions


def test_mentions_unattended_track_only():
    instructions = build_design_instructions(_design_input())

    assert "無人実行トラック" in instructions


def test_mentions_repository_access_constraint():
    instructions = build_design_instructions(_design_input())

    assert "リポジトリを参照できません" in instructions


def test_instructs_severity_classification():
    instructions = build_design_instructions(_design_input())

    assert "critical" in instructions
    assert "minor" in instructions


def test_includes_structured_json_schema_matching_design_result():
    instructions = build_design_instructions(_design_input())

    assert "```json" in instructions
    keywords = [
        "design_document",
        "open_questions",
        "question",
        "severity",
        "assumption",
    ]
    for keyword in keywords:
        assert keyword in instructions


def test_instructs_exactly_one_trailing_json_block():
    instructions = build_design_instructions(_design_input())

    assert "必ず1つだけ" in instructions


def test_does_not_contain_issue_specific_data():
    """Issue固有のデータはRunner側(`build_issue_prompt`)が追記するため、ここには含めない。"""
    instructions = build_design_instructions(_design_input())

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
        labels=("要求分析待ち",),
    )
    context = IssueContext(issue=issue)
    design_input = _design_input()

    prompt = build_issue_prompt(build_design_instructions(design_input), context)

    assert prompt.count("## Issue") == 1
    assert prompt.index(build_design_instructions(design_input)) < prompt.index(
        "## Issue"
    )
    assert "Add feature Y" in prompt
