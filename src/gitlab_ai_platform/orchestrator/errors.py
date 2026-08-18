"""Orchestrator(判断ロジック)が送出する例外。

具体的なエラー分類は最小限とし、呼び出し側の実装ミスを早期に検知するためだけに使う
(`store/errors.py`・`job/errors.py` と同じ方針)。
"""

from __future__ import annotations


class OrchestratorError(Exception):
    """Orchestratorパッケージ経由の操作が失敗したことを表す基底例外。"""


class MissingAssumptionError(OrchestratorError):
    """`severity=MINOR`(仮定して継続)なのに`Uncertainty.assumption`が指定されていないことを表す。

    MRに残す「○○と仮定して実装した」の文言の元になる情報が無いままASSUME判定を
    返してしまうと、M4-9でのMR本文組み立てが破綻するため、判定時点で早期に検知する。
    """


__all__ = ["MissingAssumptionError", "OrchestratorError"]
