# レビュープロンプト

- 実装場所: `src/gitlab_ai_platform/review/`
- 対応Issue: [#36](https://github.com/AtsushiNi/gitlab-ai-platform/issues/36) (M1-8)
- 関連ADR: なし。プロンプトの合成方式(`instructions`にRunnerがMRの実データを後から追記する形)
  自体は[ADR-0005](../adr/0005-claude-code-runner-design.md)(M1-7)で確定済みで、本Issueは
  その`instructions`引数の中身(レビュー観点の言語化)を決めるものであり、複数の技術的選択肢を
  比較するような設計判断は発生していない
- ステータス: 実装済み(プロンプト設計のみ。結果スキーマはM1-9)

## 責務

MRレビュー用のプロンプト(`instructions`文字列)を設計し、`build_review_instructions()`として
提供する。`docs/architecture.md`のReviewの責務のうち、レビュープロンプトの設計を担当する。
指摘ごとの重要度/ファイル/行/根拠/提案をJSON(機械可読)・Markdown(人間可読)として構造化する
結果スキーマの定義は[M1-9](https://github.com/AtsushiNi/gitlab-ai-platform/issues/37)の責務であり、
本ファイル・本モジュールでは扱わない。

## 前提と非対象

- 前提:
  - `build_review_instructions()`の戻り値は、Claude Code Runner
    ([`claude-code-runner.md`](claude-code-runner.md))の`ClaudeCodeRunner.run`の`instructions`引数に
    そのまま渡すことを想定する
  - Runner側の`_build_prompt`(`runner/subprocess_runner.py`)が、この`instructions`文字列の直後に
    `## Merge Request` / `## Comments` / `## Diff`としてMRタイトル・説明・コメント・diffを
    自動的に追記する。そのため`build_review_instructions()`はMR固有のデータを一切含めず、
    「何を・どう見るか」という観点だけを返す
  - Claude Codeはworktree上(Workspace Managerが用意したclone)で実行されるため、
    プロンプト内で「リポジトリを探索せよ」と指示すれば、実際にファイル読み取り・grep等の
    ツールでリポジトリ全体を参照できる(`docs/architecture.md`のデータフロー5.)
- 非対象:
  - 指摘の構造化(JSON/Markdownへの変換、`reviews/<project>/<mr_iid>/<sha>/`への保存)はM1-9
  - GitLabへの自動投稿の可否判断(`docs/architecture.md`のReviewの境界。最終判断は人間)
  - レビュー対象を絞り込むかどうかの判断(MR Poller、`poller/`の責務)

## 公開インターフェース

実装場所: `src/gitlab_ai_platform/review/prompts.py`。

```python
def build_review_instructions() -> str:
    """MRレビュー用のinstructions文字列を返す。

    引数を取らない純粋関数。MRごとの情報(タイトル・説明・コメント・diff)は含めない。
    それらはRunner側が`instructions`の直後に自動で追記する。
    """
```

`src/gitlab_ai_platform/review/__init__.py`から再エクスポートしている(`from gitlab_ai_platform.review
import build_review_instructions`)。

## プロンプトの構成と設計意図

`build_review_instructions()`が返す文字列は以下の4セクションで構成される。

1. **重視する観点**: タスク整理.mdのM1-8に列挙された7項目
   (致命的なバグ/仕様との不整合/既存コードとの不整合/デグレ/テスト不足/将来問題になる設計/
   明らかな実装ミス)をそのまま列挙する
2. **抑制する観点**: 個人の好みレベルの指摘、大量の些末な指摘の羅列(ノイズ)の2点を、
   「指摘しない」と明示して列挙する。指摘数を絞ることを目的とし、無理に指摘を作らせない
   (後述の「出力」セクションで「特に指摘なし」を許容する記述と対になっている)
3. **進め方**: 単なるdiffの投げ込みレビューにしないための中核部分。「diffの断片だけを見て
   判断しない」ことを明示し、Claude Code自身に次を確認させる:
   - 変更ファイルの、diffに現れていない周辺部分
   - 関連する既存実装(命名・エラーハンドリングの慣習)
   - 対応するテストコードの有無・整合性
   - `docs/`配下の関連ドキュメントとの整合性

   これらはRunnerが渡すMR情報(タイトル・説明・コメント・diff)だけでは判断できず、
   worktree上のリポジトリを実際に探索しないと確認できない項目を意図的に選んでいる
4. **出力**: 指摘ごとに重要度・対象ファイル・行・根拠・改善案を書くよう求める。ただし
   JSON等の厳密なスキーマは指定しない(M1-9で結果スキーマが定義されるまでの暫定)。
   Runnerは`claude -p ... --output-format json`で実行するため、Claude Codeの最終応答は
   常に`RunResult.result_text`としてJSON文字列の1フィールドに収まる
   ([`claude-code-runner.md`](claude-code-runner.md)の「入出力スキーマ」)。これはCLI呼び出しの
   ラッピングであり、`result_text`の中身(本プロンプトが求めるレビュー内容の書式)とは別の話である。
   本Issue(M1-8)ではこの中身の書式を厳密化せず、M1-9で結果スキーマとあわせて再検討する

## エラー時の振る舞い

`build_review_instructions()`は引数を取らない純粋関数で、外部I/Oを行わないため例外は送出しない。

## テスト方針

実装場所: `tests/gitlab_ai_platform/review/test_prompts.py`。

- 戻り値が空でない文字列であること、呼び出すたびに同じ内容を返す(決定的)ことを検証する
- 「重視する観点」「抑制する観点」「探索を促す指示」「出力ガイダンス」の各要素が
  キーワードとして本文に含まれることを検証する
- MR固有のデータ(`## Merge Request`等、Runner側が追記する見出し)を含まないことを検証する
- Runner(`runner/subprocess_runner.py`)の`_build_prompt`と実際に組み合わせ、
  MR情報が指示文の後に重複なく1回だけ現れることを検証する
  (`build_review_instructions()`とRunnerの合成方式の契約を回帰テストとして固定する)

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表のReview行
- [claude-code-runner.md](claude-code-runner.md) — `instructions`引数とMR情報の合成方式
  (`_build_prompt`)
- `references/タスク整理.md` M1-8/M1-9 — レビュー観点・結果スキーマの元ネタ
- ソースコード: `src/gitlab_ai_platform/review/`(`prompts.py` / `__init__.py`)
