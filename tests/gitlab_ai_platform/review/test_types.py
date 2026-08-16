import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gitlab_ai_platform.review import (
    Finding,
    IndexEntry,
    ReviewComparison,
    ReviewPaths,
    ReviewResult,
    Severity,
)


def test_severity_values():
    assert Severity.CRITICAL == "critical"
    assert Severity.MAJOR == "major"
    assert Severity.MINOR == "minor"


def test_finding_is_frozen():
    finding = Finding(
        severity=Severity.MAJOR,
        file="src/app.py",
        line=10,
        rationale="r",
        suggestion="s",
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        finding.line = 20


def test_finding_allows_line_none():
    finding = Finding(
        severity=Severity.MINOR,
        file="src/app.py",
        line=None,
        rationale="r",
        suggestion="s",
    )

    assert finding.line is None


def test_review_result_is_frozen():
    result = ReviewResult(summary="ok", findings=())

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.summary = "changed"


def test_review_paths_holds_all_output_files():
    paths = ReviewPaths(
        dir=Path("/reviews/g/p/1/sha"),
        result_json=Path("/reviews/g/p/1/sha/result.json"),
        result_md=Path("/reviews/g/p/1/sha/result.md"),
        input_path=Path("/reviews/g/p/1/sha/input.md"),
        log_path=Path("/reviews/g/p/1/sha/run_log.json"),
    )

    assert paths.dir == Path("/reviews/g/p/1/sha")
    assert paths.result_json.name == "result.json"
    assert paths.result_md.name == "result.md"
    assert paths.input_path.name == "input.md"
    assert paths.log_path.name == "run_log.json"


def test_index_entry_holds_counts_and_metadata():
    reviewed_at = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    entry = IndexEntry(
        project="group/project",
        mr_iid=1,
        sha="abc123",
        reviewed_at=reviewed_at,
        result_dir="group/project/1/abc123",
        summary="特に指摘なし",
        critical_count=0,
        major_count=1,
        minor_count=2,
    )

    assert entry.project == "group/project"
    assert entry.reviewed_at == reviewed_at
    assert entry.critical_count == 0
    assert entry.major_count == 1
    assert entry.minor_count == 2


def test_review_comparison_is_frozen_and_holds_three_buckets():
    new_finding = Finding(
        severity=Severity.MAJOR, file="a.py", line=1, rationale="r1", suggestion="s1"
    )
    unresolved_finding = Finding(
        severity=Severity.MINOR, file="b.py", line=2, rationale="r2", suggestion="s2"
    )
    resolved_finding = Finding(
        severity=Severity.CRITICAL, file="c.py", line=3, rationale="r3", suggestion="s3"
    )
    comparison = ReviewComparison(
        new=(new_finding,),
        unresolved=(unresolved_finding,),
        resolved=(resolved_finding,),
    )

    assert comparison.new == (new_finding,)
    assert comparison.unresolved == (unresolved_finding,)
    assert comparison.resolved == (resolved_finding,)
    with pytest.raises(dataclasses.FrozenInstanceError):
        comparison.new = ()
