"""Issue Ticket Store が読み書きするデータの型。

`(project, issue_iid)` 単位で「無人実行Jobを起票済みかどうか」を記録するための型を定義する
(`docs/adr/0024-issue-poller-dedup.md`)。State Store(`store/types.py`)の`ReviewRecord`とは
異なり、レビューのような進行状態(`status`)は持たない。Issueの無人実行の進行状態は
Job(`job/protocol.py`の`JobStatus`)が単独で管理するため、ここでは「起票したという事実と
いつ起票したか」だけを記録する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class IssueTicketRecord:
    """`(project, issue_iid)` 単位で、無人実行Jobを起票済みであることを表すレコード。"""

    project: str
    issue_iid: int
    ticketed_at: datetime


__all__ = ["IssueTicketRecord"]
