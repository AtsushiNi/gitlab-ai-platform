"""実装計画フェーズが送出する例外の基底クラス。

`design/errors.py`と同じ方針: 具体的な判定ロジック(JSON抽出・検証)は実装(`parser.py`)の
責務とし、ここではインターフェースとして呼び出し側が握れる型だけを定義する。
"""

from __future__ import annotations


class PlanError(Exception):
    """実装計画フェーズ経由の処理が失敗したことを表す基底例外。"""


class PlanOutputParseError(PlanError):
    """Claude Codeの応答(`RunResult.result_text`)から結果スキーマを抽出できなかったことを表す。

    `design/errors.py`の`DesignOutputParseError`と同じ設計(`raw_text`から元の応答を確認できる
    ようにする)。プロンプト(`prompts.build_plan_instructions`)の指示にClaude Codeが従わなかった
    場合(JSONブロックが無い/JSONとして壊れている/スキーマを満たさない)に送出する。
    """

    def __init__(self, message: str, *, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


__all__ = ["PlanError", "PlanOutputParseError"]
