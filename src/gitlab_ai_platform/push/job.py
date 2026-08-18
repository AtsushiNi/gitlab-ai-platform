"""push種別のJob(`job/`)のpayload/result構造。

方針(M4-9 [#115](https://github.com/AtsushiNi/gitlab-ai-platform/issues/115)、ADR-0034):

- `implement`と同じく、pushフェーズを投入する自動化された検出器(Poller相当)はまだ存在しない
  (オーケストレーション本体はM4-10のスコープ)。そのため`build_push_job_payload`は
  `poller/`ではなく本パッケージに置く
- payloadは実装フェーズ完了時の`Job`から、**payload**(`implement.build_implement_job_payload`が
  組み立てたもの、`plan_document`を持つ)と**result**
  (`implement.build_implement_job_result`が組み立てたもの、`commit_sha`等を持つ)の
  両方を入力として組み立てる(ADR-0034「論点2」: `implement`のJob resultは`plan_document`を
  持たないため)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..gitlab_adapter.types import MergeRequest
from .types import PushInput


def build_push_job_payload(
    project: str,
    issue_iid: int,
    *,
    implement_payload: Mapping[str, Any],
    implement_result: Mapping[str, Any],
) -> dict[str, Any]:
    """`push`種別Jobの`payload`を組み立てる。

    `implement_payload`/`implement_result`は実装フェーズ完了時の`Job`の`payload`/`result`
    (それぞれ`implement.build_implement_job_payload`/`build_implement_job_result`が
    組み立てたもの)。`plan_document`は`implement_payload`から、それ以外(`commit_sha`等)は
    `implement_result`から転記する。
    """
    return {
        "project": project,
        "issue_iid": issue_iid,
        "plan_document": implement_payload.get("plan_document", ""),
        "summary": implement_result.get("summary", ""),
        "commit_message": implement_result.get("commit_message"),
        "commit_sha": implement_result["commit_sha"],
        "remote_branch": implement_result["remote_branch"],
        "local_branch": implement_result["local_branch"],
        "worktree_path": implement_result["worktree_path"],
        "assumed_uncertainties": list(
            implement_result.get("assumed_uncertainties", ())
        ),
    }


def push_job_payload_to_args(payload: Mapping[str, Any]) -> PushInput:
    """`push`種別Jobの`payload`から`PushInput`を組み立てる。

    `cli/dispatcher.py`の`build_push_handler`が利用する。
    """
    return PushInput(
        project=payload["project"],
        issue_iid=payload["issue_iid"],
        plan_document=payload.get("plan_document", ""),
        summary=payload.get("summary", ""),
        commit_message=payload.get("commit_message"),
        commit_sha=payload["commit_sha"],
        remote_branch=payload["remote_branch"],
        local_branch=payload["local_branch"],
        worktree_path=payload["worktree_path"],
        assumed_uncertainties=tuple(payload.get("assumed_uncertainties", ())),
    )


def build_push_job_result(
    project: str,
    issue_iid: int,
    *,
    pushed_commit_sha: str,
    merge_request: MergeRequest,
) -> dict[str, Any]:
    """pushフェーズのJob `result`を組み立てる。

    - `pushed_commit_sha`: `GitLabWriter.push_file_changes`が返す、GitLab上に新しく
      作成されたcommitのsha(worktree内のローカルcommit shaとは別物)
    - `merge_request_*`: `GitLabWriter.create_merge_request`が返す`MergeRequest`から
      呼び出し側が参照しやすいフィールドのみを転記する
    """
    return {
        "project": project,
        "issue_iid": issue_iid,
        "pushed_commit_sha": pushed_commit_sha,
        "remote_branch": merge_request.source_branch,
        "merge_request_iid": merge_request.iid,
        "merge_request_web_url": merge_request.web_url,
    }


__all__ = [
    "build_push_job_payload",
    "build_push_job_result",
    "push_job_payload_to_args",
]
