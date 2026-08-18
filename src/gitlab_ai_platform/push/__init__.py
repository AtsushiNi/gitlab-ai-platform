"""push と MR 作成フェーズ(M4-9、Job種別`push`)。

実装フェーズ(`implement`)がworktree内にローカルcommitとして残した変更を、
`GitLabWriter.push_file_changes`(Commits API経由)でGitLab上へ実際に反映し、
`GitLabWriter.create_merge_request`でMRを作成する。`design`/`plan`/`implement`と同じ
「1フェーズ1Job種別」のパターンを踏襲するが、Claude Code Runnerを呼び出さない
(git diff計算とGitLab Adapter呼び出しのみの機械的な処理)という点でこれまでの5種類と
異なる(ADR-0034「論点3」)。

**無人実行トラック限定の機能。** 対話型トラック(VS Code拡張)では対話しながら直接pushする
ため、このフェーズ自体が不要。

実際にJobとして処理する部分(`JobHandler`)は`cli/dispatcher.py`の`build_push_handler`。
"""

from __future__ import annotations

from .errors import NoFileChangesError, PushError
from .git_ops import compute_commit_actions, resolve_push_base_sha
from .job import build_push_job_payload, build_push_job_result, push_job_payload_to_args
from .mr_template import build_merge_request_description, build_merge_request_title
from .types import PushInput

__all__ = [
    "NoFileChangesError",
    "PushError",
    "PushInput",
    "build_merge_request_description",
    "build_merge_request_title",
    "build_push_job_payload",
    "build_push_job_result",
    "compute_commit_actions",
    "push_job_payload_to_args",
    "resolve_push_base_sha",
]
