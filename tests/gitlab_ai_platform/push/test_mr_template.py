from gitlab_ai_platform.push import (
    build_merge_request_description,
    build_merge_request_title,
)

_ISSUE_IID = 7


def test_build_merge_request_title_uses_first_line_of_summary():
    title = build_merge_request_title(_ISSUE_IID, "タスク1を実装した\n詳細は本文参照")

    assert title == f"[Issue #{_ISSUE_IID}] タスク1を実装した"


def test_build_merge_request_title_falls_back_when_summary_is_blank():
    title = build_merge_request_title(_ISSUE_IID, "   \n  ")

    assert title == f"[Issue #{_ISSUE_IID}] 実装"


def test_build_merge_request_title_truncates_long_summary():
    long_summary = "あ" * 100

    title = build_merge_request_title(_ISSUE_IID, long_summary)

    assert title.startswith(f"[Issue #{_ISSUE_IID}] ")
    assert title.endswith("…")
    assert len(title) < len(long_summary) + len(f"[Issue #{_ISSUE_IID}] ")


def test_build_merge_request_description_includes_required_sections():
    description = build_merge_request_description(
        _ISSUE_IID,
        plan_document="# 概要\n実装計画の本文です。",
        summary="タスク1を実装した",
        assumed_uncertainties=[
            {
                "question": "エラーメッセージの文言は?",
                "severity": "minor",
                "assumption": "一般的な文言を使う",
            }
        ],
    )

    # 「対応Issue」「設計要約」「○○と仮定して実装した」を必須項目として含む(Issue #115本文)
    assert f"Closes #{_ISSUE_IID}" in description
    assert "## 設計要約" in description
    assert "実装計画の本文です。" in description
    assert "タスク1を実装した" in description
    assert "エラーメッセージの文言は?" in description
    assert "一般的な文言を使う" in description
    assert "と仮定して実装した" in description


def test_build_merge_request_description_handles_no_assumed_uncertainties():
    description = build_merge_request_description(
        _ISSUE_IID,
        plan_document="# 概要",
        summary="実装した",
        assumed_uncertainties=[],
    )

    assert "特になし" in description


def test_build_merge_request_description_handles_blank_plan_document_and_summary():
    description = build_merge_request_description(
        _ISSUE_IID,
        plan_document="  ",
        summary="",
        assumed_uncertainties=[],
    )

    assert "## 設計要約" in description
    assert "## 実装概要" in description
