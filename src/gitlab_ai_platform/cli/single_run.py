"""単発レビュー実行(デバッグ・プロンプト改善用)のパイプライン。

方針(M1-10 [#38](https://github.com/AtsushiNi/gitlab-ai-platform/issues/38)、
`docs/architecture.md`「データフロー(MVP)」2〜9、`docs/adr/0008-cli-single-run-design.md`):

- MR Poller(定期走査・複数MR横断)を経由せず、指定された1つのproject/MRに対して
  GitLab Adapter → Workspace Manager → Review(プロンプト) → Claude Code Runner →
  Review(パース・保存) → State Store を直接結線する。このリポジトリで最初の
  エンドツーエンド結線コード。
- `execute_review`はGitLab Adapter(`GitLabReader`)・Workspace Manager・Claude Code
  Runner・State Storeの4つをProtocol型の引数として受け取る「パイプライン本体」で、
  `MrPoller`(`poller/poller.py`)と同じくテスト時は手書きフェイクを注入できる。
  `run_single_review`はそれらの具象実装(REST/git/subprocess/SQLite)を`config`から
  組み立てる「合成ルート」で、CLI(`cli.main`)から呼ばれる想定。
- 各段階の例外(`GitLabAdapterError` / `WorkspaceError` / `RunnerError` / `ReviewError` /
  `StateStoreError`)は変換せずそのまま呼び出し側(`cli.main`)へ伝播させる。呼び出し側が
  終了コード・エラーメッセージへ変換する(このモジュール自身はCLI表示に関与しない)。
"""

from __future__ import annotations

import functools
import os
import subprocess
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..config import GITLAB_TOKEN_ENV_KEY, Config
from ..gitlab_adapter import GitLabRestAdapter
from ..gitlab_adapter.protocol import GitLabReader
from ..logging_ import get_logger
from ..review import (
    ReviewPaths,
    ReviewResult,
    build_review_instructions,
    parse_review_output,
    save_review,
)
from ..runner import ReviewContext, RunResult, SubprocessClaudeCodeRunner, build_prompt
from ..runner.protocol import ClaudeCodeRunner
from ..store import DuplicateReviewError, ReviewStatus, SqliteStateStore
from ..store.errors import StateStoreError
from ..store.protocol import StateStore
from ..workspace import GitWorkspaceManager
from ..workspace.protocol import WorkspaceManager

_logger = get_logger(__name__)

# GitLabのHTTPS認証は、PATを`.git/config`やコマンド引数に残さないよう、gitのcredential
# helperプロトコル(`get`要求に対して`username=`/`password=`を標準出力へ返す)経由で都度供給する
# (`references/spike-S3-git-worktree-windows.md` §8.1)。トークンの値そのものはこの文字列には
# 含めず、環境変数名だけを埋め込む(実際の値は`_build_workspace_manager`がsubprocessの
# 環境変数として注入する)。`!`で始まる値はgitがシェル経由で実行する
_CREDENTIAL_HELPER_TEMPLATE = '!f() {{ echo username=oauth2; echo "password=${var}"; }}; f'


@dataclass(frozen=True)
class SingleRunResult:
    """1回の単発レビュー実行の結果。CLIが標準出力に表示するサマリの元データ。"""

    project: str
    mr_iid: int
    sha: str
    worktree_path: Path
    review_result: ReviewResult
    review_paths: ReviewPaths
    run_result: RunResult


def run_single_review(
    config: Config,
    project: str,
    mr_iid: int,
    *,
    timeout_seconds: int | None = None,
    allowed_tools: Sequence[str] = (),
    disallowed_tools: Sequence[str] = (),
    permission_mode: str | None = None,
) -> SingleRunResult:
    """指定した`project`/`mr_iid`を1本レビューする(合成ルート)。

    `config`からGitLab Adapter・Workspace Manager・Claude Code Runner・State Storeの
    具象実装(REST/git/subprocess/SQLite)を組み立て、`execute_review`に委譲する。
    """
    adapter = GitLabRestAdapter(config.gitlab_url, config.gitlab_token)
    workspace = _build_workspace_manager(config)
    runner = SubprocessClaudeCodeRunner(config.runner_log_dir)
    store = SqliteStateStore(config.state_db_path)
    try:
        return execute_review(
            adapter,
            workspace,
            runner,
            store,
            config,
            project,
            mr_iid,
            timeout_seconds=timeout_seconds,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            permission_mode=permission_mode,
        )
    finally:
        store.close()


def execute_review(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
    project: str,
    mr_iid: int,
    *,
    timeout_seconds: int | None = None,
    allowed_tools: Sequence[str] = (),
    disallowed_tools: Sequence[str] = (),
    permission_mode: str | None = None,
) -> SingleRunResult:
    """指定した`project`/`mr_iid`を1本レビューする(パイプライン本体)。

    `adapter`/`workspace`/`runner`/`store`はいずれもProtocol型の引数として受け取り、
    具象実装(REST/git/subprocess/SQLite)には依存しない。State Storeには実行開始時点で
    `RUNNING`として記録し、Workspace Manager以降のいずれかの段階で例外が発生した場合は
    `FAILED`に更新してから例外を再送出する。全段階が成功した場合のみ`DONE`に更新する。
    `config`は`runner_log_dir`等ではなく`runner_timeout_seconds`/`reviews_root`の
    デフォルト値・保存先としてのみ使う(具象実装の構築は`run_single_review`の責務)。
    """
    # 3つとも独立したGitLab REST呼び出しなので、CLIの主用途(デバッグ時に繰り返し実行する)
    # で毎回の待ち時間を減らすため並列に取得する(逐次だと3回分のネットワーク往復が積み上がる)
    with ThreadPoolExecutor(max_workers=3) as executor:
        merge_request_future = executor.submit(adapter.get_merge_request, project, mr_iid)
        diffs_future = executor.submit(adapter.get_merge_request_diffs, project, mr_iid)
        discussions_future = executor.submit(
            adapter.list_merge_request_discussions, project, mr_iid
        )
        merge_request = merge_request_future.result()
        diffs = tuple(diffs_future.result())
        discussions = tuple(discussions_future.result())
    sha = merge_request.sha

    _ticket_running(store, project, mr_iid, sha)

    try:
        worktree = workspace.prepare(project, mr_iid, sha)

        context = ReviewContext(
            merge_request=merge_request, diffs=diffs, discussions=discussions
        )
        instructions = build_review_instructions()
        resolved_timeout = (
            timeout_seconds if timeout_seconds is not None else config.runner_timeout_seconds
        )

        run_result = runner.run(
            worktree.path,
            instructions,
            context,
            timeout_seconds=resolved_timeout,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            permission_mode=permission_mode,
        )
        review_result = parse_review_output(run_result)

        input_prompt = build_prompt(instructions, context)
        review_paths = save_review(
            config.reviews_root,
            project,
            mr_iid,
            sha,
            review_result,
            input_prompt=input_prompt,
            run_log_path=run_result.log_path,
        )

        # DONEへの更新も同じtry内に含める。ここが失敗した場合もFAILEDへの更新を
        # 試みる(成功していれば再実行不要だとわかるように、失敗していれば
        # 再実行が必要だとわかるように、RUNNINGのまま放置しない)
        store.update_status(
            project,
            mr_iid,
            sha,
            ReviewStatus.DONE,
            reviewed_at=datetime.now(UTC),
            result_path=str(review_paths.dir),
        )
    except Exception:
        # 起票(RUNNING)後に失敗した場合は、再実行時に状態が追えるようFAILEDへ更新してから
        # 元の例外をそのまま再送出する(CLIが終了コードへ変換する)。FAILEDへの更新自体が
        # 失敗しても(例: DB接続不良)、元の例外(RunnerError等)をStateStoreErrorで
        # 上書きせず、ログに残した上で元の例外を優先する
        try:
            store.update_status(project, mr_iid, sha, ReviewStatus.FAILED)
        except StateStoreError as update_exc:
            _logger.error(
                "single_run.failed_status_update_failed",
                extra={
                    "project": project,
                    "mr_iid": mr_iid,
                    "sha": sha,
                    "error": str(update_exc),
                },
            )
        raise

    return SingleRunResult(
        project=project,
        mr_iid=mr_iid,
        sha=sha,
        worktree_path=worktree.path,
        review_result=review_result,
        review_paths=review_paths,
        run_result=run_result,
    )


def _ticket_running(store: StateStore, project: str, mr_iid: int, sha: str) -> None:
    """`(project, mr_iid, sha)`を`RUNNING`として起票する。

    単発実行は同一commitへの再実行(プロンプト調整のたびに繰り返す運用)を想定しているため、
    既存レコードがあれば(MR Pollerのように無視するのではなく)`RUNNING`へ更新して実行を続ける。
    """
    try:
        store.create(project, mr_iid, sha, status=ReviewStatus.RUNNING)
    except DuplicateReviewError:
        store.update_status(project, mr_iid, sha, ReviewStatus.RUNNING)


def _clone_url_for(gitlab_url: str) -> Callable[[str], str]:
    def build(project: str) -> str:
        return f"{gitlab_url}/{project}.git"

    return build


def _credential_helper() -> str:
    return _CREDENTIAL_HELPER_TEMPLATE.format(var=GITLAB_TOKEN_ENV_KEY)


def _build_workspace_manager(config: Config) -> GitWorkspaceManager:
    token_env = {GITLAB_TOKEN_ENV_KEY: config.gitlab_token}
    run_with_token = functools.partial(subprocess.run, env={**os.environ, **token_env})

    return GitWorkspaceManager(
        config.workspace_root,
        _clone_url_for(config.gitlab_url),
        max_disk_bytes=config.workspace_max_disk_mb * 1024 * 1024,
        # 空値で既存のcredential.helper(実行環境の~/.gitconfig等に設定済みのものが
        # あれば)を一旦クリアしてから、PAT供給用のものだけを設定する。gitは
        # credential.helperを複数個「追加」していく仕組みのため、クリアしないと
        # 既存の設定が先に応答してPATが使われない可能性がある
        git_config=(("credential.helper", ""), ("credential.helper", _credential_helper())),
        run=run_with_token,
    )


__all__ = ["SingleRunResult", "run_single_review", "execute_review"]
