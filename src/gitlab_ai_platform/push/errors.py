"""push と MR 作成フェーズが送出する例外の基底クラス。

`implement/errors.py`と同じ方針: 具体的な判定ロジック(diff抽出・GitLab API呼び出し)は
実装(`git_ops.py`/`job.py`)の責務とし、ここでは呼び出し側が握れる型だけを定義する。
"""

from __future__ import annotations


class PushError(Exception):
    """push と MR 作成フェーズ経由の処理が失敗したことを表す基底例外。"""


class NoFileChangesError(PushError):
    """base(`resolve_push_base_sha`が返すcommit)とpush対象commitの間に、変更された
    ファイルが1件も検出されなかったことを表す(ADR-0034)。

    `implement`フェーズ(ADR-0033)がHEAD commit shaの変化を確認済みのため通常は
    起こらないはずだが、`push_file_changes`に空の`actions`を渡すことを防ぐための
    構造的なガードとして送出する。
    """


__all__ = ["NoFileChangesError", "PushError"]
