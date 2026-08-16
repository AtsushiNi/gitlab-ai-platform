#!/usr/bin/env python3
"""docs/ 配下のMarkdownに埋め込まれたmermaidダイアグラムが構文的に描画可能か確認する。

D-12(#16、ドキュメントのCI)向け。`@mermaid-js/mermaid-cli`(mmdc)をnpx経由で都度取得して
実際にレンダリングさせることで、mermaid記法の壊れ(閉じ忘れ・存在しないノード参照等)を
機械的に検出する。レンダリング結果自体(SVG)は使い捨てで、成否だけを見る。

このリポジトリは外部依存を増やしすぎない方針(docs/adr/0001-repository-structure.md)だが、
それはWindows側(オフライン制約のあるレビューツール本体)向けの制約であり、
このリポジトリ自体の開発用CI(GitHub Actions, ubuntu-latest)には適用されない
(docs/adr/0013-docs-ci.md参照)。
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"

# ```mermaid ... ``` のフェンスドコードブロックを抽出する
MERMAID_FENCE = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)

# CIのコンテナ環境ではChromiumのsandboxが使えないことが多いため無効化する
PUPPETEER_CONFIG = '{"args": ["--no-sandbox", "--disable-setuid-sandbox"]}'


def find_diagrams(md_path: Path) -> list[str]:
    text = md_path.read_text(encoding="utf-8")
    return [m.group(1) for m in MERMAID_FENCE.finditer(text)]


def render(diagram: str, tmpdir: Path, index: int) -> tuple[bool, str]:
    mmd_path = tmpdir / f"diagram-{index}.mmd"
    svg_path = tmpdir / f"diagram-{index}.svg"
    puppeteer_config_path = tmpdir / "puppeteer-config.json"
    puppeteer_config_path.write_text(PUPPETEER_CONFIG, encoding="utf-8")
    mmd_path.write_text(diagram, encoding="utf-8")

    result = subprocess.run(
        [
            "npx",
            "--yes",
            "-p",
            "@mermaid-js/mermaid-cli",
            "mmdc",
            "-i",
            str(mmd_path),
            "-o",
            str(svg_path),
            "-p",
            str(puppeteer_config_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    ok = result.returncode == 0 and svg_path.exists()
    output = result.stdout + result.stderr
    return ok, output


def main() -> int:
    md_files = sorted(DOCS_DIR.rglob("*.md"))
    tasks: list[tuple[Path, int, str]] = []
    for md_path in md_files:
        for index, diagram in enumerate(find_diagrams(md_path)):
            tasks.append((md_path, index, diagram))

    if not tasks:
        print("mermaidダイアグラムが見つかりませんでした(docs/配下)。")
        return 0

    print(f"{len(tasks)}件のmermaidダイアグラムを検査します。")

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        for md_path, index, diagram in tasks:
            rel = md_path.relative_to(REPO_ROOT)
            ok, output = render(diagram, tmpdir, index)
            if ok:
                print(f"OK: {rel} (diagram #{index + 1})")
            else:
                print(f"NG: {rel} (diagram #{index + 1})")
                print(output)
                failures.append(f"{rel} (diagram #{index + 1})")

    if failures:
        print("\n以下のmermaidダイアグラムのレンダリングに失敗しました:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nすべてのmermaidダイアグラムが正常にレンダリングできました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
