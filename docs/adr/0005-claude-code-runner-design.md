# ADR-0005: Claude Code Runner の設計

- Issue: [#35](https://github.com/AtsushiNi/gitlab-ai-platform/issues/35) (M1-7)
- 状態: 決定

## 背景・制約

- `docs/architecture.md` の設計方針により、Claude Code Runnerは「worktree上でClaude Codeを
  ヘッドレス実行し、MRタイトル・説明・コメント・diffをコンテキストとして渡す。タイムアウト・
  異常終了のハンドリング、実行ログ保存を行う」ことが責務。「レビュー観点の判断そのもの
  (何を重大とするか)はプロンプト側の責務であり、Runnerは実行制御のみ」が境界。
- `references/spike-s1-claude-code-headless.md`(S-1)で以下を実機検証済み:
  - `claude -p "<prompt>" --output-format json` でTTY不要のheadless実行と構造化出力を得られる。
  - **成否判定は`result`(自然文)ではなく`is_error`と`permission_denials`を見るべき**
    (権限がなく実際には書き込みに失敗していても、成功したかのような`result`文が返る事例を実測)。
  - CLI自体に`--timeout`は無く、外部から`subprocess`のタイムアウトで包む必要がある。
  - タイムアウト時にSIGTERMを送った場合、Claude Codeは最終結果JSON
    (`terminal_reason: aborted_*`, `is_error: true`)を出力してから終了する。
  - Bedrock認証のクレデンシャルチェーン解決が最大60秒(またはそれ以上)ブロックしうる。

## 決定

### インターフェースは`typing.Protocol`を使う(ADR-0002〜0004と同じ方針)

`ClaudeCodeRunner`(`src/gitlab_ai_platform/runner/protocol.py`)は`run`の1メソッドのみを持つ。
呼び出し側(MR Poller/Review/CLI)はこのProtocol型だけを見て実装し、subprocess実装
(`SubprocessClaudeCodeRunner`)に直接依存しない。将来Linux/Docker側(M3以降)で実装を
差し替える際も、呼び出し側の変更は不要になる想定。

### `run`はコンテキスト(`ReviewContext`)と観点(`instructions`)を分けて受け取る

MRタイトル・説明・コメント・diffは`ReviewContext`(dataclass。GitLab Adapterの
`MergeRequest`/`MergeRequestDiff`/`Discussion`をそのまま再利用し、Runner独自の型を
作り直さない)としてRunnerに渡す。一方、「何を重視してレビューするか」という観点は
`instructions`という不透明な文字列としてRunnerに渡す。Runnerはこの2つを結合してプロンプトを
組み立てる(`build_prompt`)だけで、`instructions`の中身を解釈・分岐しない。これにより
「レビュー観点の判断はプロンプト側(Review, M1-8/9)の責務、Runnerは実行制御のみ」という
`docs/architecture.md`の境界を型で表現する。M1-9(Review)がまだ実装されていない時点でも、
Runner単体でこの境界を先に固定できる。

### タイムアウトは`subprocess.run(timeout=)`ではなく`Popen`直接操作でSIGTERM→SIGKILLの2段階にする

Pythonの`subprocess.run(timeout=N)`は、タイムアウト発生時に内部で`Popen.kill()`
(SIGKILL)を呼ぶ。SIGKILLは即座にプロセスを終了させるため、S-1で確認した
「SIGTERM後もClaude Codeは最終結果JSONを出力してから終了する」という挙動を活かせず、
毎回ログもエラーも残らない「原因不明の強制終了」になってしまう。

そのため`SubprocessClaudeCodeRunner`は`subprocess.Popen`を直接操作し、
1. `communicate(timeout=timeout_seconds)`で通常のタイムアウトを待つ
2. タイムアウトしたら`terminate()`(SIGTERM)を送り、`terminate_grace_seconds`
   (デフォルト10秒)だけ`communicate()`で正常終了を待つ
3. それでも終了しなければ`kill()`(SIGKILL)で強制終了する

という2段階(GNU `timeout`コマンドの`--kill-after`相当)の実装にした。ステップ2で
Claude Codeが自発的に終了しJSONを出力できた場合は、`RunResult.timed_out=True`の
通常の戻り値として扱う(例外にしない)。ステップ3まで進んだ場合(=ハング)のみ
`ClaudeCodeTimeoutError`を送出する。

### 成否判定は`is_error`をそのまま`RunResult`に載せ、呼び出し側の判断に委ねる

S-1で確認した通り`result`(自然文)だけで成否を判定するのは危険なため、Runnerは
`result`をパースしたり成否判定に使ったりしない。`is_error`・`permission_denials`・
`terminal_reason`をそのまま構造化された`RunResult`のフィールドとして返し、
「これが失敗を表すかどうか」の最終判断は呼び出し側(Review, M1-9)に委ねる
(Runnerが「レビュー結果として成功/失敗」を判断すると、境界(実行制御のみ)を越えるため)。
`is_error`フィールド自体が欠けている(想定外のレスポンス形式)場合は、誤って成功と
判定しないようデフォルトでエラー扱いにする。

### `--dangerously-skip-permissions`は提供しない

`allowed_tools` / `disallowed_tools` / `permission_mode`は`run`の引数として公開し、
呼び出し側が用途に応じて権限を調整できるようにした(MVPのレビュー実行は読み取りのみで
足りる想定だが、将来のM4(Issue駆動実装)では書き込み権限が必要になる)。一方、
全許可フラグ`--dangerously-skip-permissions`はRunnerのインターフェース上どこにも
公開しない。GitLab Adapterが禁止操作をメソッドとして存在させないことで機構的に禁止する
方針(ADR-0002)と同じ考え方で、危険な操作への近道をコード上用意しないことを徹底した。

### 実行ログは`log_dir`配下にコマンド・stdout・stderr・所要時間をJSONで保存する

`docs/architecture.md`の「実行ログ保存」を満たすため、`run`の呼び出しごとに
`<log_dir>/<projectスラッグ>/mr-<iid>/<sha先頭12桁>-<timestamp>.json`へ保存する。
`projectスラッグ`はWorkspace Manager(ADR-0004)と同じパーセントエンコーディング方式
(`urllib.parse.quote`)を用い、ディレクトリ構成の付け方を揃えた。認証情報(Bedrock/AWS等)は
コンストラクタの`env`引数経由で`Popen`にのみ渡し、コマンド引数やログには一切含まれない
設計とした(コマンドは`["claude", "-p", <prompt>, "--output-format", "json", ...]`のみで、
`env`の中身はログ化対象に含めない)。

## 却下した選択肢

- **`instructions`と`context`を1つの完成プロンプト文字列にまとめて受け取る**: 呼び出し側が
  MRの生データ(title/description/diff等)をテキスト整形する処理まで持つことになり、
  「コンテキストとして渡す」というRunnerの責務(`docs/architecture.md`)が呼び出し側に
  漏れ出す。構造化データ(`ReviewContext`)のまま受け取り、整形はRunner内で完結させた。
- **`subprocess.run(timeout=)`をそのまま使う**: 上記の通りSIGKILLのみになり、S-1で確認した
  グレースフルな終了(最終JSON取得)を活かせないため不採用。
- **`--dangerously-skip-permissions`を`run`の引数として公開する**: MVPでは不要な上、
  誤用時の被害が大きい。将来必要になった場合も、呼び出し側がRunnerを介さず直接CLIを
  叩く判断をすべきレベルの操作と位置づけ、意図的にRunnerのインターフェースからは外した。
- **タイムアウト検知に`stream-json`出力のアイドル時間を使う**: S-1では`json`(非ストリーム)
  出力のみを検証しており、`stream-json`のアイドル検知は実測データが無い。M1-7のMVPスコープ
  (単発のレビュー実行)では外部`timeout`で十分なため見送った。長時間実行の進捗監視が
  必要になった時点(M2以降)で再検討する。
- **`result_text`をパースしてRunner側で成否判定する**: S-1が明確に禁止事項として示した
  アンチパターン。Runnerは判断せず、構造化フィールドをそのまま呼び出し側に渡す設計にした。

## 影響

- MR Poller(M1-5)・Review(M1-8/9)は`ClaudeCodeRunner`(Protocol型)にのみ依存し、
  Workspace Manager(M1-6)の`WorktreeHandle.path`をそのまま`run`の`worktree_path`に渡す形で
  実装する。
- Review(M1-9)は`instructions`(レビュー観点のプロンプト)を組み立てて`run`に渡し、
  戻り値の`RunResult.is_error`/`permission_denials`/`terminal_reason`を見て
  レビュー結果として成功/失敗を判断する。
- Bedrock認証のクレデンシャル解決の詰まり(S-1 §5.3、最大60秒)を踏まえ、呼び出し側は
  `timeout_seconds`を60秒より十分長く設定する必要がある(でないと「認証が詰まっただけ」を
  「レビューがタイムアウトした」と誤判定する)。この値の具体的な決定はCLI(M1-10/11)側の
  組み立てで行う。
