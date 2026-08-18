"""設計フェーズが送出する例外の基底クラス。

`issue_analysis/errors.py`と同じ方針: 具体的な判定ロジック(JSON抽出・検証)は実装(`parser.py`)の
責務とし、ここではインターフェースとして呼び出し側が握れる型だけを定義する。
"""

from __future__ import annotations


class DesignError(Exception):
    """設計フェーズ経由の処理が失敗したことを表す基底例外。"""


class DesignOutputParseError(DesignError):
    """Claude Codeの応答(`RunResult.result_text`)から結果スキーマを抽出できなかったことを表す。

    `issue_analysis/errors.py`の`IssueAnalysisOutputParseError`と同じ設計(`raw_text`から
    元の応答を確認できるようにする)。プロンプト(`prompts.build_design_instructions`)の指示に
    Claude Codeが従わなかった場合(JSONブロックが無い/JSONとして壊れている/スキーマを
    満たさない)に送出する。
    """

    def __init__(self, message: str, *, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


__all__ = ["DesignError", "DesignOutputParseError"]
