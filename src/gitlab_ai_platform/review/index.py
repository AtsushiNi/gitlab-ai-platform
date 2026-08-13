"""複数レビューを横断する一覧用の索引。

`<reviews root>/index.jsonl`にレビュー1回につき1行、JSON Lines形式で追記する。
`docs/adr/0006-review-output-schema.md`の通り、単一のJSON配列ファイルではなくJSON Linesを
選んでいる(追記のたびに全件を読み直して書き直す必要がなく、書き込み中のクラッシュで
壊れても直前までの行は読める)。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .types import IndexEntry

_INDEX_FILE_NAME = "index.jsonl"


def append_entry(root: Path | str, entry: IndexEntry) -> None:
    """索引に`entry`を1行追記する。"""
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    with (root_path / _INDEX_FILE_NAME).open("a", encoding="utf-8") as f:
        f.write(json.dumps(_entry_to_dict(entry), ensure_ascii=False) + "\n")


def read_index(root: Path | str) -> tuple[IndexEntry, ...]:
    """索引の全件を、追記順(古い順)で返す。索引ファイルが無ければ空を返す。"""
    index_path = Path(root) / _INDEX_FILE_NAME
    if not index_path.exists():
        return ()

    with index_path.open(encoding="utf-8") as f:
        return tuple(_entry_from_dict(json.loads(line)) for line in f if line.strip())


def _entry_to_dict(entry: IndexEntry) -> dict:
    return {
        "project": entry.project,
        "mr_iid": entry.mr_iid,
        "sha": entry.sha,
        "reviewed_at": entry.reviewed_at.isoformat(),
        "result_dir": entry.result_dir,
        "summary": entry.summary,
        "critical_count": entry.critical_count,
        "major_count": entry.major_count,
        "minor_count": entry.minor_count,
    }


def _entry_from_dict(data: dict) -> IndexEntry:
    return IndexEntry(
        project=data["project"],
        mr_iid=data["mr_iid"],
        sha=data["sha"],
        reviewed_at=datetime.fromisoformat(data["reviewed_at"]),
        result_dir=data["result_dir"],
        summary=data["summary"],
        critical_count=data["critical_count"],
        major_count=data["major_count"],
        minor_count=data["minor_count"],
    )


__all__ = ["append_entry", "read_index"]
