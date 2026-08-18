"""実装計画フェーズが扱うデータの型。

`docs/architecture.md`のOrchestrator「実装計画」フェーズ(M4-7、Job種別`plan`)が
入力(設計フェーズの結果)として受け取るデータ(`PlanInput`)と、Claude Codeの応答から
組み立てる結果スキーマ(`PlanResult`)を定義する。`design/types.py`の`DesignInput`/
`DesignResult`と同じ設計方針(Claude Codeの応答をパースした結果だけを持ち、project/issue_iid等の
識別情報は持たない)。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..orchestrator.types import Uncertainty


@dataclass(frozen=True)
class PlanInput:
    """実装計画フェーズの入力。設計フェーズ(M4-6)完了時の`Job.result`から組み立てる。

    設計フェーズは要求・受入条件・前提を`design_document`という1つのMarkdown文書に
    統合済みのため、実装計画フェーズへは`design_document`のみを転送する
    (`assumed_uncertainties`は設計フェーズが確定させた前提の一覧で、`DesignInput`と同じ
    「既に確定した前提として蒸し返さずに使う」役割)。
    """

    design_document: str
    """設計フェーズが確定させた設計内容(Markdown、`docs/specs/template.md`のフォーマット)。"""

    assumed_uncertainties: tuple[str, ...]
    """設計フェーズのASSUME判定(および`respond`でのASK→回答解決)で確定した前提の一覧。"""


@dataclass(frozen=True)
class PlanTask:
    """実装可能な粒度に分解された1件のタスク。

    タプル(`PlanResult.tasks`)内の並び順がそのまま実装順序を表す(依存関係を表す専用
    フィールドは持たない、`prompts.build_plan_instructions`が「実装順に並べる」よう指示する)。
    """

    title: str
    """タスクの短い見出し。"""

    description: str
    """タスクの内容(何を実装するか、完了の目安)。"""


@dataclass(frozen=True)
class PlanResult:
    """Claude Codeの応答をパースした、1回の実装計画生成の結果。

    `uncertainties`は`orchestrator.judge_uncertainties`にそのまま渡す`Uncertainty`の列で、
    各要素の`phase`は`"plan"`固定になる(`parser.parse_plan_output`が設定する)。
    """

    plan_document: str
    """実装計画全体をまとめたMarkdown文書(タスク一覧・分解方針を含む)。"""

    tasks: tuple[PlanTask, ...]
    """実装可能な粒度に分解したタスクの一覧(実装順)。"""

    uncertainties: tuple[Uncertainty, ...]
    """タスク分解にあたって生じた不足情報(不明点)の一覧。`judge_uncertainties`で`ASK`/`ASSUME`を判定する対象。"""


__all__ = ["PlanInput", "PlanResult", "PlanTask"]
