# レビュー結果スキーマと保存レイアウト

- 実装場所: `src/gitlab_ai_platform/review/`(`types.py` / `errors.py` / `parser.py` /
  `markdown.py` / `storage.py` / `index.py`)
- 対応Issue: [#37](https://github.com/AtsushiNi/gitlab-ai-platform/issues/37) (M1-9)、
  [#80](https://github.com/AtsushiNi/gitlab-ai-platform/issues/80) (M2-1、索引書き込みの並行安全性)
- 関連ADR: [ADR-0006](../adr/0006-review-output-schema.md)、
  [ADR-0015](../adr/0015-parallel-review-execution.md)
- ステータス: 実装済み

## 責務

Claude Codeの応答(`runner.RunResult.result_text`)から指摘一覧(重要度/ファイル/行/根拠/提案)を
抽出して`ReviewResult`に構造化し(`parser.py`)、JSON(機械可読)とMarkdown(人間可読)の両方の
形式で`reviews/<project>/<mr_iid>/<sha>/`に保存する(`storage.py`)。あわせて、その回のレビューで
Runnerに渡した入力プロンプトと実行ログのコピーも同じディレクトリに保存し、複数レビューを
横断できる索引(`index.jsonl`)を管理する(`index.py`)。`docs/architecture.md`のReviewの責務のうち、
プロンプト設計(M1-8)と対になる「結果スキーマ」側を担当する。

## 前提と非対象

- 前提:
  - `parser.parse_review_output`は`RunResult`(`result_text`単体ではない)を受け取る。
    `runner/types.py`が「`result_text`の内容だけで成否判定してはならない。`is_error`と
    `permission_denials`を必ず確認すること」と定めているため、その確認を呼び出し側の
    自己申告に頼らず構造的に強制する(`is_error`がTrueなら`result_text`の中身を解釈せず
    `ReviewOutputParseError`を送出する)。`result_text`自体は`prompts.build_review_instructions`
    (`docs/specs/prompts.md`)の指示に従ってClaude Codeが応答したものであることを前提とする。
    プロンプトの「出力」セクションとパーサーの実装は1対1の契約であり、どちらかを変更する場合は
    もう一方も見直すこと。
  - `storage.save_review`は、対象commitについてまだ保存していないことを前提とする(二重保存の
    検出はState Store, `store/`の責務であり、このモジュールは行わない)。
  - `storage.save_review`に渡す`run_log_path`は、Claude Code Runner(`runner/`)の
    `RunResult.log_path`をそのまま使うことを想定する。
  - M2-1(#80)以降、`index.append_entry`は複数のワーカースレッドから同時に呼ばれうる
    (`ReviewWorkerPool`、`docs/specs/cli.md`)。モジュール内の`threading.Lock`で追記を
    直列化しており、行が混ざって壊れることはない([ADR-0015](../adr/0015-parallel-review-execution.md)
    参照)。複数プロセスからの同時書き込みはこのロックの対象外で、`ProcessLock`
    (`cli/lock.py`)が別途防ぐ
- 非対象:
  - GitLabへの自動投稿はしない(`docs/architecture.md`のReviewの境界。最終判断は人間)。
  - パース失敗時に何を保存するか・State Storeをどう更新するかは呼び出し側の責務
    (`ReviewOutputParseError`を送出するところまでがこのモジュールの責務。「エラー時の振る舞い」
    節参照)。
  - 索引(`index.jsonl`)の検索・フィルタ・表示は行わない(`read_index`で全件を返すのみ。
    絞り込みや表示はCLI, M1-10/11の責務)。
  - レビューするか否かの判断、二重レビューの防止(State Store, `store/`の責務)。

## 公開インターフェース

実装場所: `src/gitlab_ai_platform/review/`。`__init__.py`から以下をすべて再エクスポートしている。

```python
def parse_review_output(run_result: RunResult) -> ReviewResult:
    """`run_result`から結果スキーマ(`summary`/`findings`)を抽出する。

    `run_result.is_error`がTrueなら`result_text`を解釈せず`ReviewOutputParseError`を
    送出する。`permission_denials`が空でなければ警告ログのみ残し、解析は続行する。
    JSONを抽出できない場合も`ReviewOutputParseError`を送出する。
    """


def render_markdown(
    result: ReviewResult, *, project: str, mr_iid: int, sha: str
) -> str:
    """`result`を人間可読なMarkdown文字列に整形する。"""


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
    """`result`を`<root>/<project>/<mr_iid>/<sha>/`へ保存し、索引に1行追記する。"""


def append_entry(root: Path | str, entry: IndexEntry) -> None:
    """索引に`entry`を1行追記する。"""


def read_index(root: Path | str) -> tuple[IndexEntry, ...]:
    """索引の全件を、追記順(古い順)で返す。索引ファイルが無ければ空を返す。"""
```

レビュープロンプト本体(`build_review_instructions`)は`docs/specs/prompts.md`(M1-8)を参照。

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/review/types.py`。

| 型 | フィールド | 補足 |
|---|---|---|
| `Severity` (str Enum) | `CRITICAL="critical"`, `MAJOR="major"`, `MINOR="minor"` | `docs/guide/reading-results.md`(D-15)・M2-6が前提にする3段階 |
| `Finding` (frozen dataclass) | `severity: Severity`, `file: str`, `line: int \| None`, `rationale: str`, `suggestion: str` | 指摘1件。`line`は特定の行に紐づかない指摘では`None` |
| `ReviewResult` (frozen dataclass) | `summary: str`, `findings: tuple[Finding, ...]` | `parse_review_output`の戻り値。識別情報(project等)は持たない |
| `ReviewPaths` (frozen dataclass) | `dir: Path`, `result_json: Path`, `result_md: Path`, `input_path: Path`, `log_path: Path` | `save_review`の戻り値 |
| `IndexEntry` (frozen dataclass) | `project: str`, `mr_iid: int`, `sha: str`, `reviewed_at: datetime`, `result_dir: str`, `summary: str`, `critical_count: int`, `major_count: int`, `minor_count: int` | 索引1行分 |

### Claude Codeに要求するJSON出力スキーマ

`prompts.build_review_instructions`が、応答の末尾に```json フェンスで出力するよう指示する
形式(`Finding`と1対1)。

```json
{
  "summary": "レビュー全体の要約(1〜3文)。指摘が無ければ「特に指摘なし」",
  "findings": [
    {
      "severity": "critical",
      "file": "src/app/auth.py",
      "line": 42,
      "rationale": "根拠",
      "suggestion": "改善案"
    }
  ]
}
```

`severity`は`"critical"`/`"major"`/`"minor"`のいずれか(大文字小文字は問わない、
`parser._build_finding`が`.lower()`で吸収する)。`line`は整数または`null`。

### 保存レイアウト

```text
<root>/<project>/<mr_iid>/<sha>/
    result.json    # {"summary": ..., "findings": [...]} (Findingのseverityは文字列化)
    result.md      # render_markdownの出力
    input.md        # Runnerに渡した完成後のプロンプト全文
    run_log.json    # RunResult.log_pathのコピー(Runnerの実行ログ)
<root>/index.jsonl  # レビュー1件につき1行(IndexEntryをJSON化したもの)
```

`project`はGitLabの`group/subgroup/project`をエンコードせずそのままディレクトリ階層にする
(`workspace`/`runner`の`slugify_project`とは異なる方針。理由は[ADR-0006](../adr/0006-review-output-schema.md)参照)。

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/review/errors.py`。

- `ReviewError(Exception)` — Review経由の処理が失敗したことを表す基底例外。
- `ReviewOutputParseError(ReviewError)` — `result_text`から結果スキーマのJSONを抽出できない
  (```json フェンスも全文パースも失敗した)、またはJSONは取れたがスキーマを満たさない
  (`findings`が配列でない、`severity`が`critical`/`major`/`minor`のいずれでもない、
  `file`/`rationale`/`suggestion`が空でない文字列でない、`line`が整数でもnullでもない、等)場合に
  `parse_review_output`が送出する。`raw_text`属性に元の`result_text`をそのまま保持しており、
  呼び出し側はこれを使って人間が読める形で内容を確認できる(State Storeを`FAILED`に遷移させる
  等の具体的な対応は呼び出し側の責務、[ADR-0006](../adr/0006-review-output-schema.md)参照)。
- `save_review`・`append_entry`・`read_index`はこのモジュール独自の例外を送出しない
  (ファイルI/Oの失敗は標準の`OSError`系がそのまま伝播する)。

## テスト方針

実装場所: `tests/gitlab_ai_platform/review/`(`src/`をミラー、[ADR-0001](../adr/0001-repository-structure.md))。

- `test_types.py`: 各dataclassの`frozen=True`、`Severity`の値、`Finding.line`が`None`を
  許容することを検証する。
- `test_errors.py`: `ReviewOutputParseError`が`ReviewError`のサブクラスであること、
  `raw_text`を保持することを検証する。
- `test_parser.py`: ```json フェンス付き応答・フェンス無しの全文JSON・複数フェンスがある場合に
  最後を採用すること・`suggestion`内に入れ子の```があっても正しく抽出できること・
  `line: null`を許容すること・スキーマ違反(`findings`が配列でない、`severity`が不正、
  必須フィールド欠落、`line`が整数でも`null`でもない、`line`が`bool`)、および
  `run_result.is_error`がTrueの場合に`result_text`の中身を解釈せず
  `ReviewOutputParseError`を送出することを検証する。実際にClaude Codeを起動するテストは行わない
  (テスト用ヘルパーで`RunResult`を組み立てて直接与える)。
- `test_markdown.py`: 指摘0件の場合の表示、重要度順(critical→major→minor)の並び替え、
  根拠・改善案が本文に含まれること、`line`が`None`の場合に`file:None`のような表示にならないことを
  検証する。
- `test_index.py`: 索引ファイルが無い場合に空タプルを返すこと、追記した内容がそのまま
  往復すること、複数件を追記した場合の順序を検証する。(M2-1) 多数のスレッドが同時に
  `append_entry`を呼んでも行が混ざらず、全件が正しく記録されることも検証する。
- `test_storage.py`: `tmp_path`配下に`result.json`/`result.md`/`input.md`/`run_log.json`が
  期待するパスに書き出されること、`result.json`が`Finding`と往復可能なこと、`run_log_path`の
  内容がそのままコピーされること、`reviewed_at`省略時に現在時刻が使われること、索引への
  追記(重要度ごとの件数)が正しいことを検証する。実DB・実Runnerには接続しない
  (`tmp_path`上のファイルI/Oのみ、CLAUDE.mdのテスト方針)。
- `test_prompts.py`(`docs/specs/prompts.md`と共有): 「出力」セクションが```json ブロックと
  `Finding`のフィールド名(`summary`/`findings`/`severity`/`critical`/`major`/`minor`/`file`/
  `line`/`rationale`/`suggestion`)を含むことを検証する回帰テストを追加した。

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表のReview行
- [ADR-0006: レビュー結果スキーマと保存レイアウトの設計](../adr/0006-review-output-schema.md)
- [ADR-0015: 並列レビュー実行の設計](../adr/0015-parallel-review-execution.md) —
  `index.append_entry`の並行書き込み排他の設計判断
- [prompts.md](prompts.md) — レビュープロンプト(M1-8)。「出力」セクションはこのモジュールの
  スキーマと1対1の契約
- [claude-code-runner.md](claude-code-runner.md) — `RunResult.result_text`/`log_path`の由来
- [docs/guide/reading-results.md](../guide/reading-results.md)(D-15) — 重要度の判断基準・
  指摘の読み方(このスキーマと対で維持する)
- ソースコード: `src/gitlab_ai_platform/review/`
  (`types.py` / `errors.py` / `parser.py` / `markdown.py` / `storage.py` / `index.py` / `__init__.py`)
