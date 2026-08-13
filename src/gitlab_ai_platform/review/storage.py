"""`reviews/<project>/<mr_iid>/<sha>/`への結果・実行ログ・入力プロンプトの保存。

`docs/architecture.md`のデータフロー7.(「Reviewモジュールがスキーマに整形し、JSON+Markdownを
`reviews/<project>/<mr_iid>/<sha>/`に保存」)と、M1-9(#37)の「結果・ログ・入力を保存」を担当する。
`project`はそのままディレクトリ階層として使う(`workspace`/`runner`のようなパーセントエンコード
スラッグ化はしない)。worktree(gitオブジェクトを含む)やRunnerの実行ログとは異なりWindowsの
`MAX_PATH`制限にシビアな大容量ディレクトリではないこと、`store.ReviewRecord.result_path`が
既に`"reviews/group/project/1/abc123"`のような素のprojectパスを想定していること(既存の
`tests/gitlab_ai_platform/store/test_sqlite.py`参照)から、可読性を優先した
(`docs/adr/0006-review-output-schema.md`)。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from ..logging_ import get_logger
from .errors import ReviewError
from .index import append_entry
from .markdown import render_markdown
from .types import IndexEntry, ReviewPaths, ReviewResult, Severity

_logger = get_logger(__name__)

_RESULT_JSON_NAME = "result.json"
_RESULT_MD_NAME = "result.md"
_INPUT_NAME = "input.md"
_LOG_NAME = "run_log.json"


def save_review(
    root: Path | str,
    project: str,
    mr_iid: int,
    sha: str,
    result: ReviewResult,
    *,
    input_prompt: str,
    run_log_path: Path,
    reviewed_at: datetime | None = None,
) -> ReviewPaths:
    """`result`を`<root>/<project>/<mr_iid>/<sha>/`へ保存し、索引に1行追記する。

    `input_prompt`はRunnerに渡した完成後のプロンプト全文(`runner.subprocess_runner._build_prompt`
    の戻り値)を想定する。`run_log_path`はRunnerが書き出した実行ログ(`RunResult.log_path`)を指し、
    このディレクトリ内にコピーして、レビュー結果と同じ場所から実行ログもたどれるようにする。
    """
    dest_dir = _resolve_dest_dir(root, project, mr_iid, sha)
    dest_dir.mkdir(parents=True, exist_ok=True)

    result_json_path = dest_dir / _RESULT_JSON_NAME
    result_json_path.write_text(
        json.dumps(_result_to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result_md_path = dest_dir / _RESULT_MD_NAME
    result_md_path.write_text(
        render_markdown(result, project=project, mr_iid=mr_iid, sha=sha), encoding="utf-8"
    )

    input_path = dest_dir / _INPUT_NAME
    input_path.write_text(input_prompt, encoding="utf-8")

    log_path = dest_dir / _LOG_NAME
    shutil.copyfile(run_log_path, log_path)

    paths = ReviewPaths(
        dir=dest_dir,
        result_json=result_json_path,
        result_md=result_md_path,
        input_path=input_path,
        log_path=log_path,
    )

    append_entry(
        root,
        _build_index_entry(
            project=project,
            mr_iid=mr_iid,
            sha=sha,
            result=result,
            dest_dir=dest_dir,
            root=root,
            reviewed_at=reviewed_at,
        ),
    )

    _logger.info(
        "review.saved",
        extra={
            "project": project,
            "mr_iid": mr_iid,
            "sha": sha,
            "findings": len(result.findings),
            "result_dir": str(dest_dir),
        },
    )
    return paths


def _resolve_dest_dir(root: Path | str, project: str, mr_iid: int, sha: str) -> Path:
    """`<root>/<project>/<mr_iid>/<sha>`を組み立て、`root`の外へ出ないことを保証する。

    `project`/`sha`は現状GitLab APIのレスポンス由来・config.tomlの設定由来で信頼できるが、
    将来より信頼できない経路(webhook等)からも渡されうるため、".."や絶対パスによって
    `root`の外に書き込んでしまわないことを機構として保証しておく(プロンプト上の約束事に
    頼らないというこのリポジトリ全体の設計方針と同じ考え方)。
    """
    root_resolved = Path(root).resolve()
    dest_dir = (root_resolved / project / str(mr_iid) / sha).resolve()
    if not dest_dir.is_relative_to(root_resolved):
        raise ReviewError(
            f"project/shaの値が不正です(保存先が{root_resolved!s}の外に出ます): "
            f"project={project!r}, sha={sha!r}"
        )
    return dest_dir


def _result_to_dict(result: ReviewResult) -> dict:
    return {
        "summary": result.summary,
        "findings": [
            {**asdict(finding), "severity": finding.severity.value} for finding in result.findings
        ],
    }


def _build_index_entry(
    *,
    project: str,
    mr_iid: int,
    sha: str,
    result: ReviewResult,
    dest_dir: Path,
    root: Path | str,
    reviewed_at: datetime | None,
) -> IndexEntry:
    counts = {severity: 0 for severity in Severity}
    for finding in result.findings:
        counts[finding.severity] += 1

    return IndexEntry(
        project=project,
        mr_iid=mr_iid,
        sha=sha,
        reviewed_at=reviewed_at if reviewed_at is not None else datetime.now(UTC),
        result_dir=dest_dir.relative_to(Path(root)).as_posix(),
        summary=result.summary,
        critical_count=counts[Severity.CRITICAL],
        major_count=counts[Severity.MAJOR],
        minor_count=counts[Severity.MINOR],
    )


__all__ = ["save_review"]
