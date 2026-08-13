# ADR-0006: レビュー結果スキーマと保存レイアウトの設計

- Issue: [#37](https://github.com/AtsushiNi/gitlab-ai-platform/issues/37) (M1-9)
- 状態: 決定

## 背景・制約

- `docs/architecture.md` の設計方針により、Reviewは「レビュープロンプトの設計と、結果スキーマ
  (重要度/ファイル/行/根拠/提案)の定義。JSON(機械可読)とMarkdown(人間可読)の両方を出力する」
  ことが責務。
- `docs/specs/prompts.md`(M1-8)は出力形式の指示を最小限に留めており、「M1-9で結果スキーマと
  あわせて再検討する」ことを明記していた。
- `docs/specs/claude-code-runner.md`(M1-7)により、`RunResult.result_text`はClaude Codeの
  最終応答そのもの(自然文)であり、Runnerはその中身を一切解釈しない。構造化された指摘一覧を
  得るには、Review側(このモジュール)がプロンプトで出力形式を指示し、応答から該当部分を
  抽出・検証する必要がある。
- `docs/guide/reading-results.md`(D-15、未着手)とM2-6(指摘の重要度分類とフィルタ)は
  重要度として `critical` / `major` / `minor` の3段階を前提にしている。

## 決定

### 重要度は `critical` / `major` / `minor` の3段階

D-15・M2-6が前提にしている分類とそのまま合わせる。`Severity`(`review/types.py`)を
`str, Enum`で定義し、State Store(`store/types.py`の`ReviewStatus`)と同じ実装パターンに揃えた。

### Claude Codeには、応答の末尾に ```json フェンスで囲んだJSONオブジェクトを1つだけ出力させる

- プロンプト(`prompts.build_review_instructions`の「出力」セクション)で、`summary`(文字列)と
  `findings`(配列)を持つJSONオブジェクトを応答の最後に1つだけ出力するよう指示する。
  各`findings`要素は`severity`/`file`/`line`/`rationale`/`suggestion`を持ち、`review/types.py`の
  `Finding`と1対1になるフィールド構成にした。
- 応答全体を厳密なJSONにする(自然文を一切許さない)のではなく、確認事項等の自然文を先に書かせた
  上で末尾にJSONブロックを置かせる方式にした。M1-8のプロンプトが持っていた「確信が持てない点は
  確認事項として分けて書く」という意図(`docs/specs/prompts.md`)を残しつつ、パース対象を
  明確に区切るため。
- パース(`review/parser.py`)は```json フェンスを正規表現で探し、複数出力されていた場合
  (指示違反)は最後の1つを採用する。フェンスが全く無い応答(指示より短いdiff無しMR等で
  Claude Codeがマークダウン記法を省略した場合)にも備え、フェンスが見つからなければ応答全文を
  そのままJSONとして解釈することも試みる。どちらも失敗した場合のみ`ReviewOutputParseError`を
  送出する。

### パース失敗は例外として扱い、「ソフトな失敗」の型は作らない

`ReviewOutputParseError`(`review/errors.py`)は`RunnerError`系・`WorkspaceError`系と同じく、
`raw_text`(元の`result_text`)を保持する例外として設計した。`ReviewResult`に
`parse_error: str | None`のような「パース失敗を表すフィールド」を持たせて常に何かを返す設計も
検討したが、既存モジュール(Runner/Workspace/State Store)がいずれも「失敗は例外、成功時のみ
値を返す」という一貫した契約を持っており、これに合わせた方が呼び出し側(将来のオーケストレーター、
M1-12以降)にとって型で失敗を握れて分かりやすい。パース失敗時に何を保存するか
(生の応答をどこかに残すか等)は、呼び出し側が`raw_text`を使って決める。

### 保存先ディレクトリは`project`をパーセントエンコードせず、そのままパスに使う

`workspace`(`_paths.slugify_project`)・`runner`(実行ログ)はどちらも`project`名
(`group/subgroup/project`)をパーセントエンコードして1階層のディレクトリ名に潰している。これは
Windowsの`MAX_PATH`制限とgit worktree/オブジェクトの深いパスを避けるための対策
(`docs/adr/0004-workspace-manager-design.md`)だった。

一方`reviews/<project>/<mr_iid>/<sha>/`配下に置くのは`result.json`/`result.md`/`input.md`/
`run_log.json`という小さいテキストファイルのみで、git worktreeのような深いオブジェクトツリーは
持たない。`store.ReviewRecord.result_path`も既に`"reviews/group/project/1/abc123"`という
素のprojectパスを想定した値を使っている(`tests/gitlab_ai_platform/store/test_sqlite.py`)。
人間がVS Code(GitLab拡張)で結果を探す際、GitLabの実際のnamespace階層とディレクトリ構成が
一致している方が探しやすいと判断し、`reviews/`配下は`project`をエンコードせずそのまま使う
(worktree/ログ用のスラッグ化とは別の判断であることを明記しておく)。

### 索引は単一のJSON配列ファイルではなく、JSON Lines(`index.jsonl`)で追記する

複数レビューを横断する索引を、レビュー1件ごとに`index.jsonl`へ1行追記する方式にした。

- 単一のJSON配列ファイル(`index.json`)にすると、追記のたびに全件を読み込んで配列に1件足して
  書き直す必要があり、件数が増えるほどコストが上がる。
- JSON配列は書き込み中にプロセスが落ちる(電源断・強制終了)とファイル全体が壊れ、既存の全件を
  失うリスクがある。JSON Linesなら直前までの行はそのまま読める。
- MVP(M1)は並列実行(M2-1)が無いWindows単一プロセス運用のため、追記の排他制御までは
  設計しない(将来M2-1で並列化する際に再検討する)。

`review/index.py`の`append_entry`/`read_index`で読み書きする。索引1行分(`IndexEntry`)は
`project`/`mr_iid`/`sha`/`reviewed_at`/`result_dir`/`summary`/重要度ごとの件数を持ち、
一覧画面や将来のCLI(M1-10/11)が全レビューを開かずに一覧表示できるようにする。

### 実行ログはRunnerが書いた`log_path`のファイルを、レビュー結果ディレクトリ配下にもコピーする

Runner(`runner/subprocess_runner.py`)は既に`log_dir`配下に実行ログ(コマンド・stdout・stderr・
所要時間)を保存している。Issue本文(M1-9)が「結果・ログ・入力を保存」と`reviews/`配下への
保存を明記しているため、`storage.save_review`はRunnerのログファイルを`reviews/.../run_log.json`
としてコピーする。Runner側の`log_dir`(実行ログの正本)とReview側の`reviews/`(レビュー結果の
正本)という2つの保存先の責務は変えず、Reviewは複製を持つだけにした(Runnerのログ保存の実装
(`_write_log`)を変更しない)。

## 却下した選択肢

- **`ReviewResult`に`parse_error`フィールドを持たせて例外を投げない設計**: 上記の通り、
  既存モジュールの「失敗は例外」という一貫性を崩すため不採用。
- **索引を単一のJSON配列ファイルにする**: 追記コストとファイル破損時の被害範囲の観点で
  JSON Linesを選んだ(上記参照)。
- **`reviews/`配下も`workspace`/`runner`と同じくprojectをスラッグ化する**: worktree/実行ログの
  ような深いオブジェクトツリーを持たないため、Windowsのパス長制限を回避する実利が薄い。
  `store.ReviewRecord.result_path`の既存の想定(素のprojectパス)とも整合させた。
- **応答全体を厳密なJSON(自然文を許さない)にする**: M1-8のプロンプトが持っていた
  「確信が持てない点を確認事項として自然文で書く」という意図をJSON化すると
  スキーマが煩雑になるため、自然文+末尾JSONブロックの構成にした。
- **重要度をより細かい段階(4段階以上)にする**: D-15・M2-6が3段階を前提にしており、
  独自の段階を導入すると読み方ガイド(D-15)と食い違う。

## 影響

- `review/prompts.py`の「出力」セクションはこのADRの方式(末尾に```json ブロック1つ)に
  従って書き直した(M1-8時点の「厳密なJSON構造は書かない」という設計から変更)。
- `review/parser.py`・`review/markdown.py`・`review/storage.py`・`review/index.py`は
  このADRの決定をそのまま実装している。
- 将来のCLI(M1-10/11)・オーケストレーター(M3以降)は、レビュー実行後
  `parser.parse_review_output` → `storage.save_review` → State Store更新、という順で
  呼び出す形になる想定。`ReviewOutputParseError`を捕捉した場合にState Storeを
  どう更新するか(`FAILED`にする等)はこのADRの対象外とし、呼び出し側の実装時に決める。
