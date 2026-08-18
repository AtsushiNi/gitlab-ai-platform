from __future__ import annotations

from gitlab_ai_platform.gitlab_adapter.types import Issue
from gitlab_ai_platform.runner import IssueContext, build_issue_prompt


def test_build_issue_prompt_includes_instructions_and_issue_fields(
    issue_context: IssueContext,
):
    prompt = build_issue_prompt("Analyze this issue carefully.", issue_context)

    assert "Analyze this issue carefully." in prompt
    assert issue_context.issue.title in prompt
    assert issue_context.issue.description in prompt
    assert "要求分析待ち" in prompt


def test_build_issue_prompt_omits_description_heading_when_empty():
    issue = Issue(
        project="group/project",
        iid=8,
        title="No description issue",
        description="",
        state="opened",
        author="dave",
    )
    context = IssueContext(issue=issue)

    prompt = build_issue_prompt("instructions", context)

    assert "Description:" not in prompt
    assert "Labels:" not in prompt


def test_build_issue_prompt_truncates_when_description_exceeds_max_bytes():
    # subprocess_runner.build_promptの切り詰めと同じ方針の回帰テスト。
    # argvとしてPopenに渡す際のOS上限(MAX_ARG_STRLEN)を超えないようにする
    huge_description = "a" * 200_000
    issue = Issue(
        project="group/project",
        iid=9,
        title="Huge issue",
        description=huge_description,
        state="opened",
        author="erin",
    )
    context = IssueContext(issue=issue)

    prompt = build_issue_prompt("instructions", context)

    assert len(prompt.encode("utf-8")) <= 100_000 + 200
    assert "切り詰められました" in prompt


def test_build_issue_prompt_matches_expected_format(issue_context: IssueContext):
    prompt = build_issue_prompt("instructions", issue_context)
    issue = issue_context.issue

    expected = (
        "instructions\n\n"
        "## Issue\n"
        f"Title: {issue.title}\n\n"
        "Description:\n"
        f"{issue.description}\n\n"
        f"Labels: {', '.join(issue.labels)}"
    )
    assert prompt == expected
