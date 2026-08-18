"""実装フェーズ(M4-8、Job種別`implement`)。

実装計画フェーズ(`plan`)の結果を元に、実際にファイルを編集し、テストを実行し、
ローカルにcommitする。`design`/`plan`と同じ「プロンプト設計→Runner実行→応答パース→保存」の
流れを踏襲するが、この2フェーズと異なり実際のworktree(`Workspace Manager.prepare_for_issue`)
上でClaude Codeを実行し、GitLabへのbranch作成(`create_branch`)も行う(ADR-0033)。
実際にRunnerを呼び出しJobとして処理する部分(`JobHandler`)は`cli/dispatcher.py`の
`build_implement_handler`。

**無人実行トラック限定の機能。** 対話型トラック(VS Code拡張)では対話しながら実装を
進めるため、このフェーズ自体が不要。

**GitLabへの実際のpush(`push_file_changes`経由でのコミット)はこのフェーズのスコープ外。**
M4-9(push と MR 作成)が別フェーズとして担う。このフェーズはローカルworktree内での
git commitまでで完結する(`docs/operations/security.md`参照)。
"""

from __future__ import annotations

from .errors import (
    ImplementationNotCommittedError,
    ImplementError,
    ImplementOutputParseError,
)
from .git_ops import WorktreeState, read_worktree_state
from .job import (
    build_implement_job_payload,
    build_implement_job_result,
    build_resolved_implement_job_result,
    implement_job_payload_to_args,
)
from .parser import parse_implement_output
from .prompts import build_implement_instructions
from .types import ImplementInput, ImplementResult

__all__ = [
    "ImplementError",
    "ImplementInput",
    "ImplementOutputParseError",
    "ImplementResult",
    "ImplementationNotCommittedError",
    "WorktreeState",
    "build_implement_instructions",
    "build_implement_job_payload",
    "build_implement_job_result",
    "build_resolved_implement_job_result",
    "implement_job_payload_to_args",
    "parse_implement_output",
    "read_worktree_state",
]
