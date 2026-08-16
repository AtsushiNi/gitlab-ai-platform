# ADR-0013: ドキュメントCIのツール構成

- Issue: [#16](https://github.com/AtsushiNi/gitlab-ai-platform/issues/16) (D-12)
- 状態: 決定

## 背景・制約

`docs/`配下に要件定義・アーキテクチャ・ADR・spec・運用ガイド等、38件のMarkdown文書があり、
相互に相対リンク(`[text](path)`や`[text](path#anchor)`形式)で参照し合っている。CLAUDE.mdの
運用ルールで「コードの挙動を変える変更は対応docsも同じPR/コミットで更新する」ことを求めている
が、レビューだけでリンク切れ・壊れた図・書式の乱れを防ぐには限界がある。

`docs/architecture.md`には全体像を示すmermaidダイアグラムが2つ埋め込まれており、これも
構文が壊れるとレンダリングされず情報が失われる。

このリポジトリ自体の開発用CI(`.github/workflows/ci.yml`、[ADR-0011](0011-ci-lint-format-typecheck.md))
は既にruff/mypy/pytestで整備済みだが、Pythonのコード品質のみを見ており、Markdown文書の
整合性はスコープ外。

[ADR-0001](0001-repository-structure.md)で「外部依存は最小限に絞る」方針を決めているが、
これはWindows側(管理者権限なし・外部ダウンロード制限あり)で動くレビューツール本体
(`src/gitlab_ai_platform/`)向けの制約であり、GitHub Actions (ubuntu-latest) 上でのみ動く
このリポジトリ自体の開発用CIには適用されない(Issue本文の指示)。そのため、Node.js/npm
エコシステムのツールを含めて選定対象にできる。

## 決定

既存の`ci.yml`(コード用)とは別に`.github/workflows/docs-ci.yml`を新設する。理由:

- ツールチェーンがNode.js中心(既存CIはPython中心)で、混在させるとセットアップ手順が読みにくくなる
- `docs/**`に関係する変更のときだけ実行されるよう`paths`フィルタで絞りたい(コードだけの変更で
  Node.jsのセットアップコストを払わせたくない)。既存`ci.yml`は`paths`指定なしで全変更に反応する
  設計のままにし、役割を分離する

`docs-ci.yml`は3つのジョブで構成し、それぞれ独立して並列実行する(1つが失敗しても他の結果が
分かるように)。

### リンク切れ検出: [lychee](https://github.com/lycheeverse/lychee) (`lycheeverse/lychee-action`)

- Rust製の単一バイナリ。`lycheeverse/lychee-action`がGitHub Releaseから直接バイナリを
  取得するだけで、npmの依存ツリーを持ち込まない(「外部依存を増やしすぎない」方針とも相性が良い)
- Markdown内の相対リンクとアンカー(`--include-fragments`ではなく`include_fragments = "full"`、
  v0.24時点の設定名)の両方をチェックできる。GitHubの見出しアンカー生成規則
  (`github-slugger`ライブラリ相当)に対応しており、日本語見出しのアンカーも正しく
  検証できることをローカルで確認済み
- 外部URL(このリポジトリでは`github.com`のIssueリンクが114件、`python.org`等が数件)も
  チェック対象にする。CIの不安定化を避けるため、`lychee.toml`で`timeout = 20`
  `retry_wait_time = 5` `max_retries = 3`を設定。`github.com`は`lychee-action`の
  `token`入力(既定で`github.token`)経由で認証付きチェックになり、未認証時のレート制限を
  回避する
- 設定は`lychee.toml`に集約(ワークフローのargsを短く保つ)。`docs/specs/template.md`のみ
  除外を検討したが、リンク切れチェック単体では`[ADR-XXXX](../adr/XXXX-xxx.md)`という
  プレースホルダが実在しないパスを指すため`exclude_path`で除外する必要があった
  (markdownlintの書式チェックとは独立に判断)

### 図の生成確認: `@mermaid-js/mermaid-cli`(npx経由)+ 自作の抽出スクリプト

- 公式CLI(`mmdc`)。GitHub Actionのマーケットプレイスにも代替ダイアグラムレンダリング
  アクションはあるが、mermaid公式が保守する`mermaid-cli`をnpx経由(インストール不要、
  毎回最新の安定版を取得)で使う方が実体に近い検証になる
- `docs/architecture.md`に限定せず、`docs/**/*.md`全体から ` ```mermaid ` フェンスを
  抽出して1つずつレンダリングする`scripts/check_mermaid_diagrams.py`を自作した。
  現時点で対象は`architecture.md`の2つのみだが、将来他のdocsにmermaidが追加された
  ときに検出漏れが起きないようにするため(Issue本文は`architecture.md`を例示している
  だけで、対象をそれに限定する指示ではないと解釈した)
- CIのコンテナ環境ではChromiumのsandboxが使えないため、`puppeteer`の設定ファイルで
  `--no-sandbox --disable-setuid-sandbox`を渡している
- このスクリプトは`docs/`配下のMarkdownをパースする実体のあるロジック(正規表現による
  フェンス抽出)を持つため、CLAUDE.mdのテスト方針に合わせて`tests/scripts/`に
  ユニットテストを追加した(`find_diagrams()`のみ。実際の`mmdc`呼び出し(`render()`)は
  Node.js/ネットワークに依存するため、既存の「外部依存に触れるテストはモック」方針に
  合わせてpytestの対象外とし、ワークフロー側で都度実行して検証する)

### 書式チェック: [markdownlint-cli2](https://github.com/DavidAnson/markdownlint-cli2)(npx経由)

- Markdownの書式チェックとして最も広く使われているmarkdownlintのCLIラッパー。
  設定ファイル(`.markdownlint-cli2.jsonc`)1つで完結し、追加のプラグイン管理が要らない
- 既定のフルルールセットをそのまま適用すると、既存docsに対して22件のエラーが出た。
  ADR-0011(ruffのルール選定)と同じ考え方で、実害のある指摘(見出し構造・trailing
  whitespace・フェンスの言語指定漏れ等)は残しつつ、内容に影響しない書式指摘のみ
  `.markdownlint-cli2.jsonc`で対象外にした:
  - **MD013(行長制限)**: 日本語の文章・表・URLを含む行が自然に80桁を超える。
    ruffの`E501`除外([ADR-0011](0011-ci-lint-format-typecheck.md))と同じ理由
  - **MD060(テーブルの`|`桁揃え)**: 可読性の好みであり内容には影響しない
  - **MD024(見出しの重複禁止)は無効化はせず`siblings_only: true`に緩和**: ADRの
    「追記」節が「決定」「却下した選択肢」等の見出しを再利用する構成(ミニADRを
    追記として積む形)を採っているため、無効化ではなく「同じ親を共有する兄弟見出し
    同士の重複だけを検出する」設定に変更し、意味のある重複検出は残した
- 残りの22件は、内容自体の書式バグ(意図しない箇条書き化・見出しレベルの飛び・
  コードフェンスの言語指定漏れ・コードスパン内の空白)だったため、CI追加のPRの一部として
  ドキュメント側を修正した(詳細は「影響」節)

## 却下した選択肢

- **既存`ci.yml`に1ジョブとして追加する**: ツールチェーンの混在(Python + Node.js)で
  セットアップ手順が読みにくくなること、コードのみの変更でもNode.jsセットアップの
  コストを払うことになる点を避けたく、別ワークフローファイルを選んだ
- **`markdown-link-check`(npm)でリンクチェックする**: 広く使われているが、アンカー
  チェックの精度・並列実行速度でlycheeに劣り、npm依存ツリーも増える。lycheeは単一
  バイナリで完結するため不採用
- **リンクチェックの対象を内部リンクのみにする**: Issue本文は外部URLを対象にするか
  どうかを判断に委ねていた。このリポジトリのdocsは`github.com`のIssueリンクが
  大半を占め、リンク切れ検出の実用上の価値が高いと判断し、タイムアウト・リトライで
  安定性を確保した上で対象に含めた
- **markdownlintの既定ルールセットをそのまま使う**: 「決定」節の通り、既存docsへの
  影響(88桁制限・テーブル桁揃え)が大きく、内容に実害のない指摘だったため一部を
  対象外にした
- **図の生成確認を`docs/architecture.md`のみに決め打ちする**: Issue本文の例示に
  従えば最小実装で済むが、将来のdocsにmermaidが追加されたときに検出漏れが起きる
  ため、`docs/**/*.md`全体を走査する設計にした

## 影響

- `.github/workflows/docs-ci.yml`を新設。`docs/**`・`CLAUDE.md`・関連設定ファイルの
  変更時のみpush/pull_requestで実行される
- `lychee.toml`(リンクチェック設定)、`.markdownlint-cli2.jsonc`(書式チェック設定)、
  `scripts/check_mermaid_diagrams.py`(図の生成確認スクリプト)を新設
- `pyproject.toml`の`[tool.pytest.ini_options]`に`scripts`を`pythonpath`へ追加
  (`tests/scripts/`から`scripts/check_mermaid_diagrams.py`をimportするため)
- `tests/scripts/test_check_mermaid_diagrams.py`を新設(`find_diagrams()`のユニットテスト)
- 本CI追加にあたり、既存docsに存在した以下の実体のあるバグを合わせて修正した(挙動を
  変えない範囲でのCI整備という方針に合わせ、修正は最小限に留めた):
  - `docs/operations/configuration.md`: 表内の`list[str](1件以上、空文字列不可)`が
    意図せずMarkdownリンク構文として解釈されていた(バッククォート追加で解消)
  - `docs/specs/gitlab-adapter.md`: `docs/adr/0002-gitlab-adapter-interface.md`への
    アンカーリンクが実際の見出しスラグ(`#追記m1-331`)と一致していなかった
    (`#追記m1-3-31`と誤記されていた)。同ファイル内の`+`始まりの継続行が
    意図せずMarkdownの箇条書きとして解釈されていた問題も合わせて修正
  - `docs/guide/faq.md`: 各質問見出しが`#`(h1)の直下で`###`(h3)を使っており、
    `##`(h2)を飛ばしていた。全て`##`に統一
  - `docs/specs/review-output.md` / `docs/specs/workspace-manager.md`: ディレクトリ
    構成を示すコードフェンスに言語指定(`text`)が無かった
  - `docs/operations/troubleshooting.md`: コードスパン内の不要な前後空白、
    `json`コードフェンスのliteral表記がバッククォートのネストにより
    正しくレンダリングされていなかった問題を修正
  - その他、`docs/adr/0001-repository-structure.md`等7ファイルのコードフェンスに
    言語指定(`text`)を追加、`docs/adr/0005-claude-code-runner-design.md`の
    箇条書き前に空行を追加
