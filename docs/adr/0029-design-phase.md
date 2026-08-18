# ADR-0029: 設計フェーズの出力先とRunner実行方式の設計

- Issue: [#112](https://github.com/AtsushiNi/gitlab-ai-platform/issues/112) (M4-6)
- 状態: 決定

## 背景・制約

- M4-3(要求分析フェーズ、Job種別`issue-analysis`、[ADR-0026](0026-job-waiting-human-transition.md)、
  [ADR-0027](0027-issue-analysis-runner-execution.md))が、Issue本文を分析し要求・受入条件・
  前提・不足情報を構造化する仕組みを確立した。本Issueはその次のフェーズ: 要求分析結果を元に、
  実装前の設計をレビュー可能な成果物として出力するJobフェーズ(Job種別`design`)を実装する
- Issue #112本文は「出力先は対象プロジェクトの`docs/`相当(D-6のフォーマットに従わせる)」と
  記載しているが、これは出力フォーマットの指示であり、対象プロジェクトへの実際のgit commit/push
  をこのIssueのスコープに含めるかどうかは別途判断が必要だった。M4-9(#115、実装フェーズの
  成果をpush・MR作成する、未実装)のスコープは「実装フェーズ(M4-8)の成果をpushしMRを作成する」
  であり、設計フェーズ単体のpush/MR作成には触れていない
- ADR-0027は`issue-analysis`が「Issue本文の読解のみでリポジトリ探索をしない」ことを理由に
  `run_prompt`(worktree不要)を選んだ。設計フェーズは「実装前の設計」を行う以上、既存コード
  ベースの構造を踏まえた設計が望ましい面はあるが、Workspace Manager(`workspace/`、M1-6)の
  `prepare(project, mr_iid, ref)`はMR番号とgit refの組を前提にしたシグネチャで、Issueには
  対応するrefが存在しない(GitLab Adapterにdefault branch取得等の新規メソッドが必要になる)
- さらに、`GitWorkspaceManager`(`workspace/git_workspace.py`)はworktreeを
  `<root>/worktrees/<slug>/mr-<iid>/`というパス・`mr-<iid>`というローカルbranch名で管理して
  おり、この`<iid>`は文字通りMRのIIDを指す。設計フェーズがIssueのIIDを同じ`mr_iid`引数に
  渡すと、同一プロジェクト内でIssueとMRが同じIID値を持つ場合(GitLabではIssueとMRのIID
  採番は独立した名前空間のため、数値が偶然一致することがある)、全く無関係なMRレビュー用
  worktreeとdesign用worktreeが同じディレクトリ・同じローカルbranchを取り合い、
  `reset --hard`によって互いの作業内容を破壊しうる。これはADR-0027が指摘した
  「Adapter拡張が必要」という問題とは別に、Workspace Managerの前提(1つの`mr_iid`名前空間を
  MR専用に使う)を壊さずにIssue用途へ転用することはできないという、追加の設計上の制約である

## 決定

### 対象プロジェクトへの実際のコミットは行わない(選択肢B)

設計フェーズは、設計内容を構造化データ(`design_document`というMarkdown文字列 +
`questions`/`assumed_uncertainties`)として`Job.result`に持たせるところまでとし、
`GitLabAdapter`(`create_branch`/`push_file_changes`)を使った対象プロジェクトへの実際の
コミットは行わない。

理由:

- 設計フェーズ単体でコミットすると、対象プロジェクトの多くの場合protectedであろう
  デフォルトブランチへの直pushは`GitLabRestAdapter`が`ProtectedBranchError`で拒否する
  (`docs/adr/0002-gitlab-adapter-interface.md`)。それを避けるには設計専用のbranchを作る
  必要があるが、実装(M4-8)が始まる前に「設計だけのbranch」を作っても、実装フェーズが
  別branchで進めるなら早々に孤立し、同じbranchで続けるならbranch管理の責務が
  M4-6/M4-8/M4-9に分散して複雑になる
- Issue #112本文の「出力先は対象プロジェクトのdocs/相当」は、D-6フォーマット(本リポジトリの
  `docs/specs/template.md`と同じMarkdown構造)で設計内容を書く、という**出力フォーマット**の
  指示であり、実際にその場でgit commitすることまでは求めていないと解釈した
- `issue-analysis`(M4-3)も同様に「構造化データを`Job.result`に持たせるだけ」で、GitLabへの
  書き込みは一切行わない設計だった。設計フェーズも同じ枠組みを踏襲することで、
  `build_design_handler`の依存(`GitLabReader`+`ClaudeCodeRunner`+`Config`)を
  `build_issue_analysis_handler`と同じ最小構成に保てる(`GitLabWriter`への依存を持ち込まない)
- 実際に対象プロジェクトへ設計ドキュメントをコミットするタイミングは、M4-8(実装フェーズ)・
  M4-9(pushとMR作成)が実装コードと一緒にコミットする形が自然(1つのMRに「設計+実装」が
  揃っている方がレビューしやすい)。これはM4-8/M4-9側で判断する

### Workspace Manager(worktree)は使わず、`run_prompt`のまま実行する

`build_design_handler`は`issue-analysis`と同じく`ClaudeCodeRunner.run_prompt`
(ADR-0027で追加)を使い、`Workspace Manager`は引数に取らない。Claude Codeの実行先には
Job処理の間だけ存在する一時ディレクトリ(`tempfile.TemporaryDirectory`)を使う。

理由:

- 背景に記載した通り、Workspace Managerを安全にIssue用途へ転用するには
  (a) GitLab Adapterへのdefault branch取得メソッドの追加、
  (b) worktreeのキー空間(`mr-<iid>`)をMR/Issueで衝突しないよう再設計する、
  という2つの独立した拡張が必要になり、いずれも本Issue(M4-6)のスコープを超える
- 設計フェーズの入力は要求分析フェーズが確定させた要求・受入条件・前提(`DesignInput`)であり、
  これらは既に`Issue`本文よりも実装に近い粒度まで構造化済みのデータである。「既存コードを
  読んでから設計する」ことの価値は認めつつ、初期実装では要求分析結果とIssue本文
  (タイトル・説明・ラベル)のみを入力とし、実装詳細(ファイルパス・関数名等)を断定的に
  書かないようプロンプト(`design/prompts.py`)側で明示的に指示することで対処した
  (`design/prompts.py`の「制約: リポジトリを参照できません」節)
- リポジトリ探索が実際に必要になった場合、上記(a)(b)の拡張を専用のIssueとして起票し、
  そこで安全な設計(例: Issue用途には`mr_iid`ではなく`prepare(project, key, ref)`のような
  汎用引数に一般化する、あるいはIssue専用の別名前空間を設ける)を検討するのが良い

## 却下した選択肢

- **Workspace Managerを拡張し、`prepare(project, mr_iid, ref)`にIssueのIIDをそのまま渡す**:
  背景に記載した「MRとIssueのIID衝突によるworktree破壊」のリスクを受け入れることになり、
  安全性を損なう。GitLab Adapterへのdefault branch取得メソッド追加も同時に必要になり、
  本Issueのスコープを大きく超える
- **設計フェーズでも`GitLabAdapter`を使い、その場で対象プロジェクトへコミットする**: 上記
  「決定」に記載の通り、protected branch拒否・branch管理の責務分散という問題があり、
  M4-8/M4-9で実装と一緒にコミットする方が自然なため見送った
- **`build_resolved_design_job_result`を`issue_analysis.build_resolved_issue_analysis_job_result`
  と共通化し、`orchestrator`パッケージ等に切り出す**: 両者のロジックは`questions`/
  `assumed_uncertainties`キーの汎用的なdictマージであり、実際に完全に同一である。しかし
  `build_resolved_issue_analysis_job_result`は既に#109/#111でマージ・テスト済みの公開関数
  (`issue_analysis/__init__.py`から再エクスポート、`docs/specs/issue-analysis.md`に記載)であり、
  本Issueのスコープでその公開契約を変更する理由は無い。ADR-0028自身が「design/implementが
  独自の統合関数を持つ必要がある点は今後の課題として残る」と明記しており、小さな重複を許容し
  既存コードには触れない方針を踏襲した(将来、`implement`フェーズ実装時に3つ目の重複が
  生じた時点で共通化を検討する)

## 影響

- `src/gitlab_ai_platform/design/`(新規)に`types.py`(`DesignInput`/`DesignResult`)・
  `prompts.py`(`build_design_instructions`)・`parser.py`(`parse_design_output`)・
  `errors.py`(`DesignError`/`DesignOutputParseError`)・`job.py`
  (`build_design_job_payload`/`design_job_payload_to_args`/`build_design_job_result`/
  `build_resolved_design_job_result`)を追加
- `src/gitlab_ai_platform/cli/dispatcher.py`に`build_design_handler`を追加し、
  `build_job_handlers`が`JobType.DESIGN`に対応付ける(ADR-0026の`WaitingForHumanError`/
  `wait_for_human`をそのまま再利用)
- `src/gitlab_ai_platform/cli/respond.py`の回答統合先を`job_type`ごとの辞書
  (`_RESULT_RESOLVERS`)に一般化し、`design`種別Jobの`WAITING_HUMAN`後の再開に対応した
  (ADR-0028が「今後の課題」としていた拡張)
- M4-10(Issue→MRパイプラインのオーケストレーション)が実装される際、`issue-analysis`完了
  Jobの`result`から`design.build_design_job_payload`で`design`種別Jobのpayloadを組み立てて
  投入する橋渡しコードが必要になる(本Issueでは投入者=Poller相当は実装しない)
- 将来リポジトリ探索が必要になった場合は、Workspace Manager/GitLab Adapterの拡張を
  専用Issueとして起票する必要がある(「決定」節参照)
