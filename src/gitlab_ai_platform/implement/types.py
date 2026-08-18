"""実装フェーズが扱うデータの型。

`docs/architecture.md`のOrchestrator「実装」フェーズ(M4-8、Job種別`implement`)が
入力(実装計画フェーズの結果)として受け取るデータ(`ImplementInput`)と、Claude Codeの応答から
組み立てる結果スキーマ(`ImplementResult`)を定義する。`plan/types.py`の`PlanInput`/`PlanResult`と
同じ設計方針(Claude Codeの応答をパースした結果だけを持ち、project/issue_iid等の識別情報は
持たない)。

`design`/`plan`と異なり、実装フェーズは実際にファイル編集・コミットを行う(ADR-0033)ため、
「コミットが実際に行われたか」は`ImplementResult`が持つ自己申告のフィールドではなく、
worktreeのHEAD commit shaを実行前後で比較する構造的なチェック(`implement/git_ops.py`)で
判定する。`ImplementResult.tests_passed`はあくまでClaude Code自身の自己申告であり、
`cli/dispatcher.py`の`build_implement_handler`はこれを唯一の判断根拠にはしない
(`docs/adr/0005-claude-code-runner-design.md`が確立した「result(自然文/自己申告)だけで
成否判定しない」という方針をここでも踏襲する)。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..orchestrator.types import Uncertainty
from ..plan.types import PlanTask


@dataclass(frozen=True)
class ImplementInput:
    """実装フェーズの入力。実装計画フェーズ(M4-7)完了時の`Job.result`から組み立てる。

    実装計画フェーズは`plan_document`(実装計画全体の文書)と`tasks`(実装順のタスク一覧)を
    確定させ済みのため、実装フェーズへはこれらと`assumed_uncertainties`のみを転送する
    (`plan/job.py`の`build_plan_job_payload`と同じ「前段の確定結果だけを転記する」パターン)。
    """

    plan_document: str
    """実装計画フェーズが確定させた実装計画文書(Markdown)。"""

    tasks: tuple[PlanTask, ...]
    """実装順に並べたタスクの一覧(`plan.types.PlanTask`をそのまま再利用する)。"""

    assumed_uncertainties: tuple[str, ...]
    """要求分析・設計・実装計画の各フェーズのASSUME判定で確定した前提の一覧。"""


@dataclass(frozen=True)
class ImplementResult:
    """Claude Codeの応答をパースした、1回の実装実行の結果。

    `uncertainties`は`orchestrator.judge_uncertainties`にそのまま渡す`Uncertainty`の列で、
    各要素の`phase`は`"implement"`固定になる(`parser.parse_implement_output`が設定する)。
    """

    summary: str
    """実装内容の要約(コミットメッセージ・MR説明の下書きに使うことを想定)。"""

    commit_message: str | None
    """Claude Codeが実際にcommitした際に使ったメッセージ。commitしなかった場合は`None`。"""

    tests_passed: bool
    """テストが通ったかどうかのClaude Code自身の自己申告。

    `build_implement_handler`はこれを唯一の根拠にはせず、worktreeのHEAD commit shaが
    実行前後で変化したか(=実際にcommitされたか)を構造的に確認する(`git_ops.read_head_sha`)。
    """

    uncertainties: tuple[Uncertainty, ...]
    """実装にあたって生じた不足情報(不明点)の一覧。`judge_uncertainties`で`ASK`/`ASSUME`を判定する対象。"""


__all__ = ["ImplementInput", "ImplementResult"]
