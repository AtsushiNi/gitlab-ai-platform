# 設計フェーズ(design)

- 実装場所: `src/gitlab_ai_platform/design/`(Job種別への配線は`cli/dispatcher.py`、
  `WAITING_HUMAN`後の再開は`cli/respond.py`)
- 対応Issue: [#112](https://github.com/AtsushiNi/gitlab-ai-platform/issues/112) (M4-6)
- 関連ADR: [ADR-0026](../adr/0026-job-waiting-human-transition.md)(`WAITING_HUMAN`遷移の設計)、
  [ADR-0027](../adr/0027-issue-analysis-runner-execution.md)(要求分析フェーズがworktreeを
  使わない設計、設計フェーズも同じ方針を踏襲)、
  [ADR-0028](../adr/0028-waiting-human-answer-integration.md)(`WAITING_HUMAN`後の回答取り込み)、
  [ADR-0029](../adr/0029-design-phase.md)(設計フェーズの出力先・Runner実行方式)
- ステータス: 実装済み

## 責務

要求分析フェーズ(M4-3、`issue_analysis`)が確定させた要求・受入条件・前提を元に、実装前の
設計をレビュー可能な成果物として出力するJobフェーズ。Job種別`design`
(`job/protocol.py`の`JobType.DESIGN`)として、`RunnerDispatcher`(M3-3、`cli/dispatcher.py`)の
`JobHandler`(`build_design_handler`)で処理する。

**無人実行トラック限定の機能。** 対話型トラック(VS Code拡張 + GitLab Adapter MCP Server、
M2-12)では、人間とClaude Codeが対話しながら実装を進める過程で設計内容が自然に確認される
ため、独立した設計フェーズは不要(`design/__init__.py`のdocstring、ADR-0029)。このフェーズは
Issueに無人実行ラベルが付いた場合(M4-1、Issue Poller)にのみ実行される。

## 前提と非対象

- 前提:
  - 処理対象のJobは、要求分析フェーズ(M4-3)完了時の`Job.result`
    (`issue_analysis.build_issue_analysis_job_result`/
    `build_resolved_issue_analysis_job_result`が組み立てたもの)を元に
    `design.build_design_job_payload`で組み立てて`JobRepository.enqueue`されたもの。
    投入者(「issue-analysis完了 → design投入」の橋渡し)自体はM4-10
    (Issue→MRパイプラインのオーケストレーション)のスコープで、本パッケージには含まない
  - Issue本体の取得は`GitLabReader.get_issue`(M2-10)で実行時に行う(`issue-analysis`と
    同じ設計)。設計プロンプトの主要な入力は要求分析結果(`DesignInput`)であり、Issue本文は
    見出し情報として追記される
  - 「不足情報」の判定(`ASK`で止めるか`ASSUME`で継続するか)は本パッケージの対象外。
    `DesignResult.uncertainties`を`orchestrator.judge_uncertainties`(M4-4、ADR-0024)に
    渡した結果を呼び出し側(`build_design_handler`)が使う(`issue-analysis`と同じパターン)
- 非対象:
  - リポジトリの探索(既存実装の確認等)。設計フェーズはWorkspace Manager(worktree)を
    使わず、要求分析結果とIssue本文のみを入力とする(ADR-0029「決定」。Workspace Managerを
    Issue用途へ安全に転用するには、GitLab Adapterへのdefault branch取得メソッド追加と、
    worktreeのキー空間(`mr-<iid>`)の再設計という2つの拡張が必要で、いずれも本Issueの
    スコープを超えるため)
  - 対象プロジェクトへの実際のコミット(`GitLabAdapter`の`create_branch`/`push_file_changes`)。
    設計フェーズは`design_document`を`Job.result`に構造化データとして持たせるところまでで、
    実際のコミットは行わない(ADR-0029「決定」)。実装コードと一緒にコミットするかどうかは
    M4-8(実装フェーズ)・M4-9(pushとMR作成)側の判断
  - `WAITING_HUMAN`への実際の状態遷移(`JobRepository.wait_for_human`の呼び出し)・
    `WAITING_HUMAN`からの再開(`update_status`呼び出し)。本パッケージは`Job.result`の構造
    (`build_design_job_result`/`build_resolved_design_job_result`)を組み立てるところまでで、
    実際の状態遷移は`cli/dispatcher.py`の`RunnerDispatcher._process`(ADR-0026)・
    `cli/respond.py`の`respond_to_job`(ADR-0028、M4-6で`design`種別に対応拡張)が担う
  - 「issue-analysis完了 → design投入」の橋渡し(Job間の連携)。`design.build_design_job_payload`
    という組み立て関数は用意するが、実際にいつ・誰が呼ぶかはM4-10のスコープ

## 公開インターフェース

実装場所: `src/gitlab_ai_platform/design/`(`prompts.py` / `parser.py` / `types.py` /
`errors.py` / `job.py`)。`src/gitlab_ai_platform/design/__init__.py`から再エクスポート。

```python
def build_design_instructions(design_input: DesignInput) -> str:
    """設計用のinstructions文字列を返す。`design_input`をテキストに埋め込む決定的な処理。"""


def parse_design_output(run_result: RunResult) -> DesignResult:
    """`run_result`から結果スキーマを抽出する。`issue_analysis.parser.parse_issue_analysis_output`
    と同じ設計方針(```jsonフェンスの抽出・検証、is_errorの確認)。"""


def build_design_job_payload(
    project: str, issue_iid: int, analysis_result: Mapping[str, Any]
) -> dict[str, Any]:
    """要求分析フェーズ完了時の`Job.result`から`design`種別Jobのpayloadを組み立てる。"""


def design_job_payload_to_args(
    payload: Mapping[str, Any],
) -> tuple[str, int, DesignInput]:
    """`design`種別Jobのpayloadから`(project, issue_iid, DesignInput)`を取り出す。"""


def build_design_job_result(
    project: str,
    issue_iid: int,
    design: DesignResult,
    judgments: Sequence[UncertaintyJudgment],
) -> dict[str, Any]:
    """設計フェーズのJob resultを組み立てる(`complete`/`wait_for_human`共通)。"""


def build_resolved_design_job_result(
    result: Mapping[str, Any], answers: Sequence[str]
) -> dict[str, Any]:
    """`WAITING_HUMAN`の`result`に人間の回答を統合した新しいresultを組み立てる(M4-6)。
    `cli/respond.py`の`respond_to_job`が呼び出す。"""
```

`JobHandler`本体(実際にRunnerを呼び出しJobとして処理する部分)は
`src/gitlab_ai_platform/cli/dispatcher.py`の`build_design_handler`:

```python
def build_design_handler(
    adapter: GitLabReader,
    runner: ClaudeCodeRunner,
    config: Config,
) -> JobHandler:
    """design種別の`JobHandler`を組み立てる。"""
```

`build_job_handlers`(`cli/dispatcher.py`)が`JobType.DESIGN`に対応付けてディスパッチテーブルへ
登録する。

## 入出力スキーマ

### `DesignInput`(`design/types.py`)

設計フェーズの入力。要求分析フェーズ完了時の`Job.result`から組み立てる。

| フィールド | 型 | 補足 |
|---|---|---|
| `requirements` | `tuple[str, ...]` | 要求分析フェーズが確定させた要求事項 |
| `acceptance_criteria` | `tuple[str, ...]` | 要求分析フェーズが確定させた受入条件 |
| `assumptions` | `tuple[str, ...]` | 要求分析フェーズでClaude Codeが置いた前提 |
| `assumed_uncertainties` | `tuple[str, ...]` | ASSUME判定(および`respond`でのASK→回答解決)で確定した前提を`"question → assumption"`形式の文字列に整形したもの |

### `DesignResult`(`design/types.py`)

Claude Codeの応答をパースした、1回の設計の結果。

| フィールド | 型 | 補足 |
|---|---|---|
| `design_document` | `str` | 設計内容をまとめたMarkdown文書(`docs/specs/template.md`のフォーマットに従う) |
| `uncertainties` | `tuple[Uncertainty, ...]` | 設計にあたって生じた不足情報(不明点)。各要素の`phase`は`"design"`固定(`orchestrator.types.Uncertainty`、ADR-0024) |

### Claude Codeへの出力指示(`build_design_instructions`が指示するJSONスキーマ)

`issue_analysis/prompts.py`と同じ設計パターン。応答の末尾に ```json フェンスで1つだけ、
次のスキーマのオブジェクトを出力させる:

| フィールド | 型 | 補足 |
|---|---|---|
| `design_document` | `string` | 設計文書全文(Markdown)。責務・前提と非対象・公開インターフェース・入出力スキーマ・エラー時の振る舞い・テスト方針の構成に従う(`docs/specs/template.md`準拠) |
| `open_questions` | `object[]` | 不足情報の一覧。無ければ空配列。各要素: `question: string`, `severity: "critical" \| "minor"`, `assumption: string \| null`(`severity`が`"minor"`の場合は必須) |

`parse_design_output`(`design/parser.py`)は`open_questions`の各要素を
`orchestrator.types.Uncertainty(question=..., severity=..., assumption=..., phase="design")`に
変換する。`severity="minor"`かつ`assumption`が空/欠落の場合は`DesignOutputParseError`を送出する
(`issue_analysis/parser.py`と同じ理由)。

### Job payload/result(`design`種別、`job/protocol.py`の`Job.payload`/`Job.result`)

payload(組み立ては`design.build_design_job_payload`、分解は`design_job_payload_to_args`):

| フィールド | 型 | 補足 |
|---|---|---|
| `payload.project` | `str` | 対象プロジェクトパス |
| `payload.issue_iid` | `int` | 対象IssueのIID |
| `payload.requirements` | `string[]` | 要求分析フェーズの`result.requirements`を転記 |
| `payload.acceptance_criteria` | `string[]` | 要求分析フェーズの`result.acceptance_criteria`を転記 |
| `payload.assumptions` | `string[]` | 要求分析フェーズの`result.assumptions`を転記 |
| `payload.assumed_uncertainties` | `object[]` | 要求分析フェーズの`result.assumed_uncertainties`を転記(`{"question", "severity", "assumption"}`の配列) |

result(`build_design_job_result`が組み立てる。`complete`・`wait_for_human`のどちらでも
同じ構造を使う、issue-analysisと同じ方針のADR-0026を踏襲):

| フィールド | 型 | 補足 |
|---|---|---|
| `result.project` | `str` | 対象プロジェクトパス |
| `result.issue_iid` | `int` | 対象IssueのIID |
| `result.design_document` | `string` | `DesignResult.design_document`をそのまま転記 |
| `result.assumed_uncertainties` | `object[]` | `orchestrator.assume_judgments`が返す`ASSUME`判定の不明点。各要素: `question`, `severity`(`"minor"`固定), `assumption` |
| `result.questions` | `object[]` | `orchestrator.ask_judgments`が返す`ASK`判定の不明点。各要素: `question`, `severity`(`"critical"`固定)。**`WAITING_HUMAN`のときのみ非空**になる |
| `result.resolved_questions` | `object[]` | `respond`が`questions`への回答を統合した後にのみ存在するフィールド。各要素: `question`, `severity`, `answer` |

`WAITING_HUMAN`への遷移そのもの(`JobRepository.wait_for_human`の呼び出し)は
`cli/dispatcher.py`の`RunnerDispatcher._process`が担う。`WAITING_HUMAN`後の再開
(`update_status`呼び出し・回答統合)は`cli/respond.py`の`respond_to_job`が担う
(`_RESULT_RESOLVERS`辞書で`job_type`ごとに`build_resolved_design_job_result`を選択する)。

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/design/errors.py`。

- `DesignError(Exception)` — 設計フェーズ経由の処理が失敗したことを表す基底例外
- `DesignOutputParseError(DesignError)` — Claude Codeの応答から結果スキーマを抽出できなかった
  ことを表す。`raw_text`に元の`result_text`を保持する(`issue_analysis.errors.
  IssueAnalysisOutputParseError`と同じ設計)。`run_result.is_error`が`True`の場合、
  `design_document`が空でない文字列でない場合、`open_questions`が配列でない場合、`severity`が
  `critical`/`minor`以外の場合、`severity="minor"`なのに`assumption`が欠落している場合等に
  送出する

`build_design_handler`(`cli/dispatcher.py`)内で送出された例外は`RunnerDispatcher._process`
(ADR-0022)が捕捉する: `DesignOutputParseError`を含むそれ以外の例外は`fail(..., retry=True)`
(1件のJobの失敗は他のJobの処理を止めない)。設計フェーズ自身は`WaitingForHumanError`
(`cli/dispatcher.py`、ADR-0026)を意図的に送出することで`WAITING_HUMAN`への遷移を要求する
(`fail`とは異なる正常系の分岐)。

## テスト方針

実装場所: `tests/gitlab_ai_platform/design/`・`tests/gitlab_ai_platform/cli/test_dispatcher.py`・
`tests/gitlab_ai_platform/cli/test_respond.py`(`src/`をミラー、ADR-0001)。`unittest.mock`は
使わず手書きフェイクを使う(CLAUDE.mdのテスト方針)。

- `test_types.py`: `DesignInput`/`DesignResult`が`frozen=True`であることを検証する
- `test_errors.py`: `DesignOutputParseError`が`DesignError`のサブクラスで`raw_text`を保持する
  ことを検証する
- `test_prompts.py`: `build_design_instructions`が同じ`DesignInput`に対して決定的であること、
  `requirements`/`acceptance_criteria`/`assumptions`/`assumed_uncertainties`の内容が
  出力文字列に含まれること、空の`DesignInput`でも例外を送出せず既定文言になること、
  「無人実行トラック専用」「リポジトリを参照できません」の説明を含むこと、JSONスキーマの
  キーワード(`design_document`/`open_questions`等)を含むことを検証する
- `test_parser.py`: `issue_analysis/parser.py`の`test_parser.py`と同じ観点(フェンス抽出、
  末尾優先、複数ブロック時の扱い、`is_error`時の即エラー、`permission_denials`時は警告のみで
  継続)に加え、`design_document`の型検証・`severity`の妥当性検証・`severity="minor"`時の
  `assumption`必須チェックを検証する
- `test_job.py`: `build_design_job_payload`/`design_job_payload_to_args`が要求分析フェーズの
  `result`から必要なフィールドを過不足なく往復できること、`assumed_uncertainties`が
  `"question → assumption"`形式に整形されること、`build_design_job_result`が
  `assume_judgments`/`ask_judgments`の結果を正しく変換すること、
  `build_resolved_design_job_result`が`questions`を`resolved_questions`へ変換し
  `assumed_uncertainties`へ合流させること、`answers`の件数が`questions`と一致しない場合に
  `ValueError`を送出することを検証する
- `cli/test_dispatcher.py`: `build_design_handler`が`GitLabReader.get_issue`→`IssueContext`→
  プロンプト組み立て→`ClaudeCodeRunner.run_prompt`→パース→判定という流れで結果辞書を
  組み立てること、`ASK`判定が1件でもあると`WaitingForHumanError`を送出すること、`MINOR`のみ
  (`ASSUME`判定)の場合は通常通り結果を返すことを検証する。`build_job_handlers`が
  `JobType.DESIGN`を登録することも検証する
- `cli/test_respond.py`: `design`種別の`WAITING_HUMAN`Jobに対して`respond_to_job`/`run_respond`
  が`build_resolved_design_job_result`を使って正しく`DONE`まで遷移することを検証する
  (`implement`種別の対応はM4-8、[specs/implement-phase.md](implement-phase.md)参照)

## 関連ドキュメント

- [architecture.md](../architecture.md) 「Orchestrator」の行(M4-1〜M4-6, M4-9〜M4-10)
- [ADR-0026: Job Queue経由での`WAITING_HUMAN`遷移の設計](../adr/0026-job-waiting-human-transition.md)
- [ADR-0027: 要求分析フェーズのRunner実行方式](../adr/0027-issue-analysis-runner-execution.md)
- [ADR-0028: `WAITING_HUMAN`後の回答取り込み・Job完了の設計](../adr/0028-waiting-human-answer-integration.md)
- [ADR-0029: 設計フェーズの出力先とRunner実行方式の設計](../adr/0029-design-phase.md)
- [specs/issue-analysis.md](issue-analysis.md) — 要求分析フェーズ(M4-3)の仕様。
  `payload.requirements`等の転記元、同じ設計パターンの先行実装
- [specs/claude-code-runner.md](claude-code-runner.md) — `IssueContext`/`build_issue_prompt`
  (M4-2)、`run_prompt`(M4-3、ADR-0027)
- [specs/orchestrator.md](orchestrator.md) — `judge_uncertainties`/`requires_human`/
  `ask_judgments`/`assume_judgments`(M4-4)
- [specs/job-model.md](job-model.md) — `JobType.DESIGN`、`wait_for_human`(ADR-0026)
- [specs/cli.md](cli.md) — `respond`サブコマンド(`design`種別対応、M4-6)
- [specs/template.md](template.md) — 設計文書(`design_document`)が従うMarkdown構造
- ソースコード: `src/gitlab_ai_platform/design/`(`prompts.py` / `parser.py` / `types.py` /
  `errors.py` / `job.py` / `__init__.py`)、
  `src/gitlab_ai_platform/cli/dispatcher.py`(`build_design_handler`)、
  `src/gitlab_ai_platform/cli/respond.py`(`_RESULT_RESOLVERS`)
