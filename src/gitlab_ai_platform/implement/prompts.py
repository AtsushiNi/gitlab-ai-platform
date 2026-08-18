"""実装用のプロンプト(instructions)を組み立てる。

`plan/prompts.py`の`build_plan_instructions`と対になるモジュール(M4-8 [#114]、ADR-0033)。
ここで返す文字列は`runner.build_issue_prompt`の`instructions`引数にそのまま渡す。
`build_issue_prompt`がこの文字列の直後にIssueタイトル・説明・ラベルを自動的に追記する
(`docs/specs/claude-code-runner.md`)。

`design`/`plan`と異なり、実装フェーズは実際のworktree(`Workspace Manager.prepare_for_issue`が
用意した対象プロジェクトのcheckout)上でClaude Codeを実行する(ADR-0033「決定」)。そのため
このプロンプトは「リポジトリを参照できない」という制約を書かず、逆に「実際にファイルを編集し、
テストを実行し、ローカルにcommitしてよい」ことと、その権限の境界(pushやmerge等は行わない)を
明示する。

出力スキーマは`types.ImplementResult`・`orchestrator.types.Uncertainty`と1対1になるよう設計しており、
`parser.py`がここでの指示を前提に`result_text`をパースする(指示とパーサーの実装が食い違うと
壊れるため、変更時は両方を見る)。
"""

from __future__ import annotations

from ..plan.types import PlanTask
from .types import ImplementInput


def build_implement_instructions(implement_input: ImplementInput) -> str:
    """実装用のinstructions文字列を返す。

    `implement_input`(実装計画フェーズが確定させた計画・タスク一覧・前提)をテキストに
    埋め込む決定的な処理(同じ`implement_input`を渡せば常に同じ文字列を返す)。Issueごとの
    情報(タイトル・説明・ラベル)は含めない。それらは`runner.build_issue_prompt`が
    `instructions`の直後に自動で追記する。
    """
    tasks = _numbered_task_list(implement_input.tasks)
    assumed_uncertainties = _bullet_list(
        implement_input.assumed_uncertainties, empty="(前提はありません)"
    )

    return f"""あなたは経験豊富なソフトウェアエンジニアです。これから提示する実装計画に従って、
実際にこのリポジトリのコードを編集し、テストを実行し、変更をローカルにcommitしてください。

**これは無人実行トラック専用のフェーズです。** 対話型トラック(VS Code拡張)と異なり、
実行中に人間へ質問することはできません。判断に迷う点があっても処理を止めず、妥当な判断を
下したうえで作業を完了させ、判断の内容は出力の`open_questions`で報告してください。

## 入力: これまでのフェーズの結果

以下は前段の要求分析・設計・実装計画の各フェーズで既に確定した内容です。**蒸し返さず、
そのまま前提として実装に使ってください。**

### 実装計画

{implement_input.plan_document}

### タスク一覧(実装順)

{tasks}

### 前提

{assumed_uncertainties}

## あなたに許可されている操作

- このworktree(カレントディレクトリ)配下のファイルの読み取り・編集・新規作成・削除
- テスト・lint・ビルド等、リポジトリで定義されているコマンドの実行(READMEやCI設定
  (`.github/workflows/`等)を確認し、このリポジトリで使われているテストコマンドを
  自分で調べてください)
- `git add` / `git commit` によるローカルへのcommit(1回でも複数回でも構いません。
  タスクの区切りごとにcommitすることを推奨します)

## あなたに許可されていない操作(絶対に行わないこと)

- `git push`(このリポジトリへの実際の反映は別フェーズの責務です。ローカルcommitで
  作業を終えてください)
- `git merge` / `git rebase` / リモートbranchの操作
- このworktreeの外にあるファイルの変更
- 認証情報・環境変数の値をファイルやcommitに書き出すこと

## 進め方

1. タスク一覧を順番に実装してください。各タスクの完了時点でテストを実行し、
   通ることを確認してから次のタスクに進むことを推奨します(1タスク1コミット程度の粒度)
2. 全タスクの実装が終わったら、最終的にテストスイート全体を実行してください
3. テストが最終的に通った場合は、ここまでの変更を(未commitの差分が残らないよう)
   commitしてください。commitメッセージは変更内容が分かる日本語または英語で構いません
4. テストが最終的に通らない場合、無理にcommitしないでください。何を試し、何が
   通らなかったかを出力の`summary`に記録してください

## 不足情報の重要度

実装を進める上で計画だけでは判断できない不明点が生じた場合、1件ごとに次のいずれかの
重要度を付けてください。

- `critical`: 実装の方向性そのものを左右する重要な不明点。誤った判断で実装を進めると
  大きな手戻りになりうるもの
- `minor`: 軽微な疑問。妥当な仮定を1つ置いて進めても大きな問題にならないもの。
  この場合は採用する仮定の内容も一緒に示すこと

## 出力

作業が完了したら(commitできた場合もできなかった場合も)、応答の最後に結果を機械可読な
形式でまとめた ```json のコードブロックを**必ず1つだけ**出力してください。この
コードブロック以降に他のテキストを続けないでください。JSONは次のスキーマに従う
オブジェクトにしてください。

- `summary`: 文字列。実装した内容・テスト結果の要約(コミットメッセージやMR説明の
  下書きとして使える粒度)
- `commit_message`: 文字列またはnull。実際にcommitした場合は使ったコミットメッセージ、
  commitしなかった場合は`null`
- `tests_passed`: 真偽値。最終的にテストが通ったかどうか
- `open_questions`: 配列。不足情報(不明点)の一覧。無理に作らず、無ければ空配列 `[]`。
  各要素は以下のフィールドを持つオブジェクト:
  - `question`: 不明点の内容
  - `severity`: 重要度。`"critical"` または `"minor"`
  - `assumption`: `severity`が`"minor"`の場合、採用する仮定の内容(必須)。
    `"critical"`の場合は省略するか`null`にする

出力は日本語で書いてください(JSON内の文字列値も日本語で構いません)。

(この指示の後に、対象のIssueタイトル・説明・ラベルが続きます)"""


def _numbered_task_list(tasks: tuple[PlanTask, ...]) -> str:
    if not tasks:
        return "(タスクがありません)"
    return "\n".join(
        f"{index}. **{task.title}**: {task.description}"
        for index, task in enumerate(tasks, start=1)
    )


def _bullet_list(items: tuple[str, ...], *, empty: str) -> str:
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


__all__ = ["build_implement_instructions"]
