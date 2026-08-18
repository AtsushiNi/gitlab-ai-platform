# ADR-0034: push と MR 作成フェーズの設計

- Issue: [#115](https://github.com/AtsushiNi/gitlab-ai-platform/issues/115) (M4-9)
- 状態: 決定

## 背景・制約

- M4-8(実装フェーズ、Job種別`implement`、[ADR-0033](0033-implement-phase.md))が、Issue単位の
  worktree(`Workspace Manager.prepare_for_issue`、[ADR-0031](0031-issue-workspace.md))上で
  Claude Codeにファイル編集・テスト実行・ローカルgit commitまでを行わせる仕組みを確立した。
  ADR-0033は「実際のGitLabへのpushをどこにも実装しない」ことを明確な非目標としており、
  本Issueがその最後のステップ(pushとMR作成)を担う
- `GitLabWriter.push_file_changes`(`gitlab_adapter/protocol.py`)はGitLab Commits API経由の
  ファイル単位create/update/deleteであり、`git push`そのものではない。worktree内のローカル
  commit(1つ以上)を、変更されたファイルの一覧(`CommitAction`の配列)に変換する処理が
  新たに必要になる
- 判断すべき論点は4つ: (1) 差分を取る際の「変更前」の基準点(base)をどう決めるか、
  (2) MR本文の必須項目「設計要約」の情報源、(3) `JobType`を新設するか、
  (4) push成功後にworktreeを後片付けするか

## 決定

### 論点1: baseの決定は「pushフェーズが`git merge-base`を都度計算する」

`implement`の`Job.result`に`base_sha`のような新しいフィールドを追加するのではなく、
push フェーズが自分自身で`git merge-base <commit_sha> refs/remotes/origin/<default_branch>`を
計算し、それを差分のbaseとして使う(`push/git_ops.py`の`resolve_push_base_sha`)。

理由:

- `implement`パッケージ・`Job.result`のスキーマは既にマージ済み(#114)で安定している。
  `IssueWorktreeHandle.sha`(prepare_for_issue直後の状態)をJob resultに追加で持たせる案も
  検討したが、`implement/job.py`の`build_implement_job_result`(および対応する
  `tests/gitlab_ai_platform/implement/test_job.py`の厳密な辞書一致テスト)への変更が
  必要になり、影響範囲が本Issueのパッケージ外に及ぶ
- `git merge-base`は`implement`のworktree(push成功まで`discard_for_issue`されない、
  下記「論点4」)に対して追加のgitコマンドを1つ呼ぶだけで完結し、`push`パッケージ内で
  閉じる。`ai/issue-<issue_iid>`branchはdefault branchから分岐した後、リモートへの
  マージ・rebaseを一切経ていない(pushフェーズ自身がまだ実行されていないため)ので、
  `merge-base(commit_sha, origin/<default_branch>)`は「実装用branchが分岐した時点の
  commit」と一致し、`IssueWorktreeHandle.sha`を保存しておいた場合と同じ値になる
  (worktreeが`_ensure_worktree`で`reset --hard`されない限り不変)
- `default_branch`は`GitLabReader.get_default_branch`(ADR-0032)で即座に再取得できる、
  副作用のない読み取り専用の問い合わせであり、push時点で再度呼んでも安全

`implement/git_ops.py`と同じ「Workspace Managerを拡張せず、`worktree_path`に対して直接gitを
呼ぶヘルパー」というパターンを`push/git_ops.py`でも踏襲する。差分抽出は
`git diff --no-renames --name-status <base_sha> <commit_sha>`で変更ファイル一覧
(状態: `A`/`M`/`D`/`T`)を取得し、削除以外は`git show <commit_sha>:<path>`でcommit時点の内容を
読む(`--no-renames`によりrenameは常にdelete+create の組として扱われ、`CommitActionType`が
`create`/`update`/`delete`の3値しか持たない既存の型をそのまま使える)。

### 論点2: 「設計要約」は`implement`のJob payloadが引き継いでいる`plan_document`を使う

`design_document`を`design`→`plan`→`implement`の全パッケージに新たに引き回す変更はせず、
`implement`のJob **payload**(`payload.plan_document`、実装計画フェーズの成果物)を
「設計要約」として使う。

理由:

- `plan_document`は`design_document`(設計フェーズの成果物)を実装可能な粒度のタスクへ
  具体化したものであり、設計内容の要約として実用上十分な情報を持つ
- `design_document`をpayload全体に追加で流す案は、既にマージ済みの`design`/`plan`/
  `implement`という3つの安定したパッケージ(それぞれ#112/#113/#114でマージ済み)すべてに
  変更が必要になり、影響範囲・レビューコストが大きい。本Issueのスコープ(pushとMR作成)を
  大きく超える
- push フェーズのJob payloadは、Issue単位の`Job`オブジェクトの`payload`
  (`implement.build_implement_job_payload`が組み立てたもの、`plan_document`を含む)と
  `result`(`implement.build_implement_job_result`が組み立てたもの、`commit_sha`等を含む)の
  **両方**を入力として`push.build_push_job_payload`で組み立てる。M4-10(オーケストレーション、
  未実装)が完了済みJobから次のJobを投入する際、`Job`オブジェクト全体
  (`payload`/`result`両方)にアクセスできる前提のため、この設計は無理なく成立する

### 論点3: `JobType.PUSH = "push"`を新設する

ADR-0030(実装計画フェーズ)と同じ「1フェーズ1JobType」パターンを踏襲し、`job/protocol.py`の
`JobType`に`PUSH = "push"`を追加する。

理由:

- push フェーズは`Claude Code Runner`を呼び出さない(git diff計算とGitLab Adapter呼び出しのみの
  機械的な処理)という点で、`review`/`issue-analysis`/`design`/`plan`/`implement`のいずれとも
  性質が異なる。しかし`JobType`/`JobHandler`の抽象(ADR-0016)自体は「Runnerを呼ぶこと」を
  前提にしておらず、`Job`の観測性(`list_by_status`・`list_dead_letters`・HTTP API経由の
  状態確認)・再試行/デッドレター機構(ADR-0017)・パイプライン全体の一貫した「あるフェーズの
  `Job.result`から次のフェーズのpayloadを組み立てて`enqueue`する」構成(ADR-0030が説明した
  理由)は、Runner呼び出しの有無に関わらずpushフェーズにも等しく当てはまる
- GitLab Commits API呼び出しはネットワークI/Oであり、一時的な失敗(タイムアウト等)が
  起こりうる。独立したJobとして`JobRepository`の再試行機構にそのまま乗せられる利点は
  `implement`と同様に大きい
- Enum変更のリスクは小さい(ADR-0030「決定」と同じ理由: `job/sqlite.py`の`job_type`列は
  `TEXT NOT NULL`で`CHECK`制約を持たない)

`build_push_handler`(`cli/dispatcher.py`)は`runner: ClaudeCodeRunner`を引数に取らない
(これまでの5種類のhandlerと異なる、Runnerを使わない初めてのhandler)。`WaitingForHumanError`も
送出しない(pushフェーズは機械的な処理で人間の判断を要する不明点を生成しないため)。そのため
`cli/respond.py`の`_RESULT_RESOLVERS`に`JobType.PUSH`は追加しない。

### 論点4: push成功後、`discard_for_issue`を呼んでworktreeを破棄する

`build_push_handler`は、`push_file_changes`・`create_merge_request`の両方が成功した後、
`Workspace Manager.discard_for_issue(project, issue_iid)`を呼ぶ。

理由:

- ADR-0031「今後の課題」・ADR-0033「決定」がいずれも「worktreeの後片付けはM4-9が
  push完了後に行う」と明記しており、本Issueがまさにそのタイミングにあたる
- push成功後、ローカルworktreeのcommit(`commit_sha`)はその役目(Commits API経由でGitLab上に
  同内容の新しいcommitとして反映される)を終えており、ローカルに残しておく理由が無い。
  `issue-<issue_iid>`worktree/ローカルbranchは`collect_garbage`(GC)の対象外
  (ADR-0031「決定」)のままなので、明示的に破棄しない限りディスクに残り続けてしまう
- push・MR作成のどちらかが失敗した場合は`discard_for_issue`を呼ばない(既存のJob再試行
  (ADR-0017)でJobがそのまま再試行された際、同じworktree・同じローカルcommitを参照できる
  ようにするため。`implement`が実装成功時に`discard_for_issue`を呼ばない設計
  (ADR-0033)と対称的な理由)

## 却下した選択肢

- **`IssueWorktreeHandle.sha`を`implement`のJob resultに新フィールドとして追加する**:
  論点1に記載の通り、既にマージ済みの`implement`パッケージへの変更(スキーマ・厳密一致テスト)が
  必要になり影響範囲が大きい。`git merge-base`による都度計算で同じ値を得られるため見送った
- **`design_document`を`design`→`plan`→`implement`→`push`と全フェーズへ引き回す**:
  論点2に記載の通り、3つの安定したパッケージへの変更が必要になり本Issueのスコープを
  大きく超える。`plan_document`で実用上十分な代替になると判断した
- **独立した`JobType`を追加せず、`implement`のJobHandler内部の最終ステップとして
  push・MR作成まで行う**: ADR-0030が実装計画フェーズについて却下した理由(Job Queueでの
  観測性の非対称、ADR-0022の「JobHandlerはJobType非依存の薄い処理単位」という設計意図との
  不整合)と同じ理由で見送った。加えて、`implement`は既に「commitできたが判断に迷う点がある」
  場合に`WAITING_HUMAN`へ遷移する経路を持っており、そこにpush・MR作成まで詰め込むと
  「実装の不明点」に対する人間の回答を待っている間、pushされるべきcommitが宙に浮く状態が
  長引く(既に成功しているcommitをより早くGitLab上で可視化できるという1フェーズ1JobTypeの
  利点が失われる)
- **push失敗時も`discard_for_issue`を呼ぶ**: 論点4に記載の通り、再試行時に同じworktree・
  ローカルcommitを再利用できなくなり、実装のやり直し(`implement`の再実行)が必要になって
  しまう。push自体の再試行(`push_file_changes`/`create_merge_request`をもう一度呼ぶだけ)で
  十分なはずのケースまで実装からやり直すコストを払う理由が無い
- **renameを`CommitActionType`に`move`のような新しい値を追加して表現する**: 本Issueの
  スコープは「既存Adapterメソッドを呼び出すだけ」であり、`gitlab_adapter/types.py`の変更は
  求められていない。`git diff --no-renames`でrenameを常にdelete+createの組として扱うことで、
  既存の3値のみで表現可能にした

## 影響

- `src/gitlab_ai_platform/push/`(新規)に`types.py`(`PushInput`)・
  `git_ops.py`(`resolve_push_base_sha`/`compute_commit_actions`)・
  `mr_template.py`(`build_merge_request_title`/`build_merge_request_description`)・
  `errors.py`(`PushError`/`NoFileChangesError`)・`job.py`
  (`build_push_job_payload`/`push_job_payload_to_args`/`build_push_job_result`)を追加
- `src/gitlab_ai_platform/job/protocol.py`の`JobType`に`PUSH = "push"`を追加
- `src/gitlab_ai_platform/cli/dispatcher.py`に`build_push_handler`を追加し、
  `build_job_handlers`が`JobType.PUSH`に対応付ける。`runner`を使わない初めてのhandler
- `src/gitlab_ai_platform/cli/respond.py`は変更しない(pushフェーズは`WAITING_HUMAN`を
  使わないため)
- M4-10(Issue→MRパイプラインのオーケストレーション)実装時、`implement`完了Jobの
  `payload`/`result`から`push.build_push_job_payload`で`push`種別Jobのpayloadを組み立てて
  投入する橋渡しコードが必要になる(本Issueでは投入者は実装しない、これまでの各フェーズと
  同じ位置づけ)
- `docs/operations/security.md`§3.5(新設)・§2.2を更新した。`run_dispatcher`が
  `review`/`issue-analysis`/`design`/`plan`/`implement`/`push`の全JobHandlerへ同一の
  `GitLabRestAdapter(config.gitlab_url, config.gitlab_token)`を渡す設計のため、
  `implement`(`create_branch`)に続き本フェーズ(`push_file_changes`/`create_merge_request`)も
  自動実行系用トークン([ADR-0019](0019-gitlab-token-scoping.md)が`read_api`スコープ・
  Reporterロールと定めたもの)で書き込みAPIを呼ぶことになる。この前提の崩れは
  実装フェーズ(M4-8)から既に生じていた既知の課題であり、本Issueで新たに作ったものでは
  ないが、pushフェーズの追加でより顕在化するため`security.md`§3.5に明記した。
  トークン分離の再設計は本Issueのスコープ外とし、今後の課題として残す
