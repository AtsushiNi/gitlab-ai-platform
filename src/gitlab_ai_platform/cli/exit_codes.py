"""CLIの終了コード定義。

方針(M1-10 [#38](https://github.com/AtsushiNi/gitlab-ai-platform/issues/38)、
`docs/adr/0008-cli-single-run-design.md`):

- `argparse`が引数エラー時に自動的に使う`2`と衝突しないよう、10番台をパイプラインの
  各段階(GitLab Adapter/Workspace/Runner/Review/State Store)専用に割り当てる。
- どの段階で失敗したかを終了コードだけで判別できるようにし、デバッグ・自動化スクリプトの
  両方から失敗箇所を特定しやすくする(「デバッグとプロンプト改善の主要導線」という
  M1-10の目的に合わせた設計)。
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_UNEXPECTED_ERROR = 1
# 2はargparseの引数エラーで使われるため空けておく
EXIT_CONFIG_ERROR = 10
EXIT_GITLAB_ADAPTER_ERROR = 11
EXIT_WORKSPACE_ERROR = 12
EXIT_RUNNER_ERROR = 13
EXIT_REVIEW_ERROR = 14
EXIT_STATE_STORE_ERROR = 15
EXIT_INTERRUPTED = 130

__all__ = [
    "EXIT_OK",
    "EXIT_UNEXPECTED_ERROR",
    "EXIT_CONFIG_ERROR",
    "EXIT_GITLAB_ADAPTER_ERROR",
    "EXIT_WORKSPACE_ERROR",
    "EXIT_RUNNER_ERROR",
    "EXIT_REVIEW_ERROR",
    "EXIT_STATE_STORE_ERROR",
    "EXIT_INTERRUPTED",
]
