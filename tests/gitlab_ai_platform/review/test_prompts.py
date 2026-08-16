from __future__ import annotations

from gitlab_ai_platform.gitlab_adapter.types import MergeRequest
from gitlab_ai_platform.review import build_review_instructions
from gitlab_ai_platform.runner import ReviewContext
from gitlab_ai_platform.runner.subprocess_runner import build_prompt


def test_build_review_instructions_returns_str():
    instructions = build_review_instructions()

    assert isinstance(instructions, str)
    assert instructions.strip() != ""


def test_build_review_instructions_is_deterministic():
    assert build_review_instructions() == build_review_instructions()


def test_includes_focus_points():
    instructions = build_review_instructions()

    for keyword in [
        "致命的なバグ",
        "仕様",
        "デグレ",
        "テスト不足",
        "将来問題になりうる設計",
        "実装ミス",
    ]:
        assert keyword in instructions


def test_includes_suppress_points():
    instructions = build_review_instructions()

    assert "好み" in instructions
    assert "ノイズ" in instructions


def test_instructs_to_explore_repository_beyond_diff():
    instructions = build_review_instructions()

    assert "diff" in instructions
    assert "探索" in instructions
    assert "テストコード" in instructions
    assert "既存の実装" in instructions


def test_includes_output_guidance_with_field_names():
    instructions = build_review_instructions()

    for keyword in ["重要度", "ファイル", "根拠", "改善案"]:
        assert keyword in instructions


def test_includes_structured_json_schema_matching_finding():
    """`review.types.Finding`のフィールドと1対1になるJSONスキーマを指示していること。"""
    instructions = build_review_instructions()

    assert "```json" in instructions
    keywords = [
        "summary",
        "findings",
        "severity",
        "critical",
        "major",
        "minor",
        "file",
        "line",
        "rationale",
        "suggestion",
    ]
    for keyword in keywords:
        assert keyword in instructions


def test_instructs_exactly_one_trailing_json_block():
    instructions = build_review_instructions()

    assert "必ず1つだけ" in instructions


def test_does_not_contain_mr_specific_data():
    """MR固有のデータはRunner側(`build_prompt`)が追記するため、ここには含めない。"""
    instructions = build_review_instructions()

    assert "## Merge Request" not in instructions
    assert "## Diff" not in instructions


def test_combines_with_runner_prompt_without_duplicating_sections():
    """Runnerの`build_prompt`と組み合わせても、MR情報が指示の後に1回だけ現れること。"""
    merge_request = MergeRequest(
        project="group/project",
        iid=1,
        title="Fix bug",
        description="Fixes a bug.",
        state="opened",
        source_branch="fix",
        target_branch="main",
        sha="abcdef0123456789",
        author="alice",
    )
    context = ReviewContext(merge_request=merge_request, diffs=(), discussions=())

    prompt = build_prompt(build_review_instructions(), context)

    assert prompt.count("## Merge Request") == 1
    assert prompt.index(build_review_instructions()) < prompt.index("## Merge Request")
    assert "Fix bug" in prompt
