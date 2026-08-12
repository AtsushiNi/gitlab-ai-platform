"""GitLab Adapter が読み書きするデータの型。

REST/MCPどちらの実装を使っても同じ形に正規化することで、呼び出し側(Poller/Runner/CLI)を
実装詳細から切り離す(`docs/architecture.md` の設計方針)。GitLab REST APIのレスポンス構造を
そのまま透過させず、Adapter層で必要なフィールドだけを持つ形に絞っている。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CommitActionType(str, Enum):
    """`push_file_changes`で1ファイルに対して行う操作の種別。

    GitLab Commits API(`POST /projects/:id/repository/commits`)の`actions[].action`に対応する。
    """

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True)
class CommitAction:
    """コミットに含める1ファイル分の変更。"""

    action: CommitActionType
    file_path: str
    content: str | None = None


@dataclass(frozen=True)
class Branch:
    name: str
    commit_sha: str
    protected: bool


@dataclass(frozen=True)
class MergeRequest:
    project: str
    iid: int
    title: str
    description: str
    state: str
    source_branch: str
    target_branch: str
    sha: str
    author: str
    labels: tuple[str, ...] = field(default_factory=tuple)
    web_url: str = ""


@dataclass(frozen=True)
class MergeRequestDiff:
    """MR差分の1ファイル分。`GET /merge_requests/:iid/diffs`のレスポンスに対応する。

    `changes`エンドポイントはdeprecatedのため使わない(`references/spike-S2-gitlab-rest-api.md`)。
    """

    old_path: str
    new_path: str
    diff: str
    new_file: bool
    renamed_file: bool
    deleted_file: bool


@dataclass(frozen=True)
class Note:
    """MRに対する単発コメント、またはディスカッション内の1件の発言。"""

    id: int
    body: str
    author: str
    created_at: str
    system: bool = False


@dataclass(frozen=True)
class Discussion:
    """返信関係を保った状態のコメントスレッド。`notes`エンドポイントより文脈を保てる。"""

    id: str
    notes: tuple[Note, ...]


__all__ = [
    "CommitActionType",
    "CommitAction",
    "Branch",
    "MergeRequest",
    "MergeRequestDiff",
    "Note",
    "Discussion",
]
