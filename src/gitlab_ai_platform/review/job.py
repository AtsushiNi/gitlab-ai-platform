"""review種別のJob(`job/`)のpayload/result構造。

方針(M3-1 [#91](https://github.com/AtsushiNi/gitlab-ai-platform/issues/91)、
`docs/adr/0016-job-abstraction.md`):

- Job抽象自体は`payload`/`result`を種別非依存の`dict[str, Any]`として扱う(ADR-0016)。
  `review`固有のフィールド(対象project/MR/commit、結果保存先パス)はJob抽象そのものには
  持たせず、このモジュール(呼び出し側)が定義する
- Jobの実行(`cli/single_run.py`の`execute_review_job`)からimportされる、payload/resultの
  組み立て・分解専用のヘルパー。`execute_review`本体(GitLab Adapter→Workspace Manager→
  Claude Code Runner→State Storeの結線)には関与しない
"""

from __future__ import annotations

from typing import Any

from ..job.protocol import JobType

# review種別のJobを表す値。cli側が`JobRepository.enqueue`へ渡す際にimportして使う
REVIEW_JOB_TYPE = JobType.REVIEW


def build_review_job_payload(
    project: str, mr_iid: int, *, sha: str | None = None
) -> dict[str, Any]:
    """review Jobの`payload`を組み立てる。

    `sha`省略時(単発実行でMR取得時点の最新commitを使う場合)はキー自体を含めない
    (`execute_review`の`sha: str | None = None`引数と対応させる)。
    """
    payload: dict[str, Any] = {"project": project, "mr_iid": mr_iid}
    if sha is not None:
        payload["sha"] = sha
    return payload


def review_job_payload_to_args(payload: dict[str, Any]) -> tuple[str, int, str | None]:
    """review Jobの`payload`から`execute_review`への引数`(project, mr_iid, sha)`を取り出す。"""
    return payload["project"], payload["mr_iid"], payload.get("sha")


def build_review_job_result(
    project: str, mr_iid: int, sha: str, result_path: str
) -> dict[str, Any]:
    """`execute_review`の実行結果からreview Jobの`result`を組み立てる。

    レビュー結果本体(JSON/Markdown)は`reviews/<project>/<mr_iid>/<sha>/`
    (`docs/specs/review-output.md`)に別途保存されるため、Job側にはその保存先パスのみを
    持たせる(State Storeの`result_path`と同じ設計、`docs/adr/0016-job-abstraction.md`)。
    """
    return {
        "project": project,
        "mr_iid": mr_iid,
        "sha": sha,
        "result_path": result_path,
    }


__all__ = [
    "REVIEW_JOB_TYPE",
    "build_review_job_payload",
    "build_review_job_result",
    "review_job_payload_to_args",
]
