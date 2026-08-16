"""複数レビューを横断する一覧用の索引。

`<reviews root>/index.jsonl`にレビュー1回につき1行、JSON Lines形式で追記する。
`docs/adr/0006-review-output-schema.md`の通り、単一のJSON配列ファイルではなくJSON Linesを
選んでいる(追記のたびに全件を読み直して書き直す必要がなく、書き込み中のクラッシュで
壊れても直前までの行は読める)。

並列レビュー実行(M2-1 [#80](https://github.com/AtsushiNi/gitlab-ai-platform/issues/80)、
`docs/adr/0015-parallel-review-execution.md`)以降、複数のワーカースレッドが同じ`index.jsonl`に
同時に`append_entry`しうる。OSの`O_APPEND`書き込みの原子性はプラットフォーム依存(特に
Windowsでは複数ハンドルからの同時追記で行が混ざりうる)で当てにできないため、プロセス内の
`threading.Lock`で明示的に直列化する(複数プロセスからの同時書き込みは`cli.lock.ProcessLock`が
別途防いでいるため、プロセス内の排他だけで十分)。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from ..logging_ import get_logger
from .types import IndexEntry

_logger = get_logger(__name__)

_INDEX_FILE_NAME = "index.jsonl"
_write_lock = threading.Lock()


def append_entry(root: Path | str, entry: IndexEntry) -> None:
    """索引に`entry`を1行追記する。"""
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    with _write_lock, (root_path / _INDEX_FILE_NAME).open("a", encoding="utf-8") as f:
        f.write(json.dumps(_entry_to_dict(entry), ensure_ascii=False) + "\n")


def read_index(root: Path | str) -> tuple[IndexEntry, ...]:
    """索引の全件を、追記順(古い順)で返す。索引ファイルが無ければ空を返す。

    書き込み中のクラッシュ等で末尾の行が壊れていても、そこで全体の読み込みを諦めず
    直前までの行はそのまま返す(このモジュールがJSON Linesを選んだ理由そのもの)。
    壊れていた行はスキップし、警告ログに残す。
    """
    index_path = Path(root) / _INDEX_FILE_NAME
    if not index_path.exists():
        return ()

    entries = []
    with index_path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                entries.append(_entry_from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError, ValueError):
                _logger.warning(
                    "review.index_entry_skipped",
                    extra={"index_path": str(index_path), "line": lineno},
                )
    return tuple(entries)


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
