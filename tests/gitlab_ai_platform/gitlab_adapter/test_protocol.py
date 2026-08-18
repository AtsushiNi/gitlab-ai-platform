from collections.abc import Sequence

from gitlab_ai_platform.gitlab_adapter import (
    Branch,
    CommitAction,
    Discussion,
    GitLabAdapter,
    GitLabReader,
    GitLabWriter,
    Issue,
    MergeRequest,
    MergeRequestDiff,
    Note,
)

# 許可リストに存在してよい書き込み操作。ここに無い名前(merge/delete_branch等)が
# GitLabWriterに増えていないことを、下のテストで機構として保証する。
_ALLOWED_WRITE_OPERATIONS = {
    "create_branch",
    "push_file_changes",
    "create_merge_request",
    "create_merge_request_comment",
    "update_merge_request",
    "create_issue",
    "update_issue",
}

_FORBIDDEN_WRITE_OPERATIONS = {
    "merge",
    "merge_merge_request",
    "accept_merge_request",
    "delete_branch",
    "push",  # git経由の直接push相当の操作は存在してはいけない
    "push_to_protected_branch",
    "create_project",
    "delete_project",
    "add_project_member",
    "update_project_member",
    # Issue/MRの状態遷移(state_event相当)。update_issue/update_merge_requestは
    # タイトル・説明のみを更新でき、close/reopen/mergeは別メソッドとしても
    # 引数としても存在しない(M2-10、#47)
    "close_issue",
    "reopen_issue",
    "close_merge_request",
    "reopen_merge_request",
    "delete_issue",
}


def _public_methods(protocol_cls: type) -> set[str]:
    return {name for name in dir(protocol_cls) if not name.startswith("_")}


def test_gitlab_writer_exposes_only_allow_listed_operations():
    assert _public_methods(GitLabWriter) == _ALLOWED_WRITE_OPERATIONS


def test_gitlab_writer_does_not_expose_forbidden_operations():
    exposed = _public_methods(GitLabWriter)

    assert exposed.isdisjoint(_FORBIDDEN_WRITE_OPERATIONS)


def test_gitlab_reader_exposes_read_operations():
    # M4-8(#114, ADR-0032)で`get_default_branch`を追加した(既存7メソッドは変更しない)
    assert _public_methods(GitLabReader) == {
        "get_version",
        "list_merge_requests",
        "get_merge_request",
        "get_merge_request_diffs",
        "list_merge_request_discussions",
        "list_issues",
        "get_issue",
        "get_default_branch",
    }


class _FakeFullAdapter:
    """読み取り・書き込み両方を満たすダミー実装。将来のREST/MCP実装が満たすべき形の例。"""

    def get_version(self) -> str:
        return "17.0.0-ee"

    def list_merge_requests(
        self, project: str, *, labels: Sequence[str] = (), state: str = "opened"
    ) -> list[MergeRequest]:
        return []

    def get_merge_request(self, project: str, mr_iid: int) -> MergeRequest:
        return MergeRequest(
            project=project,
            iid=mr_iid,
            title="title",
            description="",
            state="opened",
            source_branch="feature/x",
            target_branch="main",
            sha="abc123",
            author="alice",
        )

    def get_merge_request_diffs(
        self, project: str, mr_iid: int
    ) -> list[MergeRequestDiff]:
        return []

    def list_merge_request_discussions(
        self, project: str, mr_iid: int
    ) -> list[Discussion]:
        return []

    def list_issues(
        self, project: str, *, labels: Sequence[str] = (), state: str = "opened"
    ) -> list[Issue]:
        return []

    def get_issue(self, project: str, issue_iid: int) -> Issue:
        return Issue(
            project=project,
            iid=issue_iid,
            title="title",
            description="",
            state="opened",
            author="alice",
        )

    def get_default_branch(self, project: str) -> str:
        return "main"

    def create_branch(self, project: str, branch_name: str, ref: str) -> Branch:
        return Branch(name=branch_name, commit_sha="abc123", protected=False)

    def push_file_changes(
        self,
        project: str,
        branch: str,
        commit_message: str,
        actions: Sequence[CommitAction],
    ) -> str:
        return "def456"

    def create_merge_request(
        self,
        project: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> MergeRequest:
        return self.get_merge_request(project, 1)

    def create_merge_request_comment(
        self, project: str, mr_iid: int, body: str
    ) -> Note:
        return Note(id=1, body=body, author="ai-bot", created_at="2026-08-13T00:00:00Z")

    def update_merge_request(
        self,
        project: str,
        mr_iid: int,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> MergeRequest:
        return self.get_merge_request(project, mr_iid)

    def create_issue(self, project: str, title: str, description: str = "") -> Issue:
        return self.get_issue(project, 1)

    def update_issue(
        self,
        project: str,
        issue_iid: int,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> Issue:
        return self.get_issue(project, issue_iid)


class _FakeReaderOnly:
    """書き込みメソッドを一切持たない、読み取り専用実装の例。"""

    def get_version(self) -> str:
        return "17.0.0-ee"

    def list_merge_requests(
        self, project: str, *, labels: Sequence[str] = (), state: str = "opened"
    ) -> list[MergeRequest]:
        return []

    def get_merge_request(self, project: str, mr_iid: int) -> MergeRequest:
        raise NotImplementedError

    def get_merge_request_diffs(
        self, project: str, mr_iid: int
    ) -> list[MergeRequestDiff]:
        return []

    def list_merge_request_discussions(
        self, project: str, mr_iid: int
    ) -> list[Discussion]:
        return []

    def list_issues(
        self, project: str, *, labels: Sequence[str] = (), state: str = "opened"
    ) -> list[Issue]:
        return []

    def get_issue(self, project: str, issue_iid: int) -> Issue:
        raise NotImplementedError

    def get_default_branch(self, project: str) -> str:
        return "main"


def test_full_fake_satisfies_gitlab_adapter_protocol():
    adapter = _FakeFullAdapter()

    assert isinstance(adapter, GitLabReader)
    assert isinstance(adapter, GitLabWriter)
    assert isinstance(adapter, GitLabAdapter)


def test_reader_only_fake_satisfies_reader_but_not_full_adapter():
    reader = _FakeReaderOnly()

    assert isinstance(reader, GitLabReader)
    assert not isinstance(reader, GitLabWriter)
    assert not isinstance(reader, GitLabAdapter)
