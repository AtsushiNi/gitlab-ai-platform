from __future__ import annotations

from gitlab_ai_platform.gitlab_adapter.types import Issue
from gitlab_ai_platform.implement import ImplementInput, build_implement_instructions
from gitlab_ai_platform.plan import PlanTask
from gitlab_ai_platform.runner import IssueContext, build_issue_prompt


def _implement_input(**overrides) -> ImplementInput:
    kwargs = dict(
        plan_document="# 概要\n実装計画の本文です。",
        tasks=(
            PlanTask(title="タスク1", description="内容1"),
            PlanTask(title="タスク2", description="内容2"),
        ),
        assumed_uncertainties=("エラーメッセージの文言は? → 一般的な文言を使う",),
    )
    kwargs.update(overrides)
    return ImplementInput(**kwargs)


def test_build_implement_instructions_returns_str():
    instructions = build_implement_instructions(_implement_input())

    assert isinstance(instructions, str)
    assert instructions.strip() != ""


def test_build_implement_instructions_is_deterministic():
    implement_input = _implement_input()

    assert build_implement_instructions(
        implement_input
    ) == build_implement_instructions(implement_input)


def test_includes_implement_input_contents():
    implement_input = _implement_input()
    instructions = build_implement_instructions(implement_input)

    assert "# 概要\n実装計画の本文です。" in instructions
    assert "タスク1" in instructions
    assert "タスク2" in instructions
    assert "エラーメッセージの文言は? → 一般的な文言を使う" in instructions


def test_empty_tasks_does_not_raise_and_uses_placeholder_text():
    implement_input = _implement_input(tasks=())

    instructions = build_implement_instructions(implement_input)

    assert isinstance(instructions, str)
    assert "ありません" in instructions


def test_empty_assumed_uncertainties_does_not_raise_and_uses_placeholder_text():
    implement_input = _implement_input(assumed_uncertainties=())

    instructions = build_implement_instructions(implement_input)

    assert "ありません" in instructions


def test_mentions_unattended_track_only():
    instructions = build_implement_instructions(_implement_input())

    assert "無人実行トラック" in instructions


def test_permits_edit_and_commit_but_forbids_push():
    # design/planと異なり、実装フェーズはファイル編集・commitを明示的に許可しつつ、
    # git push/mergeは明示的に禁止する(ADR-0033)
    instructions = build_implement_instructions(_implement_input())

    assert "git commit" in instructions
    assert "git push" in instructions
    assert "許可されていない操作" in instructions


def test_instructs_severity_classification():
    instructions = build_implement_instructions(_implement_input())

    assert "critical" in instructions
    assert "minor" in instructions


def test_includes_structured_json_schema_matching_implement_result():
    instructions = build_implement_instructions(_implement_input())

    assert "```json" in instructions
    keywords = [
        "summary",
        "commit_message",
        "tests_passed",
        "open_questions",
        "question",
        "severity",
        "assumption",
    ]
    for keyword in keywords:
        assert keyword in instructions


def test_instructs_exactly_one_trailing_json_block():
    instructions = build_implement_instructions(_implement_input())

    assert "必ず1つだけ" in instructions


def test_does_not_contain_issue_specific_data():
    """Issue固有のデータはRunner側(`build_issue_prompt`)が追記するため、ここには含めない。"""
    instructions = build_implement_instructions(_implement_input())

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
        labels=("実装待ち",),
    )
    context = IssueContext(issue=issue)
    implement_input = _implement_input()

    prompt = build_issue_prompt(build_implement_instructions(implement_input), context)

    assert prompt.count("## Issue") == 1
    assert prompt.index(build_implement_instructions(implement_input)) < prompt.index(
        "## Issue"
    )
    assert "Add feature Y" in prompt
