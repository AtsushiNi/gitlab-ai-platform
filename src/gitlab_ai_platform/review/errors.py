"""Review が送出する例外の基底クラス。

具体的な判定ロジック(JSON抽出・検証)は実装(`parser.py`)の責務。ここではインターフェースとして
呼び出し側が握れる型だけを定義する(`runner/errors.py`と同じ方針)。
"""

from __future__ import annotations


class ReviewError(Exception):
    """Review経由の処理が失敗したことを表す基底例外。"""


class ReviewOutputParseError(ReviewError):
    """Claude Codeの応答(`RunResult.result_text`)から結果スキーマを抽出できなかったことを表す。

    `docs/specs/claude-code-runner.md`の通り、Runnerは`result_text`の中身を一切解釈しない
    (自然文か構造化JSONかはRunnerにとって不透明)。そのため中身の解釈はReview(このモジュール)の
    責務であり、プロンプト(`prompts.build_review_instructions`)の指示にClaude Codeが従わなかった
    場合(JSONブロックが無い/JSONとして壊れている/スキーマを満たさない)はここで検出し、
    この例外として送出する。呼び出し側は`raw_text`から元の応答を確認し、State Storeを
    `FAILED`に遷移させる・人間に調査を促す等の判断ができる。
    """

    def __init__(self, message: str, *, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


__all__ = ["ReviewError", "ReviewOutputParseError"]
