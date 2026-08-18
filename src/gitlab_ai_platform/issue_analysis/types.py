"""要求分析フェーズが扱うデータの型。

`docs/architecture.md`のOrchestrator「要求分析」フェーズ(M4-3、Job種別`issue-analysis`)が
Claude Codeの応答から組み立てる結果スキーマを定義する。`review/types.py`の`ReviewResult`と
同じ設計方針(Claude Codeの応答をパースした結果だけを持ち、project/issue_iid等の識別情報は
持たない)。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..orchestrator.types import Uncertainty


@dataclass(frozen=True)
class RequirementAnalysis:
    """Claude Codeの応答をパースした、1回の要求分析の結果。

    `uncertainties`は`orchestrator.judge_uncertainties`にそのまま渡す`Uncertainty`の列で、
    各要素の`phase`は`"issue-analysis"`固定になる(`parser.parse_issue_analysis_output`が設定する)。
    """

    requirements: tuple[str, ...]
    """Issueから読み取った要求事項。"""

    acceptance_criteria: tuple[str, ...]
    """受入条件。"""

    assumptions: tuple[str, ...]
    """Claude Codeが要求分析にあたって置いた前提(Issue本文に明記されていない解釈等)。"""

    uncertainties: tuple[Uncertainty, ...]
    """不足情報(不明点)の一覧。`judge_uncertainties`で`ASK`/`ASSUME`を判定する対象。"""


__all__ = ["RequirementAnalysis"]
