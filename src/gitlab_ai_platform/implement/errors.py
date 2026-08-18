"""実装フェーズが送出する例外の基底クラス。

`design/errors.py`/`plan/errors.py`と同じ方針: 具体的な判定ロジック(JSON抽出・検証、
HEAD commit shaの確認)は実装(`parser.py`/`git_ops.py`/`job.py`)の責務とし、ここでは
インターフェースとして呼び出し側が握れる型だけを定義する。
"""

from __future__ import annotations


class ImplementError(Exception):
    """実装フェーズ経由の処理が失敗したことを表す基底例外。"""


class ImplementOutputParseError(ImplementError):
    """Claude Codeの応答(`RunResult.result_text`)から結果スキーマを抽出できなかったことを表す。

    `design/errors.py`の`DesignOutputParseError`と同じ設計(`raw_text`から元の応答を確認できる
    ようにする)。プロンプト(`prompts.build_implement_instructions`)の指示にClaude Codeが
    従わなかった場合(JSONブロックが無い/JSONとして壊れている/スキーマを満たさない)に送出する。
    """

    def __init__(self, message: str, *, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


class ImplementationNotCommittedError(ImplementError):
    """実装フェーズの実行後もworktreeのHEAD commit shaが変化しなかった(=ローカルcommitが
    実際には行われなかった)ことを表す(ADR-0033「テスト失敗時の扱い」)。

    Claude Codeの自己申告(`ImplementResult.tests_passed`等)だけを根拠にせず、worktreeの
    実際の状態(`git_ops.read_head_sha`)を構造的に確認した結果として送出する。
    `cli/dispatcher.py`の`RunnerDispatcher._process`はこの例外を他の想定外例外と同様に扱い
    (`fail(..., retry=True)`)、`job/protocol.py`の既存リトライ/デッドレター機構
    (ADR-0017)にそのまま乗せる。専用のリトライ機構をこのフェーズのために新設しない。
    """


__all__ = [
    "ImplementError",
    "ImplementOutputParseError",
    "ImplementationNotCommittedError",
]
