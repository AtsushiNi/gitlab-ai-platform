# push と MR 作成フェーズ(push)

- 実装場所: `src/gitlab_ai_platform/push/`(Job種別への配線は`cli/dispatcher.py`)
- 対応Issue: [#115](https://github.com/AtsushiNi/gitlab-ai-platform/issues/115) (M4-9)
- 関連ADR: [ADR-0031](../adr/0031-issue-workspace.md)(Workspace ManagerのIssue単位worktree対応、
  `discard_for_issue`)、[ADR-0033](../adr/0033-implement-phase.md)(実装フェーズ、本フェーズの
  入力元)、[ADR-0034](../adr/0034-push-and-mr-phase.md)(本フェーズの設計判断: diffのbase決定、
  MR本文の情報源、JobType新設の是非、worktree後片付け)
- ステータス: 実装済み

## 責務

実装フェーズ(M4-8、`implement`)がIssue単位のworktree内に残したローカルcommitを、
`GitLabWriter.push_file_changes`(Commits API経由)でGitLabへ実際に反映し、
`GitLabWriter.create_merge_request`でMRを作成するJobフェーズ。Job種別`push`
(`job/protocol.py`の`JobType.PUSH`)として、`RunnerDispatcher`(M3-3、`cli/dispatcher.py`)の
`JobHandler`(`build_push_handler`)で処理する。無人実行トラック(Issue→MR)でMRを作成する
ステップで、このフェーズで初めてGitLabへの実際の書き込み(push・MR作成)が発生する。
M4-11(ADR-0036)以降、このフェーズの完了後に自動で`review`Jobが投入され、作成したMRの
一次チェックが行われる(`docs/specs/orchestrator.md`)。

**無人実行トラック限定の機能。** 対話型トラック(VS Code拡張)では人間が直接pushするため、
このフェーズ自体は使われない。

`review`/`issue-analysis`/`design`/`plan`/`implement`と異なり、このフェーズは
`ClaudeCodeRunner`を呼び出さない。git diff計算(`push/git_ops.py`)とGitLab Adapter呼び出しの
みで完結する機械的な処理のため(ADR-0034「論点3」)。

## 前提と非対象

- 前提:
  - 処理対象のJobは、実装フェーズ(M4-8)完了時の`Job`の`payload`
    (`implement.build_implement_job_payload`が組み立てたもの)と`result`
    (`implement.build_implement_job_result`/`build_resolved_implement_job_result`が
    組み立てたもの)の**両方**を入力として`push.build_push_job_payload`で組み立てて
    `JobRepository.enqueue`されたもの。投入者(「implement完了 → push投入」の橋渡し)自体は
    M4-10(Issue→MRパイプラインのオーケストレーション)のスコープで、本パッケージには
    含まない。push完了後の「push完了 → review投入」の橋渡しも同様にM4-11
    (`docs/adr/0036-self-review-connection.md`)のスコープで本パッケージには含まない
  - 実装フェーズが作成したIssue単位のworktree(`Workspace Manager.prepare_for_issue`、
    ADR-0031)が、pushフェーズの実行時点でまだ`worktree_path`に存在していること
    (`implement`は実装成功時に`discard_for_issue`を呼ばない設計のため保証される、ADR-0033)
  - 対象プロジェクトのdefault branchは`GitLabReader.get_default_branch`(ADR-0032)で
    実行時に再解決する(実装フェーズが解決した時点から変わっている可能性を許容するため)
- 非対象:
  - **実際の`git push`**。`GitLabWriter.push_file_changes`はGitLab Commits API経由のファイル
    単位create/update/deleteであり、`git push`そのものではない(既存Adapterの制約、
    `gitlab_adapter/protocol.py`)
  - **worktree内のローカルcommitをそのままGitLabへ転送すること**。Commits API経由のため、
    push後にGitLab上へ作られるcommitのshaはworktree内のローカルcommit shaとは異なる
    (`push.job.build_push_job_result`の`pushed_commit_sha`)
  - **`WAITING_HUMAN`への状態遷移**。本フェーズは機械的な処理であり、人間の判断を要する
    不明点を生成しない。`WaitingForHumanError`を送出せず、`cli/respond.py`の
    `_RESULT_RESOLVERS`にも登録しない(ADR-0034「論点3」)
  - **MRのマージ**。`GitLabWriter`に`merge`操作は存在しない(許可リスト方式、
    `gitlab_adapter/protocol.py`)
  - 「implement完了 → push投入」の橋渡し(Job間の連携)。`push.build_push_job_payload`という
    組み立て関数は用意するが、実際にいつ・誰が呼ぶかはM4-10のスコープ

## 公開インターフェース

実装場所: `src/gitlab_ai_platform/push/`(`types.py` / `git_ops.py` / `mr_template.py` /
`errors.py` / `job.py`)。`src/gitlab_ai_platform/push/__init__.py`から再エクスポート。

```python
def resolve_push_base_sha(
    worktree_path: Path,
    default_branch: str,
    commit_sha: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str:
    """`commit_sha`と`origin/<default_branch>`の共通の祖先(diffのbase)を返す(ADR-0034「論点1」)。"""


def compute_commit_actions(
    worktree_path: Path,
    default_branch: str,
    commit_sha: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[CommitAction]:
    """`resolve_push_base_sha`のbaseから`commit_sha`までの変更を`CommitAction`の配列にする。"""


def build_merge_request_title(issue_iid: int, summary: str) -> str:
    """MRタイトルを組み立てる。`summary`の先頭行を短く切り詰めて使う決定的な処理。"""


def build_merge_request_description(
    issue_iid: int,
    *,
    plan_document: str,
    summary: str,
    assumed_uncertainties: Sequence[Mapping[str, Any]],
) -> str:
    """MR本文(Markdown)を組み立てる。「対応Issue」「設計要約」「○○と仮定して実装した」を
    必須項目として含む(Issue #115本文の要求)。"""


def build_push_job_payload(
    project: str,
    issue_iid: int,
    *,
    implement_payload: Mapping[str, Any],
    implement_result: Mapping[str, Any],
) -> dict[str, Any]:
    """実装フェーズ完了時のJobの`payload`/`result`から`push`種別Jobのpayloadを組み立てる。"""


def push_job_payload_to_args(payload: Mapping[str, Any]) -> PushInput:
    """`push`種別Jobのpayloadから`PushInput`を組み立てる。"""


def build_push_job_result(
    project: str,
    issue_iid: int,
    *,
    pushed_commit_sha: str,
    merge_request: MergeRequest,
) -> dict[str, Any]:
    """pushフェーズのJob resultを組み立てる。"""
```

`JobHandler`本体(実際にJobとして処理する部分)は`src/gitlab_ai_platform/cli/dispatcher.py`の
`build_push_handler`:

```python
def build_push_handler(
    adapter: GitLabAdapter,
    workspace: WorkspaceManager,
    *,
    compute_commit_actions: Callable[
        [Path, str, str], list[CommitAction]
    ] = compute_commit_actions,
) -> JobHandler:
    """push種別の`JobHandler`を組み立てる。`ClaudeCodeRunner`を引数に取らない
    (これまでの5種類と異なり、Runnerを使わない初めてのhandler)。"""
```

`adapter`は`GitLabReader`(`get_default_branch`)と`GitLabWriter`
(`push_file_changes`/`create_merge_request`)の両方を必要とするため`GitLabAdapter`型を受け取る
(`implement`と同じ理由)。`build_job_handlers`(`cli/dispatcher.py`)が`JobType.PUSH`に
対応付けてディスパッチテーブルへ登録する。

## 入出力スキーマ

### `PushInput`(`push/types.py`)

pushフェーズの入力。実装フェーズ完了時のJobの`payload`/`result`両方から組み立てる。

| フィールド | 型 | 補足 |
|---|---|---|
| `project` | `str` | 対象プロジェクトパス |
| `issue_iid` | `int` | 対象IssueのIID |
| `plan_document` | `str` | `implement`のJob **payload**由来。MR本文の「設計要約」として使う(ADR-0034「論点2」) |
| `summary` | `str` | `implement`のJob **result**由来(`ImplementResult.summary`)。MR本文の実装概要 |
| `commit_message` | `str \| None` | `implement`のJob result由来。push時のcommit messageとして使う(`None`の場合`summary`にフォールバック) |
| `commit_sha` | `str` | 実装フェーズが確認したworktreeの実際のHEAD commit sha。push対象 |
| `remote_branch` | `str` | GitLab上の実装用branch名(`ai/issue-<issue_iid>`)。push先・MRのsource_branch |
| `local_branch` | `str` | worktreeのローカルbranch名(`issue-<issue_iid>`) |
| `worktree_path` | `str` | worktreeの絶対パス。diff計算(`push.git_ops`)の対象 |
| `assumed_uncertainties` | `tuple[Mapping[str, Any], ...]` | 実装フェーズがASSUME判定した前提の一覧(`{"question", "severity", "assumption"}`)。MR本文の「○○と仮定して実装した」として使う |

### Job payload/result(`push`種別、`job/protocol.py`の`Job.payload`/`Job.result`)

payload(組み立ては`push.build_push_job_payload`、分解は`push_job_payload_to_args`):

| フィールド | 型 | 補足 |
|---|---|---|
| `payload.project` | `str` | 対象プロジェクトパス |
| `payload.issue_iid` | `int` | 対象IssueのIID |
| `payload.plan_document` | `string` | 実装フェーズのJob **payload**の`plan_document`を転記 |
| `payload.summary` | `string` | 実装フェーズのJob **result**の`summary`を転記 |
| `payload.commit_message` | `string \| null` | 実装フェーズのJob resultの`commit_message`を転記 |
| `payload.commit_sha` | `string` | 実装フェーズのJob resultの`commit_sha`を転記 |
| `payload.remote_branch` | `string` | 実装フェーズのJob resultの`remote_branch`を転記 |
| `payload.local_branch` | `string` | 実装フェーズのJob resultの`local_branch`を転記 |
| `payload.worktree_path` | `string` | 実装フェーズのJob resultの`worktree_path`を転記 |
| `payload.assumed_uncertainties` | `object[]` | 実装フェーズのJob resultの`assumed_uncertainties`を転記 |

result(`build_push_job_result`が組み立てる):

| フィールド | 型 | 補足 |
|---|---|---|
| `result.project` | `str` | 対象プロジェクトパス |
| `result.issue_iid` | `int` | 対象IssueのIID |
| `result.pushed_commit_sha` | `string` | `push_file_changes`が返す、GitLab上に新しく作成されたcommitのsha(worktree内のローカルcommit shaとは別物) |
| `result.remote_branch` | `string` | `create_merge_request`が返した`MergeRequest.source_branch` |
| `result.merge_request_iid` | `int` | 作成したMRのIID |
| `result.merge_request_web_url` | `string` | 作成したMRのURL |

## Claude Codeへの権限付与

該当なし。本フェーズは`ClaudeCodeRunner`を呼び出さない(ADR-0034「論点3」)。

## 処理の流れ

1. `push_job_payload_to_args`でpayloadを分解(`PushInput`)
2. `GitLabReader.get_default_branch`でdefault branchを解決する(ADR-0032と同じ問い合わせ、
   実行時点で再度呼ぶ)
3. `push.git_ops.compute_commit_actions(worktree_path, default_branch, commit_sha)`で、
   `git merge-base(commit_sha, origin/<default_branch>)`で計算したbaseから`commit_sha`までの
   差分を`CommitAction`の配列にする(ADR-0034「論点1」)。差分が空であれば
   `NoFileChangesError`を送出する
4. `commit_message`(`None`の場合は`summary`にフォールバック)とともに
   `GitLabWriter.push_file_changes(project, remote_branch, commit_message, actions)`で
   GitLab上へ実際にpushする(許可リストの書き込み操作)
5. `push.mr_template.build_merge_request_title`/`build_merge_request_description`で
   MRタイトル・本文を組み立て、`GitLabWriter.create_merge_request(project, remote_branch,
   default_branch, title, description)`でMRを作成する
6. push・MR作成の両方が成功した後、`Workspace Manager.discard_for_issue(project, issue_iid)`で
   worktreeを破棄する(ADR-0034「論点4」)

push・MR作成のいずれかが失敗した場合、`discard_for_issue`は呼ばない(既存のJob再試行
(ADR-0017)で同じworktree・同じローカルcommitを再利用できるようにするため)。

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/push/errors.py`。

- `PushError(Exception)` — push と MR 作成フェーズ経由の処理が失敗したことを表す基底例外。
  `push/git_ops.py`のgitコマンド呼び出しが失敗した場合もこれを送出する
- `NoFileChangesError(PushError)` — baseと`commit_sha`の間に変更されたファイルが1件も
  検出されなかったことを表す。`push_file_changes`に空の`actions`を渡すことを防ぐ構造的な
  ガード

`build_push_handler`(`cli/dispatcher.py`)内で送出された例外(`PushError`系、
`GitLabApiError`等)は`RunnerDispatcher._process`(ADR-0022)が捕捉し、`fail(..., retry=True)`
となる。`JobRepository`が既に持つJob再試行/デッドレター機構(ADR-0017、既定
`max_attempts=3`)にそのまま乗る。専用のリトライ機構は実装しない。

## テスト方針

実装場所: `tests/gitlab_ai_platform/push/`・`tests/gitlab_ai_platform/cli/test_dispatcher.py`
(`src/`をミラー、ADR-0001)。`unittest.mock`は使わず手書きフェイクを使う(CLAUDE.mdのテスト
方針)。実際のgit/subprocess/GitLab APIには繋がない(`push/test_git_ops.py`は`tmp_path`上の
実際の一時gitリポジトリを使うが、外部サービスへは繋がない)。

- `push/test_errors.py`: `NoFileChangesError`が`PushError`のサブクラスであることを検証
- `push/test_types.py`: `PushInput`が`frozen=True`であること、`assumed_uncertainties`の既定値が
  空タプルであることを検証
- `push/test_git_ops.py`: `resolve_push_base_sha`が実際の一時gitリポジトリに対して正しい
  merge-baseを返すこと(default branchが分岐後に進んでも変わらないこと含む)、
  `compute_commit_actions`が追加/変更/削除ファイルを正しく`CommitAction`に変換すること、
  renameがdelete+createの組として現れること、差分が無ければ空リストを返すこと、gitが失敗した
  場合に`PushError`を送出すること、`run`引数を差し替えられることを検証する
- `push/test_mr_template.py`: `build_merge_request_title`が`summary`の先頭行を使うこと・空の
  場合のフォールバック・長い場合の切り詰めを検証。`build_merge_request_description`が
  「対応Issue」「設計要約」「○○と仮定して実装した」を必須項目として含むこと、
  `assumed_uncertainties`が空の場合のフォールバック文言を検証する
- `push/test_job.py`: `build_push_job_payload`が実装フェーズの`payload`/`result`から必要な
  フィールドを過不足なく組み立てること(`plan_document`はpayload由来、他はresult由来)、
  `push_job_payload_to_args`が往復できること、`build_push_job_result`が
  `MergeRequest`から必要なフィールドのみを転記することを検証する
- `cli/test_dispatcher.py`: `build_push_handler`がdefault branch解決→diff計算→push→MR作成→
  worktree破棄という流れで結果辞書を組み立てること、`compute_commit_actions`がdefault branch・
  commit_shaを正しく渡されて呼ばれること、`commit_message`が`None`の場合`summary`に
  フォールバックすること、差分が空の場合`NoFileChangesError`を送出しpush/MR作成/discardの
  いずれも呼ばないこと、push失敗時・MR作成失敗時はworktreeを破棄しないこと(ADR-0034
  「論点4」)、push成功時はworktreeを破棄することを検証する。`build_job_handlers`が
  `JobType.PUSH`を登録することも検証する

## 関連ドキュメント

- [architecture.md](../architecture.md) 「Orchestrator」の行(M4-1〜M4-6, M4-9〜M4-10)
- [ADR-0031: Workspace ManagerのIssue単位worktree対応](../adr/0031-issue-workspace.md) —
  `discard_for_issue`
- [ADR-0032: GitLab Adapterへのdefault branch取得メソッドの追加](../adr/0032-default-branch-lookup.md)
- [ADR-0033: 実装フェーズ(Job種別`implement`)の設計](../adr/0033-implement-phase.md) —
  本フェーズの入力元
- [ADR-0034: push と MR 作成フェーズの設計](../adr/0034-push-and-mr-phase.md)
- [specs/implement-phase.md](implement-phase.md) — 実装フェーズ(M4-8)の仕様。
  `payload.plan_document`/`result.commit_sha`等の転記元
- [specs/workspace-manager.md](workspace-manager.md) — `discard_for_issue`(ADR-0031)
- [specs/gitlab-adapter.md](gitlab-adapter.md) — `push_file_changes`/`create_merge_request`
  (既存の許可リスト操作)、`get_default_branch`(ADR-0032)
- [specs/job-model.md](job-model.md) — `JobType.PUSH`(ADR-0034)
- [operations/security.md](../operations/security.md) — GitLabへの実際の書き込みが
  本フェーズで初めて発生すること
- ソースコード: `src/gitlab_ai_platform/push/`(`types.py` / `git_ops.py` / `mr_template.py` /
  `errors.py` / `job.py` / `__init__.py`)、`src/gitlab_ai_platform/cli/dispatcher.py`
  (`build_push_handler`)
