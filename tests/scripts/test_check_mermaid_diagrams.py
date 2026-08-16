"""check_mermaid_diagrams.pyのMarkdown解析ロジックのテスト。

実際のmmdc呼び出し(render())はnpx/Node.js/ネットワークに依存するため、CIのpytestでは
検証しない(このリポジトリの方針: 外部依存に触れるテストはモック/フィクスチャを使う)。
mmdc自体の呼び出しはdocs CIワークフロー側で別途実行して検証する。
"""

from pathlib import Path

from check_mermaid_diagrams import find_diagrams


def test_find_diagrams_extracts_single_block(tmp_path: Path) -> None:
    md = tmp_path / "sample.md"
    md.write_text(
        "# Title\n\n```mermaid\nflowchart TD\n    A --> B\n```\n\n本文。\n",
        encoding="utf-8",
    )

    diagrams = find_diagrams(md)

    assert diagrams == ["flowchart TD\n    A --> B\n"]


def test_find_diagrams_extracts_multiple_blocks(tmp_path: Path) -> None:
    md = tmp_path / "sample.md"
    md.write_text(
        "```mermaid\nflowchart TD\n    A --> B\n```\n"
        "テキスト\n"
        "```mermaid\nsequenceDiagram\n    A->>B: hello\n```\n",
        encoding="utf-8",
    )

    diagrams = find_diagrams(md)

    assert len(diagrams) == 2
    assert "flowchart TD" in diagrams[0]
    assert "sequenceDiagram" in diagrams[1]


def test_find_diagrams_ignores_non_mermaid_fences(tmp_path: Path) -> None:
    md = tmp_path / "sample.md"
    md.write_text(
        "```python\nprint('hello')\n```\n\n```text\nplain text\n```\n",
        encoding="utf-8",
    )

    diagrams = find_diagrams(md)

    assert diagrams == []


def test_find_diagrams_returns_empty_for_no_fences(tmp_path: Path) -> None:
    md = tmp_path / "sample.md"
    md.write_text("# Title\n\n本文のみ。\n", encoding="utf-8")

    diagrams = find_diagrams(md)

    assert diagrams == []
