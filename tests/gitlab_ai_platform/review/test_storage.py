import json
from datetime import UTC, datetime

import pytest

from gitlab_ai_platform.review import (
    Finding,
    ReviewError,
    ReviewResult,
    Severity,
    read_index,
    save_review,
)


def _result() -> ReviewResult:
    return ReviewResult(
        summary="重大な指摘が1件あります。",
        findings=(
            Finding(
                severity=Severity.CRITICAL,
                file="src/app.py",
                line=10,
                rationale="根拠",
                suggestion="提案",
            ),
            Finding(
                severity=Severity.MINOR,
                file="src/util.py",
                line=None,
                rationale="r2",
                suggestion="s2",
            ),
        ),
    )


def test_save_review_writes_expected_files_under_project_mr_sha(tmp_path):
    root = tmp_path / "reviews"
    log_source = tmp_path / "runner-log.json"
    log_source.write_text('{"command": []}', encoding="utf-8")

    paths = save_review(
        root,
        "group/project",
        42,
        "abcdef0123456789",
        _result(),
        input_prompt="## Merge Request\nTitle: x",
        run_log_path=log_source,
    )

    expected_dir = root / "group/project" / "42" / "abcdef0123456789"
    assert paths.dir == expected_dir
    assert paths.result_json == expected_dir / "result.json"
    assert paths.result_md == expected_dir / "result.md"
    assert paths.input_path == expected_dir / "input.md"
    assert paths.log_path == expected_dir / "run_log.json"
    for path in (paths.result_json, paths.result_md, paths.input_path, paths.log_path):
        assert path.exists()


def test_save_review_result_json_round_trips_findings(tmp_path):
    root = tmp_path / "reviews"
    log_source = tmp_path / "runner-log.json"
    log_source.write_text("{}", encoding="utf-8")

    paths = save_review(
        root,
        "group/project",
        1,
        "sha1",
        _result(),
        input_prompt="prompt",
        run_log_path=log_source,
    )

    data = json.loads(paths.result_json.read_text(encoding="utf-8"))
    assert data["summary"] == "重大な指摘が1件あります。"
    assert data["findings"][0]["severity"] == "critical"
    assert data["findings"][0]["file"] == "src/app.py"
    assert data["findings"][0]["line"] == 10
    assert data["findings"][1]["line"] is None


def test_save_review_copies_run_log_content(tmp_path):
    root = tmp_path / "reviews"
    log_source = tmp_path / "runner-log.json"
    log_source.write_text('{"stdout": "hello"}', encoding="utf-8")

    paths = save_review(
        root,
        "group/project",
        1,
        "sha1",
        _result(),
        input_prompt="prompt",
        run_log_path=log_source,
    )

    assert paths.log_path.read_text(encoding="utf-8") == '{"stdout": "hello"}'


def test_save_review_writes_input_prompt_verbatim(tmp_path):
    root = tmp_path / "reviews"
    log_source = tmp_path / "runner-log.json"
    log_source.write_text("{}", encoding="utf-8")

    paths = save_review(
        root,
        "group/project",
        1,
        "sha1",
        _result(),
        input_prompt="the full prompt text",
        run_log_path=log_source,
    )

    assert paths.input_path.read_text(encoding="utf-8") == "the full prompt text"


def test_save_review_appends_index_entry_with_severity_counts(tmp_path):
    root = tmp_path / "reviews"
    log_source = tmp_path / "runner-log.json"
    log_source.write_text("{}", encoding="utf-8")
    reviewed_at = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    save_review(
        root,
        "group/project",
        1,
        "sha1",
        _result(),
        input_prompt="prompt",
        run_log_path=log_source,
        reviewed_at=reviewed_at,
    )

    entries = read_index(root)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.project == "group/project"
    assert entry.mr_iid == 1
    assert entry.sha == "sha1"
    assert entry.reviewed_at == reviewed_at
    assert entry.result_dir == "group/project/1/sha1"
    assert entry.critical_count == 1
    assert entry.major_count == 0
    assert entry.minor_count == 1


def test_save_review_defaults_reviewed_at_to_now(tmp_path):
    root = tmp_path / "reviews"
    log_source = tmp_path / "runner-log.json"
    log_source.write_text("{}", encoding="utf-8")
    before = datetime.now(UTC)

    save_review(
        root,
        "group/project",
        1,
        "sha1",
        _result(),
        input_prompt="prompt",
        run_log_path=log_source,
    )

    after = datetime.now(UTC)
    entry = read_index(root)[0]
    assert before <= entry.reviewed_at <= after


def test_save_review_rejects_project_containing_path_traversal(tmp_path):
    root = tmp_path / "reviews"
    log_source = tmp_path / "runner-log.json"
    log_source.write_text("{}", encoding="utf-8")

    with pytest.raises(ReviewError):
        save_review(
            root,
            "../../etc",
            1,
            "sha1",
            _result(),
            input_prompt="prompt",
            run_log_path=log_source,
        )

    # rootの外には何も書き込まれていないこと
    assert not (tmp_path / "etc").exists()


def test_save_review_rejects_sha_containing_path_traversal(tmp_path):
    root = tmp_path / "reviews"
    log_source = tmp_path / "runner-log.json"
    log_source.write_text("{}", encoding="utf-8")

    with pytest.raises(ReviewError):
        save_review(
            root,
            "group/project",
            1,
            "../" * 10 + "escape",
            _result(),
            input_prompt="prompt",
            run_log_path=log_source,
        )

    assert not (tmp_path / "escape").exists()
