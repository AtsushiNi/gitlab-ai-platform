from pathlib import Path

from gitlab_ai_platform.workspace import (
    IssueWorktreeHandle,
    WorkspaceManager,
    WorktreeHandle,
)

# M4-8(#114, ADR-0031)で`prepare_for_issue`/`discard_for_issue`を追加した。
# `prepare`/`discard`(MR単位)のシグネチャ・挙動は変更していない
_EXPECTED_PUBLIC_METHODS = {
    "prepare",
    "discard",
    "collect_garbage",
    "prepare_for_issue",
    "discard_for_issue",
}


def _public_methods(protocol_cls: type) -> set[str]:
    return {name for name in dir(protocol_cls) if not name.startswith("_")}


def test_workspace_manager_exposes_only_expected_operations():
    # 「git操作以外(ビルド・テスト実行など)はしない」(docs/architecture.md)という境界を、
    # 将来メソッドが増えた際にこのテストで検知できるようにする。
    assert _public_methods(WorkspaceManager) == _EXPECTED_PUBLIC_METHODS


class _FakeWorkspaceManager:
    """Protocolを満たすダミー実装。git実装(GitWorkspaceManager)もこの形になる。"""

    def __init__(self) -> None:
        self._handles: dict[tuple[str, int], WorktreeHandle] = {}
        self._issue_handles: dict[tuple[str, int], IssueWorktreeHandle] = {}

    def prepare(self, project: str, mr_iid: int, ref: str) -> WorktreeHandle:
        handle = WorktreeHandle(
            project=project,
            mr_iid=mr_iid,
            path=Path(f"/tmp/{project}/{mr_iid}"),
            branch=f"mr-{mr_iid}",
            sha=ref,
        )
        self._handles[(project, mr_iid)] = handle
        return handle

    def discard(self, project: str, mr_iid: int) -> None:
        self._handles.pop((project, mr_iid), None)

    def collect_garbage(self) -> list[WorktreeHandle]:
        return []

    def prepare_for_issue(
        self, project: str, issue_iid: int, ref: str
    ) -> IssueWorktreeHandle:
        handle = IssueWorktreeHandle(
            project=project,
            issue_iid=issue_iid,
            path=Path(f"/tmp/{project}/issue-{issue_iid}"),
            branch=f"issue-{issue_iid}",
            sha=ref,
        )
        self._issue_handles[(project, issue_iid)] = handle
        return handle

    def discard_for_issue(self, project: str, issue_iid: int) -> None:
        self._issue_handles.pop((project, issue_iid), None)


def test_fake_workspace_manager_satisfies_protocol():
    manager = _FakeWorkspaceManager()

    assert isinstance(manager, WorkspaceManager)
