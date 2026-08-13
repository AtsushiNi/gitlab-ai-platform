# レビュープロンプト

- 実装場所: `src/gitlab_ai_platform/review/`
- 対応Issue: [#36](https://github.com/AtsushiNi/gitlab-ai-platform/issues/36) (M1-8)
- 関連ADR: なし。プロンプトの合成方式(`instructions`にRunnerがMRの実データを後から追記する形)
  自体は[ADR-0005](../adr/0005-claude-code-runner-design.md)(M1-7)で確定済みで、本Issueは
  その`instructions`引数の中身(レビュー観点の言語化)を決めるものであり、複数の技術的選択肢を
  比較するような設計判断は発生していない。「出力」セクションのJSON出力形式に関する設計判断は
  [ADR-0006](../adr/0006-review-output-schema.md)(M1-9)に記録した
- ステータス: 実装済み(プロンプト設計 + 結果スキーマに対応した出力形式の指示。
  結果スキーマ自体の定義・保存は[review-output.md](review-output.md)、M1-9)

## 責務

MRレビュー用のプロンプト(`instructions`文字列)を設計し、`build_review_instructions()`として
提供する。`docs/architecture.md`のReviewの責務のうち、レビュープロンプトの設計を担当する。
指摘ごとの重要度/ファイル/行/根拠/提案をJSON(機械可読)・Markdown(人間可読)として構造化する
結果スキーマの定義・保存は[review-output.md](review-output.md)
([M1-9](https://github.com/AtsushiNi/gitlab-ai-platform/issues/37))の責務であり、本ファイル・
本モジュールでは扱わない。ただし「出力」セクション(下記)は、そのスキーマ
(`review.types.Finding`)とClaude Codeの応答形式を1対1に対応させる契約を持つため、
両ファイルは対で変更すること。

## 前提と非対象

- 前提:
  - `build_review_instructions()`の戻り値は、Claude Code Runner
    ([`claude-code-runner.md`](claude-code-runner.md))の`ClaudeCodeRunner.run`の`instructions`引数に
    そのまま渡すことを想定する
  - Runner側の`_build_prompt`(`runner/subprocess_runner.py`)が、この`instructions`文字列の直後に
    `## Merge Request`(常に追記)、`## Comments`(コメントがある場合のみ)、
    `## Diff`(diffがある場合のみ)としてMRタイトル・説明・コメント・diffを自動的に追記する。
    そのため`build_review_instructions()`はMR固有のデータを一切含めず、
    「何を・どう見るか」という観点だけを返す
  - Claude Codeはworktree上(Workspace Managerが用意したclone)で実行されるため、
    プロンプト内で「リポジトリを探索せよ」と指示すれば、実際にファイル読み取り・grep等の
    ツールでリポジトリ全体を参照できる(`docs/architecture.md`のデータフロー5.)。
    ただし読み取りツールの権限(`allowed_tools`/`permission_mode`)はRunner呼び出し側が
    付与する責務であり(ADR-0005)、この探索指示を実際に活かせるかは呼び出し側の設定次第
    (現時点ではまだ`review/`を呼び出すオーケストレーター(M1-12)が存在しないため未検証)
- 非対象:
  - 指摘の構造化(Claude Codeの応答からのJSON抽出・検証、Markdownへの変換、
    `reviews/<project>/<mr_iid>/<sha>/`への保存)は[review-output.md](review-output.md)(M1-9)
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
4. **出力**: まず自然文で確認事項(確信が持てない点)を書かせたうえで、応答の末尾に
   ```json フェンスで囲んだJSONオブジェクトを1つだけ出力させる。JSONは`summary`(文字列)と
   `findings`(配列、要素は`severity`/`file`/`line`/`rationale`/`suggestion`)を持ち、
   `review.types.Finding`(M1-9、[review-output.md](review-output.md))と1対1になる
   フィールド構成にしている。指摘が無い場合は`findings`を空配列にする(無理に指摘を作らせない
   という2.の意図をJSON化後も維持する)。この形式の設計判断は
   [ADR-0006](../adr/0006-review-output-schema.md)に記録した。

   Runnerは`claude -p ... --output-format json`で実行するため、Claude Codeの最終応答は
   常に`RunResult.result_text`としてJSON文字列の1フィールドに収まる
   ([`claude-code-runner.md`](claude-code-runner.md)の「入出力スキーマ」)。これはCLI呼び出しの
   ラッピングであり、`result_text`の中身(本プロンプトが求めるレビュー内容の書式)とは別の話である。
   `review.parser.parse_review_output`が`result_text`の中身から上記の```json ブロックを
   抽出・検証する(詳細は[review-output.md](review-output.md))

## エラー時の振る舞い

`build_review_instructions()`は引数を取らない純粋関数で、外部I/Oを行わないため例外は送出しない。

## テスト方針

実装場所: `tests/gitlab_ai_platform/review/test_prompts.py`。

- 戻り値が空でない文字列であること、呼び出すたびに同じ内容を返す(決定的)ことを検証する
- 「重視する観点」「抑制する観点」「探索を促す指示」「出力ガイダンス」の各要素が
  キーワードとして本文に含まれることを検証する
- 「出力」セクションが```json フェンスと`Finding`のフィールド名(`summary`/`findings`/
  `severity`/`critical`/`major`/`minor`/`file`/`line`/`rationale`/`suggestion`)を
  含むことを検証する(`review.parser.parse_review_output`が前提とするスキーマとの
  整合の回帰テスト)
- MR固有のデータ(`## Merge Request`等、Runner側が追記する見出し)を含まないことを検証する
- Runner(`runner/subprocess_runner.py`)の`_build_prompt`と実際に組み合わせ、
  MR情報が指示文の後に重複なく1回だけ現れることを検証する
  (`build_review_instructions()`とRunnerの合成方式の契約を回帰テストとして固定する)

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表のReview行
- [claude-code-runner.md](claude-code-runner.md) — `instructions`引数とMR情報の合成方式
  (`_build_prompt`)
- [review-output.md](review-output.md) — 結果スキーマ・保存レイアウト(M1-9)。「出力」
  セクションと1対1の契約
- [ADR-0006](../adr/0006-review-output-schema.md) — 「出力」セクションのJSON形式の設計判断
- `references/タスク整理.md` M1-8/M1-9 — レビュー観点・結果スキーマの元ネタ
- ソースコード: `src/gitlab_ai_platform/review/`(`prompts.py` / `__init__.py`)
