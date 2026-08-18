# ADR-0027: 要求分析フェーズのRunner実行方式(`run_prompt`の追加とworktreeを使わない設計)

- Issue: [#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109) (M4-3)
- 状態: 決定

## 背景・制約

- `runner/protocol.py`の`ClaudeCodeRunner.run`は、`instructions: str`と`context: ReviewContext`
  を受け取り、Runner内部(`SubprocessClaudeCodeRunner.run`)で`build_prompt(instructions,
  context)`(MR固有のタイトル・説明・コメント・diffを結合する関数)を呼んでプロンプト文字列を
  組み立てる設計になっていた(M1-7、ADR-0005)。`context`の型は`ReviewContext`固定で、
  実装(`_write_log`等)も`context.merge_request`に直接依存している
- M4-2([#108](https://github.com/AtsushiNi/gitlab-ai-platform/issues/108))で追加した
  `runner/issue_prompt.py`の`build_issue_prompt(instructions, context: IssueContext) -> str`は、
  `instructions`とIssue情報を結合した**完成後のプロンプト文字列**を返す関数として実装されて
  いた。しかし`ClaudeCodeRunner.run`は`context: ReviewContext`しか受け付けず、
  `IssueContext`を渡すとRunner内部で再度`build_prompt`(MR専用、`context.merge_request`を
  参照する)を呼ぼうとして`AttributeError`になる。M4-2の仕様(`docs/specs/claude-code-runner.md`)
  も「Issueを実際にClaude Code Runnerへ渡してヘッドレス実行するJobハンドラの実装(M4-3)は
  スコープ外」と明記しており、この接続部分は本Issueで設計する必要があった
- 要求分析フェーズ(`issue-analysis`)は、Issue本文(タイトル・説明・ラベル)の読解だけを
  対象とし、`review`と異なりMRのdiffやリポジトリの実装を参照しない
  (`runner/issue_prompt.py`のdocstring、M4-2の設計)。`Workspace Manager.prepare`は
  `(project, mr_iid, ref)`というMR前提のシグネチャで、GitLab Adapter(`gitlab_adapter/protocol.py`)
  にはIssueに対応する「参照すべきgit ref」を得る手段(default branchの取得等)が無い。
  そのため要求分析フェーズにMR同様のworktree準備を要求すると、Adapterの拡張という
  本Issueのスコープを超える変更が必要になる

## 決定

### `ClaudeCodeRunner`に`run_prompt`を追加し、`run`とプロンプト組み立ての責務を分離する

`run`(既存、シグネチャ・挙動とも変更しない)とは別に、呼び出し側が組み立て済みのプロンプト
文字列をそのまま実行する`run_prompt`を追加する。

```python
def run_prompt(
    self,
    worktree_path: Path,
    prompt: str,
    *,
    log_key: str,
    timeout_seconds: int,
    allowed_tools: Sequence[str] = (),
    disallowed_tools: Sequence[str] = (),
    permission_mode: str | None = None,
) -> RunResult:
    """組み立て済みの`prompt`をそのままClaude Codeへ渡して非対話実行し、結果を返す。"""
    ...
```

`run`との違いは2点だけ:

1. `instructions`+`context`ではなく、組み立て済みの`prompt: str`を直接受け取る
   (Runnerはプロンプトの組み立てに一切関与しない)
2. ログ保存先ディレクトリを`context.merge_request`から導出するのではなく、呼び出し側が
   `log_key`(`log_dir`からの相対パス文字列)として直接指定する。Runnerはこの文字列の
   中身を解釈しない(`review`固有・`issue-analysis`固有のどちらの知識も持たない、
   ADR-0005の「Runnerは中身を解釈しない」という既存方針をログパスの決定にも適用する)

`SubprocessClaudeCodeRunner`の実装は、Popen起動・タイムアウト処理(SIGTERM→SIGKILL)・
ログ保存・JSON結果パースという実行本体を`_execute`という内部メソッドに切り出し、
`run`/`run_prompt`の両方から共有する。`run`は`build_prompt(instructions, context)`で
プロンプトを組み立ててから、MRの`project`/`iid`/`sha`から`log_dir`/ログファイル名prefixを
導出して`_execute`を呼ぶ。`run_prompt`はプロンプトをそのまま、`log_key`から`log_dir`を
導出して(ファイル名prefixは無し)`_execute`を呼ぶ。既存の`run`の外部から見える挙動
(ログパス`<projectスラッグ>/mr-<iid>/<sha先頭12桁>-<timestamp>.json`、エラー時の例外型)は
一切変更していない。

`issue-analysis`のJobHandler(`cli/dispatcher.py`の`build_issue_analysis_handler`)は、
`build_issue_prompt(build_issue_analysis_instructions(), context)`で完成後のプロンプトを
組み立て、`runner.run_prompt(..., log_key=f"{slugify_project(project)}/issue-{issue_iid}",
...)`を呼ぶ。ログパスは`<log_dir>/<projectスラッグ>/issue-<issue_iid>/<timestamp>.json`になる
(`run`のMR向けパスと対称的な構成)。

### `issue-analysis`はWorkspace Manager(worktree)を使わない

`build_issue_analysis_handler`は`Workspace Manager`を引数に取らない。Claude Codeの実行先
(`run_prompt`の`worktree_path`引数、subprocessの`cwd`)には、Job処理の間だけ存在する
一時ディレクトリ(`tempfile.TemporaryDirectory`)を使い、処理完了後に破棄する。

`ClaudeCodeRunner`のインターフェース上、`worktree_path`という引数名は変更しない
(`run_prompt`の`worktree_path`が実際には空の一時ディレクトリであることは、
`issue-analysis`という利用者側の事情であり、Runner自身は「引数で渡されたディレクトリを
cwdにしてsubprocessを起動する」という契約以上のことを知らない。将来Issueに紐づくリポジトリの
探索が必要になった場合(`design`/`implement`フェーズ等)に備え、汎用的な引数名のまま残す)。

## 却下した選択肢

- **`Workspace Manager.prepare`を拡張し、Issueにも使えるようにする**: `prepare(project,
  mr_iid, ref)`はMR番号とgit refの組を前提にしたシグネチャで、Issueには対応するrefが
  存在しない(GitLab Adapterにdefault branch取得等の新規メソッドが必要になる)。要求分析
  フェーズはリポジトリ探索を行わない設計(M4-2)のため、worktree自体が本質的に不要であり、
  Adapter拡張という本Issueのスコープを超える変更をしてまで用意する理由が無い
- **`ClaudeCodeRunner.run`の`context`引数の型を`ReviewContext | IssueContext`に拡張し、
  `run`1つで両方に対応する**: `SubprocessClaudeCodeRunner.run`の内部実装(ログパスの導出、
  `build_prompt`呼び出し)がMR固有の情報(`merge_request.project`/`iid`/`sha`)に強く
  依存しており、`isinstance`分岐だらけの実装になる。`run`(既存、MRレビュー専用)と
  `run_prompt`(汎用、プロンプト組み立て済み)を分離した方が、既存の`run`の挙動・テストを
  一切変更せずに済み、Protocol拡張のパターン(M3-2で`JobRepository`に`claim`等を追加した
  時と同じ「既存メソッドは変更せず新メソッドを追加する」)とも一貫する
- **`review`と同様の“instructions+context結合をRunner内部で行う”方式を`IssueContext`にも
  適用する(`run`のオーバーロード)**: Pythonの`typing.Protocol`は引数の型によるオーバーロードを
  実質サポートしない(同名メソッドの型分岐は`@overload`が必要になり、実装側の分岐ロジックが
  複雑化する)。呼び出し側が既に`build_issue_prompt`で完成後のプロンプトを組み立て済みという
  M4-2の実装を活かし、「組み立て済みプロンプトをそのまま実行する」という薄い契約
  (`run_prompt`)にする方がシンプル

## 影響

- `runner/protocol.py`(Protocol定義)・`runner/subprocess_runner.py`(実装)に`run_prompt`を
  追加。`runner/test_protocol.py`(メソッド集合の完全一致テスト)・
  `runner/test_subprocess_runner.py`(`run_prompt`のコマンド組み立て・ログパス・エラー系の
  テスト)を更新
- `cli/dispatcher.py`の`build_issue_analysis_handler`が`run_prompt`を呼び出す
- M4-6(設計フェーズ、Job種別`design`)・M4-8(実装フェーズ、Job種別`implement`)は
  リポジトリの実装を参照する必要があるため、`run_prompt`ではなく`run`(worktree前提)、
  または将来`run_prompt`にworktree_pathとして実際のworktreeを渡す形を再検討する余地がある
  (本ADRは`issue-analysis`がworktreeを使わないことだけを決定し、`design`/`implement`の
  実行方式は各Issueのスコープで別途判断する)
