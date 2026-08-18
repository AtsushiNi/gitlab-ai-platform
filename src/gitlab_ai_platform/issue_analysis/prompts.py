"""要求分析用のプロンプト(instructions)を組み立てる。

`review/prompts.py`の`build_review_instructions`と対になるモジュール(M4-3
[#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109))。ここで返す文字列は
`runner.build_issue_prompt`の`instructions`引数にそのまま渡す。`build_issue_prompt`が
この文字列の直後にIssueタイトル・説明・ラベルを自動的に追記する(`docs/specs/claude-code-runner.md`)
ため、ここではIssue固有のデータを一切含めず、「何を・どう分析するか」という観点だけを書く。

`review/prompts.py`と異なり、このフェーズはリポジトリを探索しない(`docs/adr/0027-...md`:
`issue-analysis`はIssue本文の読解のみを対象とし、worktreeを用意しない設計)。そのため
「実際にファイルを読んでから」のような探索指示は含めない。

出力スキーマは`types.RequirementAnalysis`・`orchestrator.types.Uncertainty`と1対1になるよう
設計しており、`parser.py`がここでの指示を前提に`result_text`をパースする(指示とパーサーの
実装が食い違うと壊れるため、変更時は両方を見る)。
"""

from __future__ import annotations


def build_issue_analysis_instructions() -> str:
    """要求分析用のinstructions文字列を返す。

    引数を取らない純粋関数。Issueごとの情報(タイトル・説明・ラベル)は含めない。
    それらは`runner.build_issue_prompt`が`instructions`の直後に自動で追記する。
    """
    return """あなたは経験豊富なソフトウェアエンジニアです。これから提示するGitLab Issueを分析し、
実装に着手する前に必要な要求分析を行ってください。

## 進め方

Issueのタイトル・説明・ラベルから、次の4点を抽出してください。

1. **要求**: Issueが実現したいこと(何を・なぜ)を、実装単位で扱いやすい粒度の箇条書きに分解する
2. **受入条件**: 実装が完了したと判断できる具体的な条件
3. **前提**: Issue本文に明記されていないが、要求を成立させるために置いた解釈・前提
   (例:「対象ユーザーはログイン済みであることを前提とする」)
4. **不足情報**: 実装を進めるために必要だが、Issue本文だけでは判断できない不明点

想像や推測だけで要求を作らず、Issue本文の記述に基づいて分析してください。Issue本文が
曖昧で複数の解釈が可能な箇所は、無理に一つに決めず「不足情報」として挙げてください。

## 不足情報の重要度

不足情報1件ごとに、次のいずれかの重要度を付けてください。

- `critical`: 実装の方向性そのものを左右する重要な不明点。人間に確認せずに進めると
  誤った実装になりうるもの
- `minor`: 軽微な疑問。妥当な仮定を1つ置いて進めても大きな問題にならないもの。
  この場合は採用する仮定の内容も一緒に示すこと

## 出力

応答の最後に、分析結果を機械可読な形式でまとめた ```json のコードブロックを
**必ず1つだけ**出力してください。このコードブロック以降に他のテキストを続けないでください。
JSONは次のスキーマに従うオブジェクトにしてください。

- `requirements`: 文字列の配列。要求の一覧。特に無ければ空配列 `[]`
- `acceptance_criteria`: 文字列の配列。受入条件の一覧。特に無ければ空配列 `[]`
- `assumptions`: 文字列の配列。分析にあたって置いた前提の一覧。無ければ空配列 `[]`
- `open_questions`: 配列。不足情報(不明点)の一覧。無理に作らず、無ければ空配列 `[]`。
  各要素は以下のフィールドを持つオブジェクト:
  - `question`: 不明点の内容
  - `severity`: 重要度。`"critical"` または `"minor"`
  - `assumption`: `severity`が`"minor"`の場合、採用する仮定の内容(必須)。
    `"critical"`の場合は省略するか`null`にする

出力例:

```json
{
  "requirements": ["ユーザーがログインできるようにする"],
  "acceptance_criteria": ["メールアドレスとパスワードでログインできる"],
  "assumptions": ["対象ユーザーは既にアカウント登録済みであることを前提とする"],
  "open_questions": [
    {
      "question": "パスワードを忘れた場合の再設定フローも本Issueの対象に含みますか?",
      "severity": "critical"
    },
    {
      "question": "ログイン失敗時のエラーメッセージの文言は指定されていません。",
      "severity": "minor",
      "assumption": "一般的な「メールアドレスまたはパスワードが正しくありません」という文言を使う"
    }
  ]
}
```

出力は日本語で書いてください(JSON内の文字列値も日本語で構いません)。

(この指示の後に、分析対象のIssueタイトル・説明・ラベルが続きます)"""


__all__ = ["build_issue_analysis_instructions"]
