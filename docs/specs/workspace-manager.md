# Workspace Manager

- 実装場所: `src/gitlab_ai_platform/workspace/`
- 対応Issue: [#34](https://github.com/AtsushiNi/gitlab-ai-platform/issues/34) (M1-6)、
  [#80](https://github.com/AtsushiNi/gitlab-ai-platform/issues/80) (M2-1、並列アクセスの排他制御)
- 関連ADR: [ADR-0004](../adr/0004-workspace-manager-design.md)、
  [ADR-0014](../adr/0014-parallel-review-execution.md)
- ステータス: 実装済み(Protocol定義 + git実装)

## 責務

プロジェクトごとのbare clone、MR単位のworktree作成/更新/破棄、ディスク上限とGCを管理する。
実装(git実装。将来Linux/Docker上での差し替え, M3-4)を`typing.Protocol`で抽象化し、呼び出し側
(MR Poller/Claude Code Runner)を具象実装から切り離す。並列レビュー(M2-1)でworking treeを
共有しない設計にする。

## 前提と非対象

- 前提:
  - 呼び出し側は`WorkspaceManager`のProtocol型だけを見て実装し、具象クラス
    (`GitWorkspaceManager`)に直接依存しない
  - `prepare`に渡す`ref`(branch名またはcommit sha)は、State Store(M1-4)が`commit_sha`単位で
    追跡する対象と一致させる。MR Pollerは検出した`commit_sha`をそのまま渡すことを想定する
  - clone元のURL構築(GitLabのbase URL等)・認証設定(PAT/SSH)は呼び出し側が
    `GitWorkspaceManager`のコンストラクタ引数(`clone_url_for` / `git_config`)として注入する
  - M2-1(#80)以降、`GitWorkspaceManager`の同一インスタンスは複数のワーカースレッドから
    同時に呼ばれる(`docs/specs/cli.md`の`watch`サブコマンド、`ReviewWorkerPool`)。
    project(bare repo)単位のロックで内部のgit操作を直列化し、同一project内の複数MRからの
    同時呼び出しでも破損しないことを保証する(下記「並行アクセスの安全性」参照)
- 非対象:
  - git操作以外(ビルド・テスト実行など)はしない(`docs/architecture.md`の境界)
  - GitLabの認証方式そのものの決定(PAT vs SSH、credential helperの実装)はこのモジュールの
    責務外([ADR-0004](../adr/0004-workspace-manager-design.md)参照)。GitLab Adapter
    (M1-1〜M1-3)/configの責務
  - 複数**プロセス**からの同時実行時の排他制御(ファイルロック等)は対象外。`GitWorkspaceManager`が
    保証するのは同一プロセス内(=同一`watch`プロセスの複数ワーカースレッド)からの並行呼び出しの
    安全性のみ。複数プロセスの同時起動自体は`ProcessLock`(`cli/lock.py`、
    [ADR-0009](../adr/0009-cli-watch-design.md))が別途防ぐ

## 並行アクセスの安全性(M2-1、ADR-0014)

`GitWorkspaceManager`はproject名をキーにした`threading.RLock`の辞書(`_project_locks`)を持ち、
`prepare`/`discard`は該当projectのロックを取得してから内部のgit操作を行う。

- 同一projectへの`clone`/`fetch`/`worktree prune`/`worktree add`/`reset --hard`は、
  複数MR(=複数ワーカースレッド)から同時に呼ばれても常に直列化される
- 異なるprojectは別ロックのため、真に並行実行できる(1つのprojectの処理を待つ必要はない)
- ロックが保護するのは`prepare`/`discard`本体(git操作)のみで、その後のClaude Code Runner
  実行(呼び出し側が`WorktreeHandle`を受け取った後に行う、本来時間のかかる処理)はロックの
  外で行われる。並列化の効果はここで確保する
- `collect_garbage`(GC)は退避対象のprojectロックを`acquire(blocking=False)`で試み、
  取得できない(他スレッドが操作中の)候補はスキップして次に古いものを試す。ブロッキング
  待ちにしないのは、GC実行中のスレッドと`prepare`実行中の別スレッドが互いのロックを
  待ち合う循環待ち(デッドロック)を構造的に起こさないため(詳細は[ADR-0014](../adr/0014-parallel-review-execution.md)参照)

## 公開インターフェース

`WorkspaceManager`を`@runtime_checkable`な`typing.Protocol`として定義する。
実装場所: `src/gitlab_ai_platform/workspace/protocol.py`。

```python
from typing import Protocol, runtime_checkable

from .types import WorktreeHandle


@runtime_checkable
class WorkspaceManager(Protocol):
    def prepare(self, project: str, mr_iid: int, ref: str) -> WorktreeHandle:
        """指定MRのworktreeを用意する(bare cloneの作成/fetch、worktreeの新規作成/更新)。"""
        ...

    def discard(self, project: str, mr_iid: int) -> None:
        """指定MRのworktreeを破棄する。対象が存在しなくても例外を送出しない(冪等)。"""
        ...

    def collect_garbage(self) -> list[WorktreeHandle]:
        """ディスク上限を超えている場合、最終利用時刻が古いworktreeから破棄する。"""
        ...
```

git実装: `src/gitlab_ai_platform/workspace/git_workspace.py`の`GitWorkspaceManager`。

```python
GitWorkspaceManager(
    root: Path | str,
    clone_url_for: Callable[[str], str],
    *,
    max_disk_bytes: int,
    git_config: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
)
```

- `root`: `<root>/repos/`(bare clone群)と`<root>/worktrees/`(worktree群)を作るベースディレクトリ
- `clone_url_for`: project名(`group/project`形式)からbare clone用URLを組み立てる関数
- `max_disk_bytes`: `<root>/worktrees/`配下の合計サイズの上限(バイト)。必須(デフォルト無し)
- `git_config`: 全gitコマンド呼び出しに`-c key=value`として注入する追加設定
  (`credential.helper` / `core.sshCommand`等の認証設定を想定)。`Mapping`(キー毎に1個)の他、
  同じキーを複数回渡したい場合(例: 既存の`credential.helper`を空値でクリアしてから
  新しい値を設定する、M1-10)のために`(key, value)`のタプル列も受け付ける

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/workspace/types.py`。

| 型 | フィールド | 補足 |
|---|---|---|
| `WorktreeHandle` (frozen dataclass) | `project: str`, `mr_iid: int`, `path: Path`, `branch: str`, `sha: str` | `path`はチェックアウト済みの作業ディレクトリ(Claude Code Runnerがこの配下でheadless実行する)。`sha`は`path`で実際にcheckout済みのcommit |

ディレクトリ構成(`root`配下):

```text
repos/<slug>.git             # プロジェクト単位のbare clone
worktrees/<slug>/mr-<iid>/   # MR単位のworktree
```

`slug`はproject名をパーセントエンコーディング(`urllib.parse.quote`)したもの
(単純な`/`→`__`置換は単射でなく別プロジェクトと衝突しうるため不採用。詳細はADR-0004)。
worktreeのローカルbranch名は`mr-<iid>`
(ソースbranch名はディレクトリ名・branch名に含めない。[ADR-0004](../adr/0004-workspace-manager-design.md)参照)。

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/workspace/errors.py`。

- `WorkspaceError(Exception)` — Workspace Manager経由の操作が失敗したことを表す基底例外。
  呼び出し側はまずこの型でcatchすればWorkspace Manager起因の失敗を一括して扱える
- `GitCommandError(WorkspaceError)` — gitコマンドが非ゼロの終了コードで終了したことを表す。
  `command` / `returncode` / `stderr`を保持する。ネットワーク不通・認証失敗・不正な`ref`指定等が
  該当する。呼び出し側は通常リトライせず、ログを見て原因を切り分ける
- `DiskLimitExceededError(WorkspaceError)` — `collect_garbage`で破棄可能なworktreeを
  全て破棄してもなお`max_disk_bytes`を超過していることを表す。呼び出し側は
  `max_disk_bytes`の見直しか、手動でのディスク確保が必要

## テスト方針

実装場所: `tests/gitlab_ai_platform/workspace/`(`src/`をミラー、
[ADR-0001](../adr/0001-repository-structure.md))。

- `test_types.py`: `WorktreeHandle`のイミュータブル性(`frozen=True`)を検証する
- `test_errors.py`: `GitCommandError`/`DiskLimitExceededError`が`WorkspaceError`の
  サブクラスであることを検証する
- `test_protocol.py`: `WorkspaceManager`の公開メソッド集合が`prepare`/`discard`/
  `collect_garbage`と完全一致することを検証する。Protocolを満たすダミー実装に対して
  `isinstance(impl, WorkspaceManager)`が`True`になることも検証する
- `test_git_workspace.py`: `GitWorkspaceManager`を実際のgitコマンドで検証する。実サービス
  (社内GitLab)には繋がず、`tmp_path`配下に作った通常のリポジトリ(non-bare)を「origin」代わりに
  使う(CLAUDE.mdのテスト方針)。以下を検証する:
  - `prepare`が新規bare clone + worktreeを作成し、指定`ref`(branch名)のHEADが
    checkoutされること
  - `prepare`がbranch名だけでなくcommit shaも受け付け、branchの最新でなく指定commitそのものを
    checkoutできること
  - 同一プロジェクトの2件目のMRに対する`prepare`が、bare cloneを再利用すること
  - 既存worktreeに対する`prepare`が、origin側の新しいcommitまで最新化すること
    ([ADR-0004](../adr/0004-workspace-manager-design.md)の`refs/remotes/origin/*`経由の
    fetch戦略・branch名解決の回帰テストを兼ねる)
  - 異なるMRのworktreeが別ディレクトリになり、互いのファイルを共有しないこと
  - `discard`がworktreeディレクトリを削除すること、および対象が存在しない場合も例外を
    送出しないこと(冪等性)
  - `discard`後に同じMRを`prepare`し直せること
  - ディスク上限超過時、`prepare`が最終利用時刻の古いworktreeを自動的にGCしてから
    新規worktreeを作成すること
  - `collect_garbage`が最終利用時刻の古い順にworktreeを破棄すること、上限内なら何もしないこと
  - GCしても上限を満たせない場合に`DiskLimitExceededError`を送出すること
  - (M2-1) 同一projectへの複数MRの`prepare`を`ThreadPoolExecutor`から並行に呼んでも、
    そのprojectのbare repoに対するgit操作はどの瞬間を見ても1つまでしか走らないこと
    (projectロックによる直列化)。異なるprojectへの`prepare`は実際に重なる(真に並行実行
    される)ことを、git呼び出しの同時実行数を計測して検証する
  - (M2-1) `collect_garbage`が、ロックを保持中(操作中)のprojectをブロッキング待ちせず
    スキップし、次点の候補を退避すること(デッドロック回避設計の回帰テスト)

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表のWorkspace Manager行
- [ADR-0004: Workspace Manager の設計](../adr/0004-workspace-manager-design.md)
- [ADR-0014: 並列レビュー実行の設計](../adr/0014-parallel-review-execution.md) —
  project単位ロックの設計判断・却下した選択肢
- `references/spike-S3-git-worktree-windows.md` — fetch戦略・パス長制限に関する検証結果
- ソースコード: `src/gitlab_ai_platform/workspace/`
  (`protocol.py` / `types.py` / `errors.py` / `git_workspace.py` / `__init__.py`)
