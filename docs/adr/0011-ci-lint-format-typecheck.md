# ADR-0011: CI(GitHub Actions)のツール構成

- Issue: [#4](https://github.com/AtsushiNi/gitlab-ai-platform/issues/4) (M0-4)
- 状態: 決定

## 背景・制約

`.github/workflows/`が未整備で、lint・format・type check・testのいずれもCI上で自動実行
されていなかった。`pyproject.toml`の`dev` extrasには`pytest`のみが入っており、
lint/format/type checkのツール自体も未導入だった。

このリポジトリ自体の開発は複数セッション・複数コントリビューターの並行作業が前提
(`CLAUDE.md`「並行セッション・worktreeの運用」)であり、レビュー前に機械的に検出できる
問題(未使用import、明らかな型不整合、フォーマット崩れ)はCIで弾き、レビューは設計判断に
集中できるようにしたい。

着手時点で`src/gitlab_ai_platform/`配下に53ファイル、`tests/`配下に57ファイル(pytest 359件)
という既存コード量があり、ゼロから厳格なルールを敷くと大量の修正が必要になる。
「既存コードのスタイルを壊さない程度の妥当な設定」を優先し、必要なら段階的導入とする方針
(Issue本文の指示)で進めた。

## 決定

### ツール構成: ruff(lint + format) / mypy(type check) / pytest(test、既存)

- **lint + format**: [ruff](https://docs.astral.sh/ruff/)。`ruff check`と`ruff format --check`の
  2コマンドで、従来`flake8` + `isort` + `black`の3ツールが担っていた領域を1バイナリでカバーできる。
  Rust製で実行が高速なこともCI時間の観点で有利
- **type check**: [mypy](https://mypy-lang.org/)。Pythonの型チェッカーとして最も広く使われており、
  `pyright`等の代替より`pyproject.toml`一箇所に設定を集約しやすい
- **test**: 既存の`pytest`をそのまま使う(変更なし)

いずれも`pyproject.toml`の`dev` extrasに追加し(`ruff>=0.16`、`mypy>=1.10`)、
`pip install -e ".[dev]"`だけで開発環境が揃うようにした。

### ruffのルール選定: 既定のフルルールセットではなく`E, F, I, UP, B`に絞る

導入時点でruffの既定ルール(バージョン0.16時点で400以上のルールが有効)をそのまま適用すると
183件のlintエラーが出た。自動修正(`--fix`)で153件、`ruff format`適用で残りの大半を解消できたが、
最終的に以下のカテゴリに絞って`select`した(`pyproject.toml`の`[tool.ruff.lint]`にコメント付きで記載):

- `E`/`F`: pycodestyle・pyflakes。未使用import・構文上のバグなど実害の大きい検出
- `I`: isort相当のimport整形
- `UP`: pyupgrade。現行Pythonバージョン(3.11)向けの書き方への統一
- `B`: flake8-bugbear。バグになりやすいパターンの検出

`DTZ`(timezone-naive datetime)・`C408`(`dict()`リテラル化)・`PLW1510`
(`subprocess.run`の`check`未指定)・`PYI034`・`PIE810`・`RUF022`(`__all__`のソート)等、
既定では有効だが今回`select`に含めなかったカテゴリは、①既存コードへの影響が大きい、
②ノイズの多いスタイル指摘で実害が小さい、のいずれかに該当すると判断した。
必要になった時点で個別にIssueを立てて追加する。

さらに以下2ルールを個別に`ignore`した(理由は`pyproject.toml`側にもコメントを残した):

- **`E501`(行長超過)**: `CLAUDE.md`の規約でソースコメントは日本語必須であり、説明的な
  日本語コメント・docstring・プロンプトテンプレート文字列(`review/prompts.py`)は
  88文字を自然に超える。コード自体の折り返しは`ruff format`が担うため、コメント・文字列の
  行長までlintで縛らない
- **`UP042`(`class X(str, Enum)` → `enum.StrEnum`への書き換え提案)**: `CommitActionType`
  (`gitlab_adapter/types.py`)・`Severity`(`review/types.py`)・`ReviewStatus`
  (`store/types.py`)が該当。`StrEnum`は`__str__`の返り値やJSON変換時の型解決が
  `(str, Enum)`と微妙に異なり、`docs/specs/review-output.md`等が規定する出力フォーマットに
  影響しうる。挙動を変えない範囲でのCI整備という本Issueのスコープ外とし、
  移行するなら別Issueで専用テストとともに行う

### `ruff format`のline-lengthは既定の88のまま

行長を伸ばして既存コードの折り返しを避ける案(120等)も検討したが、多くのコード行を
「意図せず1行に展開し直す」形の差分が広範囲に発生し、レビュー負荷が上がると判断して見送った。
既定の88のまま`ruff format`を1回適用し(48ファイルが対象、機械的なフォーマット差分のみ)、
今後はこのフォーマットを正とする。

### mypyは追加設定を最小限にする

`python_version = "3.11"`と`warn_unused_configs` / `warn_redundant_casts` /
`warn_unused_ignores`のみを設定し、`strict = true`等の厳格モードは今回導入しない。
既定設定のままでも`mypy src`は2件のエラーのみだったため(後述)、まずはこの基準で
CIに組み込み、型カバレッジを上げたくなった時点で段階的に強化する。

### 既存コードの修正方針: 挙動を変えない範囲でのみ修正する

上記のルール選定・除外を行った上で、`ruff check` / `ruff format --check` / `mypy src` /
`pytest`がすべて通ることをローカルで確認し、残った指摘は以下の方針で解消した(すべて
挙動を変えない修正):

- `ruff --fix` / `ruff format`による自動修正(import整形、`dict()`→リテラル化 は対象外だが
  未選択のため無関係、末尾コンマ等)
- `review/parser.py`の未使用`import re`の削除
- `adapter_mcp_server/tools.py`の`ToolFactory`型エイリアスの修正: [#69](
  https://github.com/AtsushiNi/gitlab-ai-platform/issues/69)で各`_make_xxx`ファクトリ関数に
  `default_project`引数が追加された際、この型エイリアスの更新が漏れていた
  (`Callable[[GitLabAdapter], ...]` → `Callable[[GitLabAdapter, str | None], ...]`)。
  mypy導入で初めて検出できた既存の型不整合で、実行時の挙動には影響しない
- `cli/lock.py`の`ProcessLock._file`の型注釈を`object | None`から`IO[str] | None`に修正。
  `object`型では`close()`呼び出しがmypyの`attr-defined`エラーになっていた
- `tests/gitlab_ai_platform/review/test_prompts.py`の1行に収まっていたリスト内包の`for`文を
  複数行のリスト変数に分割(`E501`を回避する意図だったが、最終的に`E501`自体をignoreしたため
  必須ではなくなった。可読性向上として残した)

## 却下した選択肢

- **flake8 + isort + black の組み合わせ**: 実現できることはruffとほぼ同等だが、3ツール分の
  依存・設定ファイル・実行コマンドが必要になる。ruffが1バイナリで同等の機能(fix含む)を
  高速に提供するため不採用
- **pyright(type check)**: TypeScriptプロジェクトでのVS Code統合に強みがあるが、このリポジトリは
  Pythonのコミュニティ標準に寄せる方針(`mcp`等の主要依存も型ヒント前提)を優先し、
  より広く使われているmypyを採用
- **ruffの既定フルルールセットをそのまま`select`せず適用する**: 「決定」節の通り、
  既存コードへの影響・ノイズの大きさを理由に見送った。将来的にルールを追加する余地は残す
- **`--fix`で自動修正可能な指摘も含めて全カテゴリを有効化し、大規模な一括修正コミットを打つ**:
  今回のCI整備の主目的(CIパイプライン自体の追加)から外れる規模の変更になるため見送り。
  ルール選定自体を「妥当な範囲」に絞ることで、今回のPRの差分を機械的なフォーマット適用と
  少数の型修正に収めた

## 影響

- `pyproject.toml`の`dev` extrasに`ruff`・`mypy`を追加。`[tool.ruff]` / `[tool.ruff.lint]` /
  `[tool.mypy]`セクションを新設
- `.github/workflows/ci.yml`を新設。push・pull_requestで
  `ruff check .` → `ruff format --check .` → `mypy src` → `pytest`の順に実行する
- `ruff format`の初回適用により、既存コード48ファイルにフォーマット差分が入った
  (挙動に影響しない機械的な差分)
- `adapter_mcp_server/tools.py`の`ToolFactory`型エイリアス、`cli/lock.py`の`ProcessLock._file`の
  型注釈を修正(挙動に影響しない)
- 以降、コードの挙動を変える変更を行う開発者は、CIが要求する4チェックをローカルでも
  実行してからPRを出すことが期待される(`pip install -e ".[dev]"`で環境を揃えられる)
