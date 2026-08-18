# GitLab Adapter

- 実装場所: `src/gitlab_ai_platform/gitlab_adapter/`
- 対応Issue: [#29](https://github.com/AtsushiNi/gitlab-ai-platform/issues/29) (M1-1、インターフェース定義)、
  [#30](https://github.com/AtsushiNi/gitlab-ai-platform/issues/30) (M1-2、REST実装)、
  [#31](https://github.com/AtsushiNi/gitlab-ai-platform/issues/31) (M1-3、書き込み許可リスト機構の強化)、
  [#47](https://github.com/AtsushiNi/gitlab-ai-platform/issues/47) (M2-10、Issue/MR操作の拡充)、
  [#114](https://github.com/AtsushiNi/gitlab-ai-platform/issues/114) (M4-8、`get_default_branch`追加)
- 関連ADR: [ADR-0002](../adr/0002-gitlab-adapter-interface.md)、
  ADR-0032(`get_default_branch`追加の判断)
- ステータス: 実装済み(インターフェース定義[M1-1] + REST実装[M1-2] +
  許可リスト機構の強化[M1-3] + Issue/MR操作の拡充[M2-10] + `get_default_branch`[M4-8])

## 責務

GitLabとのやりとりを一手に引き受ける唯一の窓口。読み取り(`GitLabReader`)と書き込み
(`GitLabWriter`)を`typing.Protocol`で抽象化し、実装(REST, M1-2。将来MCPへの差し替えも想定)を
差し替え可能にする。書き込みは「read / branch作成 / push(コミット) / MR作成 / コメント / MR更新 /
Issue作成 / Issue更新」のみを許可リスト方式で提供し、それ以外の操作(merge・protected branchへの
直push・branch削除・管理操作・Issue/MRの状態遷移[close/reopen等])は**メソッドとして存在させない
(または引数として渡せない)こと**で機構的に禁止する。

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
  - Issue/MRの状態遷移(close/reopen/merge)は`update_issue`/`update_merge_request`の引数
    としても提供しない。GitLab REST APIの`state_event`パラメータは一切受け付けない
    (M2-10、[#47](https://github.com/AtsushiNi/gitlab-ai-platform/issues/47))
  - protected branchかどうかの実行時判定・権限エラーの詳細ハンドリングはこのインターフェース
    自体の責務ではなく、具象実装(REST, M1-2)側で行う
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

from .types import (
    Branch,
    CommitAction,
    Discussion,
    Issue,
    MergeRequest,
    MergeRequestDiff,
    Note,
)


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

    def get_merge_request_diffs(
        self, project: str, mr_iid: int
    ) -> list[MergeRequestDiff]:
        """MRの差分をファイル単位で取得する(`diffs`エンドポイント。`changes`は使わない)。"""
        ...

    def list_merge_request_discussions(
        self, project: str, mr_iid: int
    ) -> list[Discussion]:
        """MRのコメントを、返信関係を保ったスレッド単位で取得する。"""
        ...

    def list_issues(
        self,
        project: str,
        *,
        labels: Sequence[str] = (),
        state: str = "opened",
    ) -> list[Issue]:
        """指定プロジェクトのIssue一覧を取得する。"""
        ...

    def get_issue(self, project: str, issue_iid: int) -> Issue:
        """Issueの詳細を取得する。"""
        ...

    def get_default_branch(self, project: str) -> str:
        """プロジェクトのdefault branch名を取得する(M4-8、ADR-0032)。"""
        ...


@runtime_checkable
class GitLabWriter(Protocol):
    """書き込み操作。許可リストに載っている操作のみをメソッドとして持つ。

    許可: branch作成 / push(ファイル変更のコミット) / MR作成 / コメント投稿 /
    MR更新(タイトル・説明のみ) / Issue作成 / Issue更新(タイトル・説明のみ)。
    禁止(メソッドとして存在しない、または引数として渡せない): merge、protected branchへの
    直push、branch削除、管理操作、Issue/MRのクローズ・再オープン等の状態遷移
    (`state_event`相当の引数は`update_issue`/`update_merge_request`に存在しない)。
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

        Commits API経由のコミット作成であり、git経由の直接pushではない。実装(REST, M1-2)は
        対象branchがprotectedの場合、GitLab APIへ到達する前に`ProtectedBranchError`を送出して拒否する。
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

    def create_merge_request_comment(
        self, project: str, mr_iid: int, body: str
    ) -> Note:
        """MRにコメントを投稿する。"""
        ...

    def update_merge_request(
        self,
        project: str,
        mr_iid: int,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> MergeRequest:
        """MRのタイトル・説明を更新する。`state_event`(close/reopen/merge相当)は持たない。"""
        ...

    def create_issue(self, project: str, title: str, description: str = "") -> Issue:
        """Issueを作成する。"""
        ...

    def update_issue(
        self,
        project: str,
        issue_iid: int,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> Issue:
        """Issueのタイトル・説明を更新する。`state_event`(close/reopen相当)は持たない。"""
        ...


@runtime_checkable
class GitLabAdapter(GitLabReader, GitLabWriter, Protocol):
    """GitLab Adapterが満たすべき完全なインターフェース。"""
```

`GitLabWriter`の公開メソッドはこの7つ(`create_branch` / `push_file_changes` /
`create_merge_request` / `create_merge_request_comment` / `update_merge_request` /
`create_issue` / `update_issue`)**以外に増やしてはならない**。
`merge` / `delete_branch` / `push`(git直接push相当) / プロジェクト管理系メソッドを追加する
実装変更、および`update_merge_request`/`update_issue`に`state_event`相当の引数を追加する
変更は、この設計判断(ADR-0002)を覆すことになるため、追加前に必ずADRを更新する。

## 入出力スキーマ

実装場所: `src/gitlab_ai_platform/gitlab_adapter/types.py`。GitLab REST APIのレスポンス
(dict)をそのまま返さず、以下のdataclassに正規化してから返す(呼び出し側をREST/MCPの構造差異から
切り離すため)。すべて`frozen=True`のイミュータブルなdataclass。

| 型 | フィールド | 補足 |
|---|---|---|
| `CommitActionType` (Enum) | `CREATE` / `UPDATE` / `DELETE` | GitLab Commits APIの`actions[].action`に対応 |
| `CommitAction` | `action: CommitActionType`, `file_path: str`, `content: str \| None = None` | `push_file_changes`の1ファイル分の変更。`DELETE`時は`content`不要 |
| `Branch` | `name: str`, `commit_sha: str`, `protected: bool` | |
| `Issue` | `project: str`, `iid: int`, `title: str`, `description: str`, `state: str`, `author: str`, `labels: tuple[str, ...] = ()`, `web_url: str = ""` | `MergeRequest`と同じ形で正規化したGitLab Issue(M2-10) |
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
  場合も`GitLabApiError`を送出する想定(protected branch判定自体はREST実装(M1-2)の責務で、
  このインターフェースは「拒否されたらエラーになる」という契約のみを保証する)

## 監査ログ(M1-3、M2-10で拡充)

実装場所: `src/gitlab_ai_platform/gitlab_adapter/rest.py`の`GitLabRestAdapter._record_write`。
`GitLabWriter`の7メソッド(`create_branch` / `push_file_changes` / `create_merge_request` /
`create_merge_request_comment` / `update_merge_request` / `create_issue` / `update_issue`)は、
呼び出し結果を`logging_`モジュール(M0-3)経由で構造化ログに記録する。X-1(セキュリティレビュー)の
証跡として、書き込み操作が実際に何を行った/拒否したかを後から追跡できるようにするためのもの。

- ログの`message`は固定文字列`"gitlab_adapter.write"`。`extra`に以下のフィールドを乗せる:
  - `operation`: メソッド名(例: `push_file_changes`、`update_issue`)
  - `status`: `success` / `rejected_protected_branch`(`push_file_changes`がprotected branchを
    拒否した場合) / `error`(GitLab APIがエラーを返した場合)
  - 操作対象を特定する識別子(`project` / `branch` / `mr_iid` / `note_id` / `issue_iid`等。
    操作によって異なる)
- **commit本文・MRの説明文・コメント本文・Issueのタイトルや説明文の内容そのものなど、
  任意長・機微になりうる内容は記録しない**。`update_issue`/`update_merge_request`も更新後の
  `title`/`description`の値そのものはログに含めず、対象の`issue_iid`/`mr_iid`のみを記録する。
  監査ログは「誰が/いつ/どのbranch・MR・Issueに対して/何の操作を/成功したか拒否・失敗したか」を
  追えれば十分という設計判断による
- ファイルログ(JSON)への出力は`logging_.setup_logging(log_dir=...)`の設定に依存する
  (このモジュール自体はロガーを取得するだけで、出力先やレベルは呼び出し側の責務)

## テスト方針

実装場所: `tests/gitlab_ai_platform/gitlab_adapter/`(`src/`をミラー、[ADR-0001](../adr/0001-repository-structure.md))。

- `test_protocol.py`:
  - `GitLabWriter`の公開メソッド集合が許可リスト
    (`create_branch` / `push_file_changes` / `create_merge_request` / `create_merge_request_comment` /
    `update_merge_request` / `create_issue` / `update_issue`)と**完全一致**することを検証する
    (`_public_methods()`ヘルパーで`dir()`から`_`始まりを除いたもの)。
    禁止操作名(`merge` / `delete_branch` / `push` / `close_issue` / `reopen_merge_request`等、
    state_eventを介した状態遷移相当を含む)の集合とも非交差であることを検証する。
    将来誰かが禁止操作をうっかり追加した場合にこのテストが落ちる
  - `GitLabReader`の公開メソッド集合が読み取り8メソッド(MR系5 + `list_issues`/`get_issue`/
    `get_default_branch`、M4-8で追加)と一致することを検証する
  - Protocolを満たすダミー実装(`_FakeFullAdapter` / `_FakeReaderOnly`)に対して
    `isinstance(impl, GitLabReader/GitLabWriter/GitLabAdapter)`が期待通り`True`/`False`になることを
    検証する(構造的部分型が意図通り機能することの確認)
- `test_types.py`: dataclass(`Issue`含む)のデフォルト値・イミュータブル性(`frozen=True`)を検証する
- `test_errors.py`: `GitLabApiError`が`status_code`を保持すること、`GitLabAdapterError`の
  サブクラスであることを検証する
- `test_rest.py`(REST実装、M1-2/M1-3/M2-10):
  - `GitLabRestAdapter`の公開メソッド集合が許可リスト(読み取り8・書き込み7)と完全一致することを、
    `test_protocol.py`と同じ強さで具象クラス側にも適用する
    (`test_rest_adapter_exposes_only_allow_listed_operations`)
  - `get_default_branch`が`GET /projects/:id`から`default_branch`フィールドを取り出すこと、
    フィールド欠落時に`GitLabApiError`を送出することを検証する(M4-8、ADR-0032)
  - `push_file_changes`がprotected branchへの直pushを、Commits APIへ到達する前に
    `ProtectedBranchError`で拒否すること(`test_push_file_changes_rejects_protected_branch_without_calling_commits_api`)
  - `update_issue`/`update_merge_request`が送信するリクエストボディに`state_event`キーが
    一切含まれないこと、指定したフィールド(`title`/`description`)のみが送信されることを
    `test_update_issue_does_not_send_state_event` /
    `test_update_merge_request_does_not_send_state_event`で回帰確認する
  - 監査ログ(M1-3、M2-10): 7つの書き込みメソッドそれぞれについて、成功時に`status="success"`の
    ログが1件記録されること、`push_file_changes`はprotected branch拒否時に
    `status="rejected_protected_branch"`、GitLab APIエラー時に`status="error"`が記録されること、
    コメント本文・Issue/MRのタイトルや説明文などの機微な内容がログに含まれないことを`caplog`で検証する
- REST実装(M1-2)のテストでは、実GitLabへは繋がず、HTTPレイヤーをモック/フィクスチャ化する
  (CLAUDE.mdのテスト方針)。このインターフェース自体のテストはHTTPに触れない

## セキュリティ機構の棚卸し(X-1向け)

X-1(セキュリティレビュー。`references/タスク整理.md`の該当項目。本ドキュメント作成時点では
GitHub Issue未作成なので、Issue化した時点でこの節にリンクを追加すること)向けに、
「禁止操作が機構として不可能であること」をどの実装・テストが担保しているかをまとめる。

| 担保したい性質 | 実装 | テスト |
|---|---|---|
| merge/branch削除/管理操作がAdapter経由で呼び出せない | `protocol.py`の`GitLabWriter`にメソッドとして定義しない(ADR-0002) | `test_protocol.py`(`GitLabWriter`公開メソッド集合の完全一致・禁止操作名との非交差)、`test_rest.py`(`GitLabRestAdapter`側でも同様の完全一致) |
| Issue/MRのクローズ・再オープン等の状態遷移がAdapter経由で行えない | `protocol.py`の`update_issue`/`update_merge_request`に`state_event`相当の引数を持たせない(ADR-0002 M2-10追記)。`rest.py`の`_build_update_body`も`title`/`description`以外は組み立てられない | `test_protocol.py`(禁止操作名`close_issue`等との非交差)、`test_rest.py::test_update_issue_does_not_send_state_event` / `test_update_merge_request_does_not_send_state_event`(送信ボディに`state_event`が含まれないことを直接検証) |
| protected branchへの直pushを拒否する | `rest.py`の`_reject_if_branch_protected`(`push_file_changes`内でGitLab APIへ到達する前にチェック) | `test_rest.py::test_push_file_changes_rejects_protected_branch_without_calling_commits_api` |
| 書き込み操作の実行を事後に追跡できる | `rest.py`の`_record_write`(全7メソッドの成功/拒否/エラーを構造化ログに記録) | `test_rest.py`の監査ログ系テスト(上記テスト方針参照) |
| PATスコープだけに頼らない設計であること | `typing.Protocol`による許可リスト方式そのもの(`references/spike-S2-gitlab-rest-api.md`でPATスコープの粒度不足を確認済み) | 上記2項目のテストが機構として担保 |

**M1-3で見送った項目**(理由は[ADR-0002の追記](../adr/0002-gitlab-adapter-interface.md)参照):

- GitLabのprotected branchフラグに依存しない、config層でのbranch名パターンによる追加ガード。
  Runner/Poller側の設計が固まる時点(M2以降)で再検討する

## 関連ドキュメント

- [architecture.md](../architecture.md) 「コンポーネントの責務と境界」表のGitLab Adapter行、
  および「設計原則(ADR化する判断)」節
- [ADR-0002: GitLab Adapter のインターフェース設計](../adr/0002-gitlab-adapter-interface.md)
- ソースコード: `src/gitlab_ai_platform/gitlab_adapter/`
  (`protocol.py` / `types.py` / `errors.py` / `rest.py` / `__init__.py`)
- `references/spike-S2-gitlab-rest-api.md` — PATスコープの制御粒度に関する調査
