# Claude Code Runner

- 実装場所: `src/gitlab_ai_platform/runner/`
- 対応Issue: [#35](https://github.com/AtsushiNi/gitlab-ai-platform/issues/35) (M1-7)
- 関連ADR: [ADR-0005](../adr/0005-claude-code-runner-design.md)
- ステータス: 実装済み(Protocol定義 + subprocess実装)

## 責務

worktree上でClaude Codeをヘッドレス実行し、MRタイトル・説明・コメント・diffをコンテキストとして
渡す。タイムアウト・異常終了のハンドリング、実行ログ保存を行う。実装(subprocess実装。将来
Linux/Docker上での差し替え, M3-4)を`typing.Protocol`で抽象化し、呼び出し側(MR Poller/Review/CLI)
を具象実装から切り離す。

## 前提と非対象

- 前提:
  - 呼び出し側は`ClaudeCodeRunner`のProtocol型だけを見て実装し、具象クラス
    (`SubprocessClaudeCodeRunner`)に直接依存しない
  - `run`に渡す`worktree_path`は、Workspace Manager(M1-6)の`prepare`が返す
    `WorktreeHandle.path`をそのまま使うことを想定する
  - `claude` CLIがPATH上で実行可能であること、Bedrock認証(`CLAUDE_CODE_USE_BEDROCK`/`AWS_*`)
    が呼び出し側の`env`引数経由で正しく渡されることが前提(`references/spike-s1-claude-code-headless.md`
    §5)
- 非対象:
  - レビュー観点の判断そのもの(何を重大とするか)はしない。`run`の`instructions`引数は
    不透明な文字列として扱い、中身を解釈・分岐しない(`docs/architecture.md`の境界)
  - `RunResult`が「レビューとして成功/失敗か」を判断しない。`is_error`/`permission_denials`/
    `terminal_reason`を構造化フィールドとして返すのみで、最終判断は呼び出し側(Review, M1-9)
    に委ねる([ADR-0005](../adr/0005-claude-code-runner-design.md))
  - `--dangerously-skip-permissions`相当の全許可フラグは提供しない

## 公開インターフェース

`ClaudeCodeRunner`を`@runtime_checkable`な`typing.Protocol`として定義する。
実装場所: `src/gitlab_ai_platform/runner/protocol.py`。

```python
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from .types import ReviewContext, RunResult


@runtime_checkable
class ClaudeCodeRunner(Protocol):
    def run(
        self,
        worktree_path: Path,
        instructions: str,
        context: ReviewContext,
        *,
        timeout_seconds: int,
        allowed_tools: Sequence[str] = (),
        disallowed_tools: Sequence[str] = (),
        permission_mode: str | None = None,
    ) -> RunResult:
        """`worktree_path`配下でClaude Codeを非対話実行し、結果を返す。"""
        ...
```

subprocess実装: `src/gitlab_ai_platform/runner/subprocess_runner.py`の`SubprocessClaudeCodeRunner`。

```python
SubprocessClaudeCodeRunner(
    log_dir: Path | str,
    *,
    claude_command: str = "claude",
    env: Mapping[str, str] | None = None,
    terminate_grace_seconds: float = 10.0,
    popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
)
```

- `log_dir`: 実行ログ(コマンド・stdout・stderr・所要時間)の保存先ルート
- `claude_command`: 実行するCLIコマンド名(通常は`"claude"`。テストでの差し替え用)
- `env`: `os.environ`とマージして`Popen`に渡す追加環境変数(Bedrock認証・モデルバージョン
  固定用。`ANTHROPIC_DEFAULT_SONNET_MODEL`等)
- `terminate_grace_seconds`: タイムアウト時にSIGTERMを送ってからSIGKILLするまでの猶予秒数

`build_prompt(instructions: str, context: ReviewContext) -> str`は`run`が内部で使う
プロンプト結合ロジックを公開したもの。呼び出し側(CLI, M1-10)が`review.save_review`の
`input_prompt`(Runnerに実際に渡した完成後のプロンプト全文)を再現する際に使う
(`docs/specs/review-output.md`)。`runner`パッケージから`from gitlab_ai_platform.runner
import build_prompt`で参照できる。

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/runner/types.py`。

| 型 | フィールド | 補足 |
|---|---|---|
| `ReviewContext` (frozen dataclass) | `merge_request: MergeRequest`, `diffs: tuple[MergeRequestDiff, ...]`, `discussions: tuple[Discussion, ...]` | 型はすべてGitLab Adapter(`gitlab_adapter/types.py`)のものを再利用する |
| `RunResult` (frozen dataclass) | `is_error: bool`, `result_text: str`, `session_id: str`, `terminal_reason: str`, `permission_denials: tuple[Mapping[str, Any], ...]`, `num_turns: int`, `total_cost_usd: float`, `timed_out: bool`, `duration_seconds: float`, `log_path: Path`, `raw: Mapping[str, Any]` | `raw`はClaude CLIが返したJSON全体。上記フィールドで表現しきれない値が必要な場合はここから参照する |

`instructions`(レビュー観点のプロンプト本文)と`context`は`build_prompt`で結合され、
以下の形式のテキストとして`claude -p`の引数に渡される:

```text
<instructions>

## Merge Request
Title: <mr.title>

Description:
<mr.description>

## Comments
- <author>: <note.body>
  (discussion内のsystemノートは除外)

## Diff
--- <old_path> -> <new_path> ---
<diff>
```

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/runner/errors.py`。

- `RunnerError(Exception)` — Claude Code Runner経由の実行が失敗したことを表す基底例外。
  呼び出し側はまずこの型でcatchすればRunner起因の失敗を一括して扱える
- `ClaudeCodeNotFoundError(RunnerError)` — `claude`コマンドが見つからない(未インストール・
  PATH未設定)ことを表す
- `ClaudeCodeTimeoutError(RunnerError)` — `timeout_seconds`経過後、SIGTERMを送っても
  `terminate_grace_seconds`以内に終了しない(ハングした)場合、またはSIGKILL後も有効な
  結果が得られなかった場合に送出する。`timeout_seconds` / `log_path` / `stderr`を保持する。
  **SIGTERM後に自発的に終了しJSONを取得できた場合はこの例外にはならず、通常の`RunResult`
  (`timed_out=True`)を返す**([ADR-0005](../adr/0005-claude-code-runner-design.md)参照)
- `ClaudeCodeOutputError(RunnerError)` — Claude Codeの標準出力が空、またはJSONとして
  解釈できなかったことを表す。`returncode` / `log_path` / `stdout` / `stderr`を保持する
- いずれの例外も`log_path`を保持しており、失敗時も実行ログから詳細を追跡できる

## 実行ログ(`log_dir`への保存)

実装場所: `src/gitlab_ai_platform/runner/subprocess_runner.py`の`SubprocessClaudeCodeRunner._write_log`。

- 保存先: `<log_dir>/<projectスラッグ>/mr-<iid>/<sha先頭12桁>-<timestamp>.json`
  (`projectスラッグ`はWorkspace Manager, ADR-0004と同じパーセントエンコーディング方式)
- 内容: `command`(実行コマンド。プロンプト文字列も含むがシークレットは含まない) /
  `cwd` / `started_at` / `duration_seconds` / `timed_out` / `returncode` / `stdout` / `stderr`
- 認証情報(Bedrock/AWS等)は`env`経由で`Popen`にのみ渡され、`command`にもログにも含まれない
- 正常終了・タイムアウト・出力エラーのいずれの場合も、例外送出前に必ずログを保存してから
  例外を送出する(失敗時の調査可能性を優先する)

## テスト方針

実装場所: `tests/gitlab_ai_platform/runner/`(`src/`をミラー、[ADR-0001](../adr/0001-repository-structure.md))。
実際にClaude Codeのサブプロセスを起動するテストは行わず、`subprocess.Popen`をダミー実装
(`_FakePopen`)で差し替える(CLAUDE.mdのテスト方針)。

- `test_types.py`: `ReviewContext`/`RunResult`のイミュータブル性(`frozen=True`)を検証する
- `test_errors.py`: 各例外が`RunnerError`のサブクラスであること、保持する属性
  (`log_path`等)を検証する
- `test_protocol.py`: `ClaudeCodeRunner`の公開メソッド集合が`run`と完全一致することを検証する。
  Protocolを満たすダミー実装に対して`isinstance(impl, ClaudeCodeRunner)`が`True`になることも
  検証する
- `test_subprocess_runner.py`:
  - 正常系: コマンド組み立て(`claude -p <prompt> --output-format json`)、`cwd`が
    `worktree_path`になること、JSON出力が`RunResult`へ正しくマッピングされることを検証する
  - `instructions`とMRタイトル・説明・diff・コメント(systemノートを除く)がプロンプトに
    含まれることを検証する
  - `allowed_tools` / `disallowed_tools` / `permission_mode`が対応するCLIフラグに変換される
    こと、`--dangerously-skip-permissions`がどのコマンドにも現れないことを検証する
  - `claude`コマンドが見つからない場合に`ClaudeCodeNotFoundError`を送出することを検証する
  - タイムアウト後にSIGTERMで正常終了できた場合、例外にならず`RunResult.timed_out=True`を
    返すことを検証する(ADR-0005の中核的な設計判断の回帰テスト)
  - SIGTERMの猶予期間内に終了しない場合、SIGKILLされ`ClaudeCodeTimeoutError`を送出する
    ことを検証する
  - 標準出力がJSONとして解釈できない場合に`ClaudeCodeOutputError`を送出することを検証する
  - 実行ログが期待するパスに保存され、認証情報等のシークレットが含まれないことを検証する

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表のClaude Code Runner行
- [ADR-0005: Claude Code Runner の設計](../adr/0005-claude-code-runner-design.md)
- `references/spike-s1-claude-code-headless.md` — ヘッドレス実行方式・タイムアウト・
  権限設定・Bedrock認証に関する実機検証結果
- ソースコード: `src/gitlab_ai_platform/runner/`
  (`protocol.py` / `types.py` / `errors.py` / `subprocess_runner.py` / `__init__.py`)
