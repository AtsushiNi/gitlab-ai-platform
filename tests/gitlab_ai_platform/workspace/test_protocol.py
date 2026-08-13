from pathlib import Path

from gitlab_ai_platform.workspace import WorkspaceManager, WorktreeHandle

_EXPECTED_PUBLIC_METHODS = {"prepare", "discard", "collect_garbage"}


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


def test_fake_workspace_manager_satisfies_protocol():
    manager = _FakeWorkspaceManager()

    assert isinstance(manager, WorkspaceManager)
