"""設計フェーズが扱うデータの型。

`docs/architecture.md`のOrchestrator「設計」フェーズ(M4-6、Job種別`design`)が
入力(要求分析フェーズの結果)として受け取るデータ(`DesignInput`)と、Claude Codeの応答から
組み立てる結果スキーマ(`DesignResult`)を定義する。`issue_analysis/types.py`の
`RequirementAnalysis`と同じ設計方針(Claude Codeの応答をパースした結果だけを持ち、
project/issue_iid等の識別情報は持たない)。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..orchestrator.types import Uncertainty


@dataclass(frozen=True)
class DesignInput:
    """設計フェーズの入力。要求分析フェーズ(M4-3)完了時の`Job.result`から組み立てる。

    `assumed_uncertainties`は要求分析(および`respond`によるASK→回答の解決後)が確定させた
    前提の一覧を、設計プロンプトに埋め込みやすい単純な文字列(例:「エラーメッセージの文言は? →
    一般的な文言を使う」)に整形したもの。設計フェーズはこれらを「既に確定した前提」として
    受け取り、蒸し返さずに設計を進める。
    """

    requirements: tuple[str, ...]
    """要求分析フェーズが確定させた要求事項。"""

    acceptance_criteria: tuple[str, ...]
    """要求分析フェーズが確定させた受入条件。"""

    assumptions: tuple[str, ...]
    """要求分析フェーズでClaude Codeが置いた前提。"""

    assumed_uncertainties: tuple[str, ...]
    """ASSUME判定(および`respond`でのASK→回答解決)で確定した前提の一覧。"""


@dataclass(frozen=True)
class DesignResult:
    """Claude Codeの応答をパースした、1回の設計の結果。

    `uncertainties`は`orchestrator.judge_uncertainties`にそのまま渡す`Uncertainty`の列で、
    各要素の`phase`は`"design"`固定になる(`parser.parse_design_output`が設定する)。
    """

    design_document: str
    """設計内容をまとめたMarkdown文書(`docs/specs/template.md`のフォーマットに従う)。"""

    uncertainties: tuple[Uncertainty, ...]
    """設計にあたって生じた不足情報(不明点)の一覧。`judge_uncertainties`で`ASK`/`ASSUME`を判定する対象。"""


__all__ = ["DesignInput", "DesignResult"]
