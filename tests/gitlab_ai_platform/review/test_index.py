from datetime import UTC, datetime

from gitlab_ai_platform.review import IndexEntry, append_entry, read_index


def _entry(**overrides) -> IndexEntry:
    defaults = dict(
        project="group/project",
        mr_iid=1,
        sha="abc123",
        reviewed_at=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        result_dir="group/project/1/abc123",
        summary="特に指摘なし",
        critical_count=0,
        major_count=0,
        minor_count=0,
    )
    defaults.update(overrides)
    return IndexEntry(**defaults)


def test_read_index_returns_empty_tuple_when_file_missing(tmp_path):
    assert read_index(tmp_path) == ()


def test_append_then_read_round_trips(tmp_path):
    entry = _entry()

    append_entry(tmp_path, entry)

    assert read_index(tmp_path) == (entry,)


def test_append_preserves_order_across_multiple_entries(tmp_path):
    first = _entry(mr_iid=1, sha="sha1")
    second = _entry(mr_iid=2, sha="sha2")

    append_entry(tmp_path, first)
    append_entry(tmp_path, second)

    assert read_index(tmp_path) == (first, second)


def test_append_creates_root_directory_if_missing(tmp_path):
    root = tmp_path / "reviews"

    append_entry(root, _entry())

    assert root.exists()
    assert read_index(root) == (_entry(),)


def test_read_index_skips_malformed_trailing_line(tmp_path):
    # JSON Linesを選んだ理由(書き込み中のクラッシュ等で末尾が壊れても、直前までの
    # 行はそのまま読める)の回帰テスト。壊れた行だけをスキップし、例外は送出しない
    first = _entry(mr_iid=1, sha="sha1")
    append_entry(tmp_path, first)
    with (tmp_path / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"project": "group/project", "mr_iid": 2, "sha": "sha2"\n')  # 途中で切れた行

    assert read_index(tmp_path) == (first,)


def test_read_index_skips_line_missing_required_field(tmp_path):
    first = _entry(mr_iid=1, sha="sha1")
    append_entry(tmp_path, first)
    with (tmp_path / "index.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"project": "group/project"}\n')  # 必須フィールドが欠けている

    assert read_index(tmp_path) == (first,)
