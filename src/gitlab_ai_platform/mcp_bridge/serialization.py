"""GitLab Adapter の dataclass 戻り値を、MCPツールのJSON安全な戻り値に変換するヘルパー。

対話型Claude Code(MCPクライアント)側はGitLab Adapterのdataclass型を知らないため、
`dataclasses.asdict`相当の再帰的な辞書変換を行う。`gitlab_adapter/types.py`の型は
すべて`frozen=True`のdataclassで、Enumを持つのは`CommitAction.action`
(`push_file_changes`の入力側)のみで戻り値側には登場しないため、ここでは
Enumの変換までは行わない(必要になったら`push_file_changes`用の入力変換
(`tools.py`の`_parse_commit_action`)側で対応する)。
"""

from __future__ import annotations

import dataclasses
from typing import Any


def to_jsonable(value: Any) -> Any:
    """dataclass(ネスト・tupleを含む)をJSONで表現可能な組み込み型に変換する。"""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    return value


__all__ = ["to_jsonable"]
