# GitLab Adapter

- 実装場所: `src/gitlab_ai_platform/gitlab_adapter/`
- 対応Issue: [#29](https://github.com/AtsushiNi/gitlab-ai-platform/issues/29) (M1-1、インターフェース定義)。
  REST実装は [M1-2](https://github.com/AtsushiNi/gitlab-ai-platform/issues/30)、
  書き込み許可リスト機構の強化は [M1-3](https://github.com/AtsushiNi/gitlab-ai-platform/issues/31)
- 関連ADR: [ADR-0002](../adr/0002-gitlab-adapter-interface.md)
- ステータス: 実装中(インターフェース定義のみ実装済み。REST実装[M1-2]は未着手)

## 責務

GitLabとのやりとりを一手に引き受ける唯一の窓口。読み取り(`GitLabReader`)と書き込み
(`GitLabWriter`)を`typing.Protocol`で抽象化し、実装(REST, M1-2。将来MCPへの差し替えも想定)を
差し替え可能にする。書き込みは「read / branch作成 / push(コミット) / MR作成 / コメント」のみを
許可リスト方式で提供し、それ以外の操作(merge・protected branchへの直push・branch削除・管理操作)
は**メソッドとして存在させないこと**で機構的に禁止する。

## 前提と非対象

- 前提:
  - 呼び出し側(Poller/Runner/CLI)は `GitLabReader` / `GitLabWriter` / `GitLabAdapter` の
    Protocol型だけを見て実装する。具象クラス(REST実装)に直接依存しない
  - GitLab PATは`api`または`read_api`スコープを持つ想定。スコープ自体では
    「MR作成は許可するがmergeは禁止」という粒度の制御はできないため、その制御はこの
    Adapter層のインターフェース設計(呼べるメソッドの絞り込み)で行う
    (`references/spike-S2-gitlab-rest-api.md`)
- 非対象:
  - `merge`・protected branchへの直push・branch削除・プロジェクト管理系操作
    (作成/メンバー管理等)は提供しない。これらが必要な操作は人間がGitLab UI/CLIで行う
  - protected branchかどうかの実行時判定・権限エラーの詳細ハンドリングはこのインターフェース
    自体の責務ではなく、具象実装(REST, M1-2)と許可リスト機構の強化(M1-3)側で行う
  - GitLab以外の外部システム(Slack通知等)は扱わない
  - リトライ・レート制限(429)ハンドリングの具体的なポリシーは実装(M1-2)側の責務。
    このインターフェースは例外の型のみを定義する

## 公開インターフェース

`GitLabReader` / `GitLabWriter` を`@runtime_checkable`な`typing.Protocol`として定義し、
`GitLabAdapter`はその合成(`GitLabReader, GitLabWriter`両方を満たす)として定義する。
実装場所: `src/gitlab_ai_platform/gitlab_adapter/protocol.py`。

```python
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .types import Branch, CommitAction, Discussion, MergeRequest, MergeRequestDiff, Note


@runtime_checkable
class GitLabReader(Protocol):
    """読み取り専用の操作。`read_api`スコープのPATのみで動作することを想定する。"""

    def get_version(self) -> str:
        """GitLabのバージョン文字列を返す(`GET /version`または`/metadata`)。"""
        ...

    def list_merge_requests(
        self,
        project: str,
        *,
        labels: Sequence[str] = (),
        state: str = "opened",
    ) -> list[MergeRequest]:
        """指定プロジェクトのMR一覧を取得する。"""
        ...

    def get_merge_request(self, project: str, mr_iid: int) -> MergeRequest:
        """MRの詳細を取得する。"""
        ...

    def get_merge_request_diffs(self, project: str, mr_iid: int) -> list[MergeRequestDiff]:
        """MRの差分をファイル単位で取得する(`diffs`エンドポイント。`changes`は使わない)。"""
        ...

    def list_merge_request_discussions(self, project: str, mr_iid: int) -> list[Discussion]:
        """MRのコメントを、返信関係を保ったスレッド単位で取得する。"""
        ...


@runtime_checkable
class GitLabWriter(Protocol):
    """書き込み操作。許可リストに載っている操作のみをメソッドとして持つ。

    許可: branch作成 / push(ファイル変更のコミット) / MR作成 / コメント投稿。
    禁止(メソッドとして存在しない): merge、protected branchへの直push、branch削除、管理操作。
    """

    def create_branch(self, project: str, branch_name: str, ref: str) -> Branch:
        """`ref`を起点に新しいbranchを作成する。"""
        ...

    def push_file_changes(
        self,
        project: str,
        branch: str,
        commit_message: str,
        actions: Sequence[CommitAction],
    ) -> str:
        """`branch`にファイル変更のコミットをpushし、新しいcommit shaを返す。

        Commits API経由のコミット作成であり、git経由の直接pushではない。実装は対象branchが
        protectedの場合に拒否すること(M1-3で許可リスト機構として強化)。
        """
        ...

    def create_merge_request(
        self,
        project: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> MergeRequest:
        """MRを作成する。"""
        ...

    def create_merge_request_comment(self, project: str, mr_iid: int, body: str) -> Note:
        """MRにコメントを投稿する。"""
        ...


@runtime_checkable
class GitLabAdapter(GitLabReader, GitLabWriter, Protocol):
    """GitLab Adapterが満たすべき完全なインターフェース。"""
```

`GitLabWriter`の公開メソッドはこの5つ(`create_branch` / `push_file_changes` /
`create_merge_request` / `create_merge_request_comment`)**以外に増やしてはならない**。
`merge` / `delete_branch` / `push`(git直接push相当) / プロジェクト管理系メソッドを追加する
実装変更は、この設計判断(ADR-0002)を覆すことになるため、追加前に必ずADRを更新する。

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/gitlab_adapter/types.py`。GitLab REST APIのレスポンス
(dict)をそのまま返さず、以下のdataclassに正規化してから返す(呼び出し側をREST/MCPの構造差異から
切り離すため)。すべて`frozen=True`のイミュータブルなdataclass。

| 型 | フィールド | 補足 |
|---|---|---|
| `CommitActionType` (Enum) | `CREATE` / `UPDATE` / `DELETE` | GitLab Commits APIの`actions[].action`に対応 |
| `CommitAction` | `action: CommitActionType`, `file_path: str`, `content: str \| None = None` | `push_file_changes`の1ファイル分の変更。`DELETE`時は`content`不要 |
| `Branch` | `name: str`, `commit_sha: str`, `protected: bool` | |
| `MergeRequest` | `project: str`, `iid: int`, `title: str`, `description: str`, `state: str`, `source_branch: str`, `target_branch: str`, `sha: str`, `author: str`, `labels: tuple[str, ...] = ()`, `web_url: str = ""` | |
| `MergeRequestDiff` | `old_path: str`, `new_path: str`, `diff: str`, `new_file: bool`, `renamed_file: bool`, `deleted_file: bool` | `GET /merge_requests/:iid/diffs`に対応。`changes`エンドポイント(deprecated)は使わない |
| `Note` | `id: int`, `body: str`, `author: str`, `created_at: str`, `system: bool = False` | 単発コメント、またはDiscussion内の1件の発言 |
| `Discussion` | `id: str`, `notes: tuple[Note, ...]` | 返信関係を保ったスレッド単位のコメント |

## エラー時の振る舞い

実装場所: `src/gitlab_ai_platform/gitlab_adapter/errors.py`。

- `GitLabAdapterError(Exception)` — Adapter経由のGitLab操作が失敗したことを表す基底例外。
  呼び出し側はまずこの型でcatchすればAdapter起因の失敗を一括して扱える
- `GitLabApiError(GitLabAdapterError)` — GitLab APIがエラーレスポンスを返したことを表す。
  `status_code: int | None`を持つ(HTTPステータスコード)。呼び出し側は`status_code`を見て
  リトライ可否を判断できる(例: 429ならリトライ、404なら即失敗)
- 429ハンドリングやリトライの具体的な回数・バックオフ方針は、このインターフェースでは定義しない。
  REST実装(M1-2)側の責務であり、実装時にこの仕様(または新設するADR)を更新すること
- `GitLabWriter`の各メソッドは、対象branchがprotectedである等の理由でGitLab側が操作を拒否した
  場合も`GitLabApiError`を送出する想定(protected branch判定自体はREST実装/M1-3の責務で、
  このインターフェースは「拒否されたらエラーになる」という契約のみを保証する)

## テスト方針

実装場所: `tests/gitlab_ai_platform/gitlab_adapter/`(`src/`をミラー、[ADR-0001](../adr/0001-repository-structure.md))。

- `test_protocol.py`:
  - `GitLabWriter`の公開メソッド集合が許可リスト
    (`create_branch` / `push_file_changes` / `create_merge_request` / `create_merge_request_comment`)
    と**完全一致**することを検証する(`_public_methods()`ヘルパーで`dir()`から`_`始まりを除いたもの)。
    禁止操作名(`merge` / `delete_branch` / `push`等)の集合とも非交差であることを検証する。
    将来誰かが禁止操作をうっかり追加した場合にこのテストが落ちる
  - `GitLabReader`の公開メソッド集合が読み取り5メソッドと一致することを検証する
  - Protocolを満たすダミー実装(`_FakeFullAdapter` / `_FakeReaderOnly`)に対して
    `isinstance(impl, GitLabReader/GitLabWriter/GitLabAdapter)`が期待通り`True`/`False`になることを
    検証する(構造的部分型が意図通り機能することの確認)
- `test_types.py`: dataclassのデフォルト値・イミュータブル性(`frozen=True`)を検証する
- `test_errors.py`: `GitLabApiError`が`status_code`を保持すること、`GitLabAdapterError`の
  サブクラスであることを検証する
- REST実装(M1-2)のテストでは、実GitLabへは繋がず、HTTPレイヤーをモック/フィクスチャ化する
  (CLAUDE.mdのテスト方針)。このインターフェース自体のテストはHTTPに触れない

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表のGitLab Adapter行、
  および「設計原則(ADR化する判断)」節
- [ADR-0002: GitLab Adapter のインターフェース設計](../adr/0002-gitlab-adapter-interface.md)
- ソースコード: `src/gitlab_ai_platform/gitlab_adapter/`
  (`protocol.py` / `types.py` / `errors.py` / `__init__.py`)
- `references/spike-S2-gitlab-rest-api.md` — PATスコープの制御粒度に関する調査
