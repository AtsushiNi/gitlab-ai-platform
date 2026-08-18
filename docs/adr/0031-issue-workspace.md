# ADR-0031: Workspace ManagerのIssue単位worktree対応

- Issue: [#114](https://github.com/AtsushiNi/gitlab-ai-platform/issues/114) (M4-8)
- 状態: 決定

## 背景・制約

- ADR-0027(要求分析フェーズ)・ADR-0029(設計フェーズ)・ADR-0030(実装計画フェーズ)は
  いずれも「Workspace Manager(`workspace/`)を使わず`run_prompt`をworktree無しで実行する」
  という設計を選んだ。理由は共通していて、`WorkspaceManager.prepare(project, mr_iid, ref)`が
  MR番号とgit refの組を前提にしたシグネチャであり、Issue単位のフェーズにそのまま使うと
  次の2つの独立した課題が生じるためだった:
  1. **default branchの取得手段が無い**: `ref`に何を渡すべきかをGitLab Adapterが解決できない
     (ADR-0032で対処)
  2. **worktreeのキー空間衝突**: GitLabではMRとIssueのIID採番は独立した名前空間のため、
     Issueの番号をそのまま`prepare`の`mr_iid`引数に流用すると、たまたま同じ番号のMRの
     worktree(進行中のレビュー等)と衝突し、`reset --hard`で破損させる危険がある
     (ADR-0029「背景・制約」が明示的に指摘)
- 本Issue(M4-8、実装フェーズ)は、パイプライン中で初めて実際にファイルを編集し
  テストを実行する必要があるフェーズであり、要求分析/設計/実装計画のように
  「リポジトリを参照しない」という制約でスコープを絞ることができない。実際のworktreeが
  必要になる最初のフェーズであり、上記2の衝突リスクに今ここで対処する必要がある

## 決定

### 既存の`prepare`/`discard`は変更せず、`prepare_for_issue`/`discard_for_issue`を追加する

`WorkspaceManager`(`src/gitlab_ai_platform/workspace/protocol.py`)に次の2メソッドを追加する。

```python
def prepare_for_issue(
    self, project: str, issue_iid: int, ref: str
) -> IssueWorktreeHandle: ...
def discard_for_issue(self, project: str, issue_iid: int) -> None: ...
```

既存の`prepare(project, mr_iid, ref) -> WorktreeHandle`/`discard(project, mr_iid) -> None`は
シグネチャ・挙動とも一切変更しない。ADR-0026(`wait_for_human`の追加)・ADR-0027(`run_prompt`の
追加)と同じ「既存メソッドは変更せず新メソッドを追加する」方針を踏襲する。

### worktreeのパス・ローカルbranch名は`mr-<iid>`とは別の`issue-<iid>`名前空間を使う

`GitWorkspaceManager`(`workspace/git_workspace.py`)の実装:

```text
<root>/worktrees/<slug>/mr-<iid>/       # 既存(MR単位)
<root>/worktrees/<slug>/issue-<iid>/    # 新規(Issue単位)
```

ローカルbranch名も同様に`mr-<iid>`/`issue-<iid>`で完全に分離する。これにより、MRとIssueの
IIDが数値として偶然一致しても、worktree・ローカルbranchが物理的に別のディレクトリ・別の名前に
なり、`reset --hard`が互いを破壊しうる余地が構造的に無くなる(背景に記載したADR-0029の
衝突リスクを、実行時チェックではなく名前空間の分離という構造的な手段で解消する)。

bare clone/fetch/`git worktree`まわりの内部処理(`_sync_bare_repo`/`_ensure_worktree`)は
`prepare`/`prepare_for_issue`で共通化した。project単位のロック(`_project_lock`、M2-1・
ADR-0015)も共有する: 同一projectの`mr-<iid>`/`issue-<iid>`worktreeへの操作は、同じbare repoに
対するgit操作である以上、排他も共有する必要があるため。

### `prepare_for_issue`の`ref`引数は呼び出し側が用意する

`prepare_for_issue`自身はdefault branchの解決やbranch作成を行わない(`prepare`が`ref`の解決を
呼び出し側に委ねているのと同じ設計)。実装フェーズのJobHandler(`cli/dispatcher.py`の
`build_implement_handler`)が、ADR-0032の`GitLabReader.get_default_branch`とGitLabWriterの
`create_branch`(許可リストの書き込み操作)を使って実装用branchをGitLab上に用意し、
そのbranch名を`ref`として`prepare_for_issue`に渡す(詳細はADR-0033)。

### `collect_garbage`(GC)は現時点で`mr-`prefixのworktreeのみを対象とする

`_worktrees_sorted_by_age`の対象は`mr-`prefixのディレクトリのみとし、`issue-`prefixの
worktreeはGCの対象に含めない。理由:

- 実装フェーズのJobHandlerは実装成功時に`discard_for_issue`を呼ばない設計にした
  (ADR-0033「決定」。ローカルcommitをM4-9のpushフェーズが参照できるようにするため)。
  そのため「最終利用時刻が古いissue用worktreeを機械的に破棄する」というGCの前提
  (=破棄しても実害が無い)が、issue用worktreeには単純に当てはまらない
  (未pushのローカルcommitを消してしまう恐れがある)
- Issue駆動開発(M4)は現時点でMRレビューよりも実行頻度が低いと想定され、ディスク使用量への
  影響も相対的に小さい
- 上記の理由により、issue用worktreeをGCの対象に含めるには「push済みかどうか」を
  Workspace Manager自身が判定できる必要があり、GitLab Adapterとの結合が生じる。
  本Issueのスコープを超えるため見送った

`prepare_for_issue`は新規worktree作成前に引き続き`_ensure_disk_budget`(既存のGC呼び出し)を
実行するが、evictできるのは`mr-`prefixのworktreeのみである点に注意。

## 却下した選択肢

- **`prepare`の`mr_iid: int`引数をそのまま流用し、Issueの番号を渡す**: 背景に記載した
  IID衝突リスクをそのまま受け入れることになり、ADR-0029が明示的に「安全性を損なう」として
  却下した選択肢と同じであるため見送った
- **`prepare`のシグネチャを`prepare(project, key: str, ref)`のように一般化し、
  呼び出し側が`f"mr-{mr_iid}"`/`f"issue-{issue_iid}"`を組み立てて渡す**: 既存の
  `prepare`呼び出し元(`cli/single_run.py`の`execute_review`、`cli/dispatcher.py`の
  `build_review_handler`)・既存テストの引数の型(`mr_iid: int`)を変更することになり、
  「既存メソッドは変更しない」という一貫した方針から外れる。将来的な一般化の余地は
  残しつつ、今回は影響範囲を最小化する新メソッド追加を優先した
- **`WorktreeHandle`をMR/Issue共通の型にし、`mr_iid`フィールドを`iid`のような汎用名に
  リネームする**: 既存の`WorktreeHandle.mr_iid`を参照する呼び出し元(`cli/single_run.py`、
  `cli/dispatcher.py`の`build_review_handler`等)に影響が及ぶため見送り、代わりに専用の
  `IssueWorktreeHandle`(`issue_iid`フィールドを持つ)を新設した
- **Issue用worktreeもGC(`collect_garbage`)の対象に含める**: 「決定」節に記載の通り、
  未pushのローカルcommitを保持し続ける必要があるため、単純なLRU破棄をそのまま適用できない。
  pushの完了を判定する仕組み(M4-9のスコープ)が整った時点で改めて検討する

## 影響

- `src/gitlab_ai_platform/workspace/types.py`に`IssueWorktreeHandle`を追加
- `src/gitlab_ai_platform/workspace/protocol.py`の`WorkspaceManager`に`prepare_for_issue`/
  `discard_for_issue`を追加。`tests/gitlab_ai_platform/workspace/test_protocol.py`の
  メソッド集合完全一致テストを更新
- `src/gitlab_ai_platform/workspace/git_workspace.py`の`GitWorkspaceManager`に実装を追加。
  `_sync_bare_repo`/`_ensure_worktree`として`prepare`との共通処理を切り出した
  (`prepare`自体の外部から見える挙動は変更していない)
- `tests/gitlab_ai_platform/workspace/test_git_workspace.py`に、MRとIssueのIIDが同じ数値でも
  worktree・ローカルbranchが衝突しないことを確認する回帰テストを追加
- `src/gitlab_ai_platform/cli/dispatcher.py`の`build_implement_handler`(ADR-0033)が
  `prepare_for_issue`を使う
- 今後、Issue用worktreeの後片付け(pushが完了した後の破棄)はM4-9(push フェーズ)または
  専用のGCの仕組みとして別途検討する必要がある(「決定」節参照)
