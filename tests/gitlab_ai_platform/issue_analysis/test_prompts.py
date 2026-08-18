from __future__ import annotations

from gitlab_ai_platform.gitlab_adapter.types import Issue
from gitlab_ai_platform.issue_analysis import build_issue_analysis_instructions
from gitlab_ai_platform.runner import IssueContext, build_issue_prompt


def test_build_issue_analysis_instructions_returns_str():
    instructions = build_issue_analysis_instructions()

    assert isinstance(instructions, str)
    assert instructions.strip() != ""


def test_build_issue_analysis_instructions_is_deterministic():
    assert build_issue_analysis_instructions() == build_issue_analysis_instructions()


def test_instructs_the_four_analysis_points():
    instructions = build_issue_analysis_instructions()

    for keyword in ["要求", "受入条件", "前提", "不足情報"]:
        assert keyword in instructions


def test_instructs_severity_classification():
    instructions = build_issue_analysis_instructions()

    assert "critical" in instructions
    assert "minor" in instructions


def test_includes_structured_json_schema_matching_requirement_analysis():
    """`types.RequirementAnalysis`・`orchestrator.types.Uncertainty`のフィールドと
    1対1になるJSONスキーマを指示していること。"""
    instructions = build_issue_analysis_instructions()

    assert "```json" in instructions
    keywords = [
        "requirements",
        "acceptance_criteria",
        "assumptions",
        "open_questions",
        "question",
        "severity",
        "assumption",
    ]
    for keyword in keywords:
        assert keyword in instructions


def test_instructs_exactly_one_trailing_json_block():
    instructions = build_issue_analysis_instructions()

    assert "必ず1つだけ" in instructions


def test_does_not_contain_issue_specific_data():
    """Issue固有のデータはRunner側(`build_issue_prompt`)が追記するため、ここには含めない。"""
    instructions = build_issue_analysis_instructions()

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

    prompt = build_issue_prompt(build_issue_analysis_instructions(), context)

    assert prompt.count("## Issue") == 1
    assert prompt.index(build_issue_analysis_instructions()) < prompt.index("## Issue")
    assert "Add feature Y" in prompt
