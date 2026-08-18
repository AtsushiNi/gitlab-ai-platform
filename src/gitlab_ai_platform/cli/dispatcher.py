"""Runner Dispatcher: `JobRepository.claim`でJobを取り出し、別プロセスとして処理し続ける。

方針(M3-3 [#93](https://github.com/AtsushiNi/gitlab-ai-platform/issues/93)、
`docs/adr/0022-runner-process-separation.md`):

- `cli/single_run.py`の`execute_review_job`(`enqueue`直後に同一プロセス内で同期処理する経路)は
  変更しない。本モジュールはそれとは別の経路として、`JobRepository.claim`(M3-2、
  `docs/adr/0017-job-queue.md`)でJobを取り出して処理する常駐/単発実行のループを提供する。
  `cli/watch.py`(パイプライン本体+合成ルートを同じファイルに置く配置パターン)を踏襲する。
- Job受け渡しプロトコル: `JobType` → `JobHandler`(`Callable[[Job], dict[str, Any] | None]`)の
  ディスパッチテーブル(`build_job_handlers`)を組み立て、`RunnerDispatcher`はそのテーブルと
  `JobRepository`だけを知っていればよい(`review`固有のロジックを知らない)。M4で
  `issue-analysis`/`design`/`implement`用のhandlerを追加する際も`RunnerDispatcher`自体は
  無改修で済む。
- `review`種別の`JobHandler`(`build_review_handler`)は`execute_review`(GitLab Adapter→
  Workspace Manager→Claude Code Runner→Review→State Storeの結線本体、変更しない)をそのまま
  呼び出す。
- `job_types`省略時は`handlers`に登録済みの種別のみをclaim対象にする(未実装種別を誤って
  claimして即デッドレター化させないため)。
- Jobの処理中は専用のdaemonスレッドで一定間隔ごとに`heartbeat`を呼び、可視性タイムアウトの
  リースを延長する。
- `handler`が`NotImplementedError`を送出した場合(未対応のJobType、ADR-0016の契約)は
  `fail(..., retry=False)`で即座にデッドレター化する。それ以外の例外は`fail(..., retry=True)`
  とし、リトライ上限判定は`JobRepository`(ADR-0017の`attempts`/`max_attempts`)に委ねる。
  1件のJobの失敗は他のJobの処理を止めない(`watch`の「想定外の例外はプロセスを落とす」方針
  [ADR-0009]とは異なる。`worker`は無人・ヘッドレスな運用を前提とするため、ADR-0022参照)。
- 排他は`JobRepository.claim`のアトミックなUPDATE文(ADR-0017)に委ね、`ProcessLock`
  (`cli/lock.py`)は使わない。複数の`worker`プロセス(将来は複数ホスト)が同一`job_db_path`に
  対して同時に稼働することを前提とする設計そのものであるため(ADR-0022「決定」参照)。
- `handler`が`WaitingForHumanError`を送出した場合(M4-3
  [#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109)、ADR-0026)は、
  `fail`ではなく`JobRepository.wait_for_human`でJobを`WAITING_HUMAN`へ遷移させる。
  「未対応のJobType→`NotImplementedError`」と同じ、JobHandlerとRunnerDispatcherの間の
  契約として例外を使うパターン。
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .._paths import slugify_project
from ..config import Config
from ..design import (
    build_design_instructions,
    build_design_job_result,
    design_job_payload_to_args,
    parse_design_output,
)
from ..gitlab_adapter import GitLabApiError, GitLabRestAdapter
from ..gitlab_adapter.protocol import GitLabAdapter, GitLabReader
from ..implement import (
    ImplementationNotCommittedError,
    WorktreeState,
    build_implement_instructions,
    build_implement_job_result,
    implement_job_payload_to_args,
    parse_implement_output,
    read_worktree_state,
)
from ..issue_analysis import (
    build_issue_analysis_instructions,
    build_issue_analysis_job_result,
    parse_issue_analysis_output,
)
from ..job import SqliteJobRepository
from ..job.errors import JobError, LeaseLostError
from ..job.protocol import (
    DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    Job,
    JobRepository,
    JobType,
)
from ..logging_ import execution_id_scope, get_logger
from ..orchestrator import judge_uncertainties, requires_human
from ..plan import (
    build_plan_instructions,
    build_plan_job_result,
    parse_plan_output,
    plan_job_payload_to_args,
)
from ..poller.issue_poller import issue_analysis_job_payload_to_args
from ..review import (
    REVIEW_JOB_TYPE,
    build_review_job_result,
    review_job_payload_to_args,
)
from ..runner import IssueContext, SubprocessClaudeCodeRunner, build_issue_prompt
from ..runner.protocol import ClaudeCodeRunner
from ..store import build_state_store
from ..store.protocol import StateStore
from ..workspace.protocol import WorkspaceManager
from .single_run import build_workspace_manager, execute_review

_logger = get_logger(__name__)

# claimが空振り(処理対象なし)した際、次のclaimまで待つ既定秒数
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
# Job処理中にリースを延長するためheartbeatを呼ぶ既定間隔(秒)。
# DEFAULT_VISIBILITY_TIMEOUT_SECONDS(既定600秒)より十分短く保つ(ADR-0022)
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 120.0

# JobType→処理関数のディスパッチテーブルの値の型(ADR-0022「Job受け渡しプロトコル」)。
# `complete`にそのまま渡せる`result`(またはNone)を返す契約
JobHandler = Callable[[Job], "dict[str, Any] | None"]

# 実装フェーズ(M4-8, ADR-0033)でClaude Codeに許可するツール。このリポジトリで初めて
# Edit/Write/Bash(実際のファイル編集・シェルコマンド実行)を許可する。これまでの
# review/issue-analysis/design/planはいずれも読み取り・分析のみで、明示的なallowed_toolsを
# 渡していなかった(`docs/operations/security.md`参照)
_IMPLEMENT_ALLOWED_TOOLS: tuple[str, ...] = ("Edit", "Write", "Bash")
# git pushはこのフェーズのスコープ外(M4-9の責務)。`Bash`をallowedToolsで許可する以上、
# Claude Code自身がBash経由で`git push`を試みることを構造的に禁止できるわけではない
# (Adapter層の「メソッドとして存在しない」という保証とは強さが異なる。
# `docs/operations/security.md`「Claude Code Runner」参照)が、明示的に一段防御しておく
_IMPLEMENT_DISALLOWED_TOOLS: tuple[str, ...] = ("Bash(git push:*)",)
# headless実行のため、Edit/Write(allowedToolsに既に含む)の確認プロンプトを自動承認する
# モードにする。`--dangerously-skip-permissions`相当の全許可モード("bypassPermissions")は
# 使わない(既存方針、ADR-0005「`--dangerously-skip-permissions`は提供しない」)
_IMPLEMENT_PERMISSION_MODE = "acceptEdits"


class WaitingForHumanError(Exception):
    """JobHandlerが人間の確認を必要とすると判断したことを表す(M4-3, ADR-0026)。

    `NotImplementedError`(未対応のJobType、ADR-0016)と同じ、JobHandlerと
    `RunnerDispatcher`の間の契約として使う例外。`_process`はこの例外を捕捉すると
    `fail`ではなく`JobRepository.wait_for_human`でJobを`WAITING_HUMAN`へ遷移させる。
    `result`には`wait_for_human`にそのまま渡す辞書(質問一覧等、`build_issue_analysis_job_result`
    が組み立てたもの)を持つ。
    """

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("人間の確認が必要です(WAITING_HUMAN)")
        self.result = result


def build_review_handler(
    adapter: GitLabReader,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
) -> JobHandler:
    """review種別の`JobHandler`を組み立てる。

    `execute_review`本体(GitLab Adapter→Workspace Manager→Claude Code Runner→Review→
    State Storeの結線)は変更しない。`execute_review_job`(`cli/single_run.py`)と同じ
    payload分解・result組み立てロジックを再利用する。
    """

    def _handle(job: Job) -> dict[str, Any]:
        project, mr_iid, sha = review_job_payload_to_args(job.payload)
        result = execute_review(
            adapter, workspace, runner, store, config, project, mr_iid, sha=sha
        )
        return build_review_job_result(
            result.project, result.mr_iid, result.sha, str(result.review_paths.dir)
        )

    return _handle


def build_issue_analysis_handler(
    adapter: GitLabReader,
    runner: ClaudeCodeRunner,
    config: Config,
) -> JobHandler:
    """issue-analysis種別の`JobHandler`を組み立てる(M4-3, ADR-0026/0027)。

    `review`と異なりWorkspace Manager(worktree)を使わない。要求分析はIssue本文(タイトル・
    説明・ラベル)の読解のみを対象とし、リポジトリ探索を必要としない設計のため(ADR-0027)。
    Claude Codeの実行先(subprocessのcwd)には、Job処理の間だけ存在する一時ディレクトリを使い、
    処理後に破棄する。

    処理の流れ: `issue_analysis_job_payload_to_args`でpayloadを分解(`poller/issue_poller.py`と
    共有するpayload形式、重複実装しない)→`GitLabReader.get_issue`でIssue取得→
    `IssueContext`に詰める→要求分析用instructions(`build_issue_analysis_instructions`)と
    `build_issue_prompt`でプロンプトを組み立てる→`ClaudeCodeRunner.run_prompt`で実行→
    `parse_issue_analysis_output`で構造化→検出した不明点(`RequirementAnalysis.uncertainties`)を
    `judge_uncertainties`で判定する。`requires_human`が`True`(1件でも`ASK`判定がある)場合は
    `WaitingForHumanError`を送出し、`RunnerDispatcher._process`がJobを`WAITING_HUMAN`へ
    遷移させる。`False`の場合は通常のJob結果として辞書を返す(`complete`)。
    """

    def _handle(job: Job) -> dict[str, Any]:
        project, issue_iid = issue_analysis_job_payload_to_args(job.payload)
        issue = adapter.get_issue(project, issue_iid)
        context = IssueContext(issue=issue)
        prompt = build_issue_prompt(build_issue_analysis_instructions(), context)

        with tempfile.TemporaryDirectory(prefix="issue-analysis-") as workdir:
            run_result = runner.run_prompt(
                Path(workdir),
                prompt,
                log_key=f"{slugify_project(project)}/issue-{issue_iid}",
                timeout_seconds=config.runner_timeout_seconds,
            )

        analysis = parse_issue_analysis_output(run_result)
        judgments = judge_uncertainties(analysis.uncertainties)
        result = build_issue_analysis_job_result(
            project, issue_iid, analysis, judgments
        )
        if requires_human(judgments):
            raise WaitingForHumanError(result)
        return result

    return _handle


def build_design_handler(
    adapter: GitLabReader,
    runner: ClaudeCodeRunner,
    config: Config,
) -> JobHandler:
    """design種別の`JobHandler`を組み立てる(M4-6, ADR-0029)。

    `issue-analysis`と同じくWorkspace Manager(worktree)を使わない。設計フェーズは要求分析
    フェーズが確定させた要求・受入条件・前提のみを入力とし、対象プロジェクトの実際の
    リポジトリを探索しない設計とした(ADR-0029「決定」。Workspace Managerの
    `prepare(project, mr_iid, ref)`はMR番号とgit refの組を前提にしたシグネチャで、
    design(Issue単位)にそのまま使うとMRのworktreeとキー空間が衝突しうる問題があり、
    GitLab Adapterのdefault branch取得拡張を含む対応は本Issueのスコープを超えるため)。

    処理の流れ: `design_job_payload_to_args`でpayloadを分解(`project`/`issue_iid`/
    `DesignInput`)→`GitLabReader.get_issue`でIssue取得(見出し用)→`IssueContext`に詰める→
    設計用instructions(`build_design_instructions`)と`build_issue_prompt`でプロンプトを
    組み立てる→`ClaudeCodeRunner.run_prompt`で実行→`parse_design_output`で構造化→検出した
    不明点(`DesignResult.uncertainties`)を`judge_uncertainties`で判定する。`requires_human`が
    `True`(1件でも`ASK`判定がある)場合は`WaitingForHumanError`を送出し、
    `RunnerDispatcher._process`がJobを`WAITING_HUMAN`へ遷移させる。`False`の場合は通常の
    Job結果として辞書を返す(`complete`)。
    """

    def _handle(job: Job) -> dict[str, Any]:
        project, issue_iid, design_input = design_job_payload_to_args(job.payload)
        issue = adapter.get_issue(project, issue_iid)
        context = IssueContext(issue=issue)
        instructions = build_design_instructions(design_input)
        prompt = build_issue_prompt(instructions, context)

        with tempfile.TemporaryDirectory(prefix="design-") as workdir:
            run_result = runner.run_prompt(
                Path(workdir),
                prompt,
                log_key=f"{slugify_project(project)}/issue-{issue_iid}/design",
                timeout_seconds=config.runner_timeout_seconds,
            )

        design = parse_design_output(run_result)
        judgments = judge_uncertainties(design.uncertainties)
        result = build_design_job_result(project, issue_iid, design, judgments)
        if requires_human(judgments):
            raise WaitingForHumanError(result)
        return result

    return _handle


def build_plan_handler(
    adapter: GitLabReader,
    runner: ClaudeCodeRunner,
    config: Config,
) -> JobHandler:
    """plan種別の`JobHandler`を組み立てる(M4-7, ADR-0030)。

    `design`と同じくWorkspace Manager(worktree)を使わない。実装計画フェーズは設計フェーズが
    確定させた設計内容・前提のみを入力とし、対象プロジェクトの実際のリポジトリを探索しない
    設計とした(ADR-0030「決定」。理由はADR-0029が設計フェーズについて述べたものと同じ)。

    処理の流れ: `plan_job_payload_to_args`でpayloadを分解(`project`/`issue_iid`/`PlanInput`)→
    `GitLabReader.get_issue`でIssue取得(見出し用)→`IssueContext`に詰める→実装計画用
    instructions(`build_plan_instructions`)と`build_issue_prompt`でプロンプトを組み立てる→
    `ClaudeCodeRunner.run_prompt`で実行→`parse_plan_output`で構造化→検出した不明点
    (`PlanResult.uncertainties`)を`judge_uncertainties`で判定する。`requires_human`が`True`
    (1件でも`ASK`判定がある)場合は`WaitingForHumanError`を送出し、
    `RunnerDispatcher._process`がJobを`WAITING_HUMAN`へ遷移させる。`False`の場合は通常の
    Job結果として辞書を返す(`complete`)。
    """

    def _handle(job: Job) -> dict[str, Any]:
        project, issue_iid, plan_input = plan_job_payload_to_args(job.payload)
        issue = adapter.get_issue(project, issue_iid)
        context = IssueContext(issue=issue)
        instructions = build_plan_instructions(plan_input)
        prompt = build_issue_prompt(instructions, context)

        with tempfile.TemporaryDirectory(prefix="plan-") as workdir:
            run_result = runner.run_prompt(
                Path(workdir),
                prompt,
                log_key=f"{slugify_project(project)}/issue-{issue_iid}/plan",
                timeout_seconds=config.runner_timeout_seconds,
            )

        plan = parse_plan_output(run_result)
        judgments = judge_uncertainties(plan.uncertainties)
        result = build_plan_job_result(project, issue_iid, plan, judgments)
        if requires_human(judgments):
            raise WaitingForHumanError(result)
        return result

    return _handle


def _remote_branch_name_for_issue(issue_iid: int) -> str:
    """実装フェーズがGitLab上に作成する実装用branchの名前(M4-8, ADR-0033)。

    Workspace Managerのローカルbranch名(`issue-<iid>`、`workspace/git_workspace.py`)とは
    別の名前空間。こちらはGitLab上の実際のbranch名であり、M4-9(push)がpush先として使う。
    """
    return f"ai/issue-{issue_iid}"


def _ensure_remote_branch(
    adapter: GitLabAdapter, project: str, branch_name: str, ref: str
) -> None:
    """`branch_name`をGitLab上に作成する。既に存在する場合(retry等)は例外にせず続行する。

    `create_branch`は許可リストに載った書き込み操作(`docs/operations/security.md` §2.2)。
    GitLab APIは既存branch名を指定すると400を返すため、その場合のみ「既存のbranchを
    そのまま使う」と判断して続行する(それ以外のエラーは呼び出し元にそのまま伝播させる)。
    """
    try:
        adapter.create_branch(project, branch_name, ref)
    except GitLabApiError as exc:
        if exc.status_code == 400:
            _logger.info(
                "implement.remote_branch_already_exists",
                extra={"project": project, "branch_name": branch_name},
            )
            return
        raise


def build_implement_handler(
    adapter: GitLabAdapter,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    config: Config,
    *,
    read_worktree_state: Callable[[Path], WorktreeState] = read_worktree_state,
) -> JobHandler:
    """implement種別の`JobHandler`を組み立てる(M4-8, ADR-0033)。

    `design`/`plan`と異なり実際のworktree(`Workspace Manager.prepare_for_issue`、ADR-0031)を
    使う。処理の流れ:

    1. `implement_job_payload_to_args`でpayloadを分解(`project`/`issue_iid`/`ImplementInput`)
    2. `GitLabReader.get_issue`でIssue取得、`GitLabReader.get_default_branch`でdefault branchを
       解決(ADR-0032)
    3. `_ensure_remote_branch`でdefault branchを起点に実装用branch(`ai/issue-<iid>`)を
       GitLab上に作成する(`create_branch`、許可リストの書き込み操作)
    4. `Workspace Manager.prepare_for_issue`でその実装用branchのworktreeを用意する
    5. 実行前のworktreeの状態(HEAD commit sha)を`read_worktree_state`で記録する
    6. 実装用instructions(`build_implement_instructions`)と`build_issue_prompt`でプロンプトを
       組み立て、`ClaudeCodeRunner.run_prompt`で実際のworktree上でClaude Codeを実行する
       (`allowed_tools`/`disallowed_tools`/`permission_mode`は本モジュール冒頭の
       `_IMPLEMENT_*`定数、ADR-0033)
    7. 実行後のworktreeの状態を再度確認し、HEAD commit shaが変化していなければ
       (=実際にはローカルcommitが行われなかった、またはtestsが通らなかった場合)
       `ImplementationNotCommittedError`を送出する。Claude Codeの自己申告
       (`ImplementResult.tests_passed`)だけを信用しない(ADR-0005と同じ方針)。
       この例外は`RunnerDispatcher._process`の`except Exception`経路で`fail(..., retry=True)`
       となり、既存のJob再試行/デッドレター機構(ADR-0017)にそのまま乗る(ADR-0033
       「テスト失敗時の扱い」)
    8. commitが確認できた場合、検出した不明点(`ImplementResult.uncertainties`)を
       `judge_uncertainties`で判定する。`requires_human`が`True`の場合は
       `WaitingForHumanError`を送出し、`False`の場合は通常のJob結果を返す(`complete`)

    実装成功時、このhandlerは`Workspace Manager.discard_for_issue`を呼ばない
    (ローカルcommitをM4-9のpushフェーズが参照できるようにするため。ADR-0033参照)。
    """

    def _handle(job: Job) -> dict[str, Any]:
        project, issue_iid, implement_input = implement_job_payload_to_args(job.payload)
        issue = adapter.get_issue(project, issue_iid)
        default_branch = adapter.get_default_branch(project)
        remote_branch = _remote_branch_name_for_issue(issue_iid)
        _ensure_remote_branch(adapter, project, remote_branch, default_branch)

        worktree = workspace.prepare_for_issue(project, issue_iid, remote_branch)
        before = read_worktree_state(worktree.path)

        context = IssueContext(issue=issue)
        instructions = build_implement_instructions(implement_input)
        prompt = build_issue_prompt(instructions, context)

        run_result = runner.run_prompt(
            worktree.path,
            prompt,
            log_key=f"{slugify_project(project)}/issue-{issue_iid}/implement",
            timeout_seconds=config.runner_timeout_seconds,
            allowed_tools=_IMPLEMENT_ALLOWED_TOOLS,
            disallowed_tools=_IMPLEMENT_DISALLOWED_TOOLS,
            permission_mode=_IMPLEMENT_PERMISSION_MODE,
        )

        implement_result = parse_implement_output(run_result)
        after = read_worktree_state(worktree.path)

        if after.head_sha == before.head_sha or not after.is_clean:
            raise ImplementationNotCommittedError(
                f"project={project!r} issue_iid={issue_iid!r}: 実装フェーズの実行後も"
                "worktreeに新しいローカルcommitが確認できませんでした"
                f"(tests_passed={implement_result.tests_passed!r}, "
                f"is_clean={after.is_clean!r}, summary={implement_result.summary!r})"
            )

        judgments = judge_uncertainties(implement_result.uncertainties)
        result = build_implement_job_result(
            project,
            issue_iid,
            implement_result=implement_result,
            commit_sha=after.head_sha,
            remote_branch=remote_branch,
            local_branch=worktree.branch,
            worktree_path=str(worktree.path),
            judgments=judgments,
        )
        if requires_human(judgments):
            raise WaitingForHumanError(result)
        return result

    return _handle


def build_job_handlers(
    adapter: GitLabAdapter,
    workspace: WorkspaceManager,
    runner: ClaudeCodeRunner,
    store: StateStore,
    config: Config,
) -> dict[JobType, JobHandler]:
    """`JobType` → `JobHandler`のディスパッチテーブルを組み立てる(ADR-0022)。

    M4-8時点で`review`/`issue-analysis`/`design`/`plan`/`implement`が実装済み(M4のJobType
    予約4種+`plan`が出揃った)。`adapter`は`implement`が`create_branch`(書き込み)も必要と
    するため、M4-7までの`GitLabReader`から`GitLabAdapter`(読み取り+書き込み)へ型を広げた
    (呼び出し元`run_dispatcher`は元々`GitLabRestAdapter`という両方満たす具象実装を渡して
    いたため、実際の配線は変更していない)。
    """
    return {
        REVIEW_JOB_TYPE: build_review_handler(
            adapter, workspace, runner, store, config
        ),
        JobType.ISSUE_ANALYSIS: build_issue_analysis_handler(adapter, runner, config),
        JobType.DESIGN: build_design_handler(adapter, runner, config),
        JobType.PLAN: build_plan_handler(adapter, runner, config),
        JobType.IMPLEMENT: build_implement_handler(adapter, workspace, runner, config),
    }


class RunnerDispatcher:
    """`JobRepository.claim`でJobを取り出し、対応する`JobHandler`で処理するループ本体。

    `job_repo`/`handlers`ともにProtocol型・関数のマッピングのみに依存し、`review`固有の
    ロジックを知らない(ADR-0022)。
    """

    def __init__(
        self,
        job_repo: JobRepository,
        handlers: Mapping[JobType, JobHandler],
        *,
        worker_id: str,
        job_types: Sequence[JobType] | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    ) -> None:
        self._job_repo = job_repo
        self._handlers = dict(handlers)
        self._worker_id = worker_id
        # 未指定ならhandlersに登録済みの種別のみをclaim対象にする(未実装種別を誤って
        # claimしてデッドレター化させないため、ADR-0022「決定」参照)
        self._job_types: tuple[JobType, ...] = (
            tuple(job_types) if job_types is not None else tuple(self._handlers.keys())
        )
        self._poll_interval_seconds = poll_interval_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._visibility_timeout_seconds = visibility_timeout_seconds

    def run_once(self) -> bool:
        """1件Jobをclaimして処理する。claimできるJobが無ければ`False`を返す。"""
        job = self._job_repo.claim(
            self._worker_id,
            job_types=self._job_types or None,
            visibility_timeout_seconds=self._visibility_timeout_seconds,
        )
        if job is None:
            return False
        # 1Job分の実行ごとに新しいexecution_idを振る(同一workerプロセス内で複数Jobを
        # 順次処理してもログを実行単位で追跡できるようにするため、`cli/watch.py`と同じ方針)
        with execution_id_scope():
            self._process(job)
        return True

    def run_forever(self, stop_event: threading.Event) -> None:
        """`stop_event`がセットされるまで、claim→処理を繰り返す。

        claimが空振りした場合のみ`poll_interval_seconds`だけ待つ(ADR-0022「決定」)。
        """
        while not stop_event.is_set():
            processed = self.run_once()
            if not processed:
                stop_event.wait(self._poll_interval_seconds)

    def _process(self, job: Job) -> None:
        """1件のJobを処理し、結果に応じて`complete`/`fail`/`wait_for_human`を呼び分ける(ADR-0022/0026)。

        1件の失敗は他のJobの処理を止めない(例外を再送出しない)。`worker`は無人・ヘッドレスな
        運用を前提とするため、`watch`の「想定外の例外はプロセスを落とす」方針とは意図的に異なる。
        """
        handler = self._handlers.get(job.job_type)
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(job.id, heartbeat_stop),
            daemon=True,
            name=f"dispatcher-heartbeat-{job.id}",
        )
        heartbeat_thread.start()
        try:
            if handler is None:
                # ADR-0016: 未実装のJobTypeを受け取った場合はNotImplementedErrorを送出する契約
                raise NotImplementedError(f"未対応のJobTypeです: {job.job_type.value}")
            result = handler(job)
        except WaitingForHumanError as exc:
            # ADR-0026: JobHandlerが人間の確認が必要と判断した場合は、失敗ではなく
            # WAITING_HUMANへ遷移させる(complete/failと対になる経路)
            self._job_repo.wait_for_human(job.id, self._worker_id, result=exc.result)
            _logger.info(
                "dispatcher.job_waiting_human",
                extra={"job_id": job.id, "job_type": job.job_type.value},
            )
        except NotImplementedError as exc:
            # 未実装種別はリトライしても状況が変わらないため、即座にデッドレター化する
            self._job_repo.fail(job.id, self._worker_id, str(exc), retry=False)
            _logger.error(
                "dispatcher.job_type_not_implemented",
                extra={"job_id": job.id, "job_type": job.job_type.value},
            )
        except Exception as exc:  # noqa: BLE001 - 1件の失敗を他Jobから隔離するため
            self._job_repo.fail(job.id, self._worker_id, str(exc), retry=True)
            _logger.error(
                "dispatcher.job_failed",
                extra={
                    "job_id": job.id,
                    "job_type": job.job_type.value,
                    "error": str(exc),
                },
            )
        else:
            self._job_repo.complete(job.id, self._worker_id, result=result)
            _logger.info(
                "dispatcher.job_completed",
                extra={"job_id": job.id, "job_type": job.job_type.value},
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join()

    def _heartbeat_loop(self, job_id: str, stop_event: threading.Event) -> None:
        """`stop_event`がセットされるまで、一定間隔ごとにリースを延長する(ADR-0022)。"""
        while not stop_event.wait(self._heartbeat_interval_seconds):
            try:
                self._job_repo.heartbeat(
                    job_id,
                    self._worker_id,
                    visibility_timeout_seconds=self._visibility_timeout_seconds,
                )
            except LeaseLostError:
                # 既に可視性タイムアウトを超えて別workerに再取得された。これ以上延長を
                # 試みても意味がないため、ログのみでスレッドを終了する(handler本体は
                # 中断せず最後まで実行させる。State Storeのレコードを不整合な状態のまま
                # 放置しないため)
                _logger.warning(
                    "dispatcher.heartbeat_lease_lost", extra={"job_id": job_id}
                )
                return
            except JobError as exc:
                _logger.warning(
                    "dispatcher.heartbeat_failed",
                    extra={"job_id": job_id, "error": str(exc)},
                )


def default_worker_id() -> str:
    """`--worker-id`省略時に使う既定のworker識別子(`hostname:pid`)を組み立てる。"""
    return f"{socket.gethostname()}:{os.getpid()}"


def run_dispatcher(
    config: Config,
    *,
    stop_event: threading.Event | None = None,
    worker_id: str | None = None,
    job_types: Sequence[JobType] | None = None,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    visibility_timeout_seconds: int = DEFAULT_VISIBILITY_TIMEOUT_SECONDS,
    run_once: bool = False,
) -> None:
    """`config`から具象実装を組み立て、Runner Dispatcherを起動する(合成ルート、ADR-0022)。

    `run_single_review`/`run_watch`と同じ流儀で`GitLabRestAdapter`/`build_workspace_manager`/
    `SubprocessClaudeCodeRunner`/`StateStore`(M3-5、`build_state_store`)/`SqliteJobRepository`を
    組み立てる。複数の`worker`プロセス(別ホストも可)が同一`config.job_db_path`に対して同時に
    起動されることを前提とする設計のため、`watch`と異なり`ProcessLock`は使わない(排他は
    `JobRepository.claim`のアトミックなUPDATE文がプロセス/ホスト境界を越えて担保する)。
    """
    adapter = GitLabRestAdapter(config.gitlab_url, config.gitlab_token)
    workspace = build_workspace_manager(config)
    runner = SubprocessClaudeCodeRunner(config.runner_log_dir)
    store = build_state_store(config)
    job_repo = SqliteJobRepository(config.job_db_path)
    try:
        handlers = build_job_handlers(adapter, workspace, runner, store, config)
        dispatcher = RunnerDispatcher(
            job_repo,
            handlers,
            worker_id=worker_id if worker_id is not None else default_worker_id(),
            job_types=job_types,
            poll_interval_seconds=poll_interval_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            visibility_timeout_seconds=visibility_timeout_seconds,
        )
        if run_once:
            dispatcher.run_once()
        else:
            effective_stop_event = (
                stop_event if stop_event is not None else threading.Event()
            )
            dispatcher.run_forever(effective_stop_event)
    finally:
        job_repo.close()
        store.close()


__all__ = [
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "JobHandler",
    "RunnerDispatcher",
    "WaitingForHumanError",
    "build_design_handler",
    "build_implement_handler",
    "build_issue_analysis_handler",
    "build_job_handlers",
    "build_plan_handler",
    "build_review_handler",
    "default_worker_id",
    "run_dispatcher",
]
