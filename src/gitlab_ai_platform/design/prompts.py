"""設計用のプロンプト(instructions)を組み立てる。

`issue_analysis/prompts.py`の`build_issue_analysis_instructions`と対になるモジュール
(M4-6 [#112](https://github.com/AtsushiNi/gitlab-ai-platform/issues/112))。ここで返す文字列は
`runner.build_issue_prompt`の`instructions`引数にそのまま渡す。`build_issue_prompt`が
この文字列の直後にIssueタイトル・説明・ラベルを自動的に追記する(`docs/specs/claude-code-runner.md`)。

`build_issue_analysis_instructions`(引数無しの純粋関数)と異なり、こちらは`DesignInput`
(要求分析フェーズが確定させた要求・受入条件・前提)を引数に取る。設計はIssue本文の読解だけでは
成立せず、前段フェーズの結果を踏まえる必要があるため(`docs/architecture.md`の
「Issue → 要求分析 → 設計 → 実装 → MR作成」パイプライン)。ただし`DesignInput`の内容を
テキストに埋め込むだけの決定的な処理であり、Issue固有のデータ(タイトル・説明・ラベル)は
含めない点は`build_issue_analysis_instructions`と同じ。

ADR-0029の決定により、設計フェーズはWorkspace Manager(worktree)を使わずリポジトリを
探索しない(`issue-analysis`と同じくADR-0027の設計を踏襲)。そのため、実装詳細のファイルパスや
既存コードの断定的な記述を避け、方針レベルの設計に留めるよう明示的に指示する。

出力スキーマは`types.DesignResult`・`orchestrator.types.Uncertainty`と1対1になるよう設計しており、
`parser.py`がここでの指示を前提に`result_text`をパースする(指示とパーサーの実装が食い違うと
壊れるため、変更時は両方を見る)。
"""

from __future__ import annotations

from .types import DesignInput


def build_design_instructions(design_input: DesignInput) -> str:
    """設計用のinstructions文字列を返す。

    `design_input`(要求分析フェーズが確定させた要求・受入条件・前提)をテキストに埋め込む
    決定的な処理(同じ`design_input`を渡せば常に同じ文字列を返す)。Issueごとの情報
    (タイトル・説明・ラベル)は含めない。それらは`runner.build_issue_prompt`が`instructions`の
    直後に自動で追記する。
    """
    requirements = _bullet_list(
        design_input.requirements, empty="(要求分析フェーズで確定した要求はありません)"
    )
    acceptance_criteria = _bullet_list(
        design_input.acceptance_criteria, empty="(受入条件はありません)"
    )
    assumptions = _bullet_list(
        (*design_input.assumptions, *design_input.assumed_uncertainties),
        empty="(前提はありません)",
    )

    return f"""あなたは経験豊富なソフトウェアアーキテクトです。これから提示する要求分析結果を元に、
実装に着手する前の設計をレビュー可能な成果物として作成してください。

**これは無人実行トラック専用のフェーズです。** 人間と対話しながら実装を進める対話型トラック
(VS Code拡張)では、対話の中で自然に設計内容が確認されるため、このフェーズ自体が不要です。
このフェーズはIssueに無人実行ラベルが付いた場合にのみ実行されます。

## 入力: 要求分析フェーズの結果

以下は前段の要求分析フェーズで既に確定した内容です。**蒸し返さず、そのまま前提として設計に
使ってください。**

### 要求

{requirements}

### 受入条件

{acceptance_criteria}

### 前提

{assumptions}

## 制約: リポジトリを参照できません

このフェーズはIssue本文と上記の要求分析結果のみを入力とし、対象プロジェクトの実際のソース
コードを参照できません。そのため、次の点に注意してください。

- 実在するかどうか確認できないファイルパス・関数名・クラス名を断定的に書かない
- 「〇〇というファイルを変更する」のような実装詳細ではなく、「〇〇という責務を持つコンポーネントを
  追加する」のような方針レベルで設計する
- 既存コードとの整合性が必要な判断(命名規則・アーキテクチャパターン等)は、実装フェーズが
  実際のコードを見て解決する前提を明示する

## 進め方

次の構成でMarkdown形式の設計文書を作成してください(`docs/specs/template.md`のフォーマットに
準拠する形式):

1. **責務**: このIssueの実装が何を行うか(1〜3文)
2. **前提と非対象**: 前提となる外部条件、意図的に対象外にしたこと
3. **公開インターフェース**: 呼び出し側から見える関数・クラス・メソッドの想定シグネチャ
4. **入出力スキーマ**: やりとりするデータの型定義
5. **エラー時の振る舞い**: どんな例外・エラーを扱うか
6. **テスト方針**: 何を保証するテストが必要か

## 不足情報の重要度

設計を進める上で要求分析結果だけでは判断できない不明点が生じた場合、1件ごとに次のいずれかの
重要度を付けてください。

- `critical`: 設計の方向性そのものを左右する重要な不明点。人間に確認せずに進めると
  誤った設計になりうるもの
- `minor`: 軽微な疑問。妥当な仮定を1つ置いて進めても大きな問題にならないもの。
  この場合は採用する仮定の内容も一緒に示すこと

## 出力

応答の最後に、設計結果を機械可読な形式でまとめた ```json のコードブロックを
**必ず1つだけ**出力してください。このコードブロック以降に他のテキストを続けないでください。
JSONは次のスキーマに従うオブジェクトにしてください。

- `design_document`: 文字列。上記の構成に従って作成した設計文書全文(Markdown)
- `open_questions`: 配列。不足情報(不明点)の一覧。無理に作らず、無ければ空配列 `[]`。
  各要素は以下のフィールドを持つオブジェクト:
  - `question`: 不明点の内容
  - `severity`: 重要度。`"critical"` または `"minor"`
  - `assumption`: `severity`が`"minor"`の場合、採用する仮定の内容(必須)。
    `"critical"`の場合は省略するか`null`にする

出力は日本語で書いてください(JSON内の文字列値も日本語で構いません)。

(この指示の後に、対象のIssueタイトル・説明・ラベルが続きます)"""


def _bullet_list(items: tuple[str, ...], *, empty: str) -> str:
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


__all__ = ["build_design_instructions"]
