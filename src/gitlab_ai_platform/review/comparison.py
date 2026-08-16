"""再レビュー時に、前回の指摘と今回の指摘を突き合わせる。

M2-2([#81](https://github.com/AtsushiNi/gitlab-ai-platform/issues/81))の「修正済み /
未対応 / 新規」の区別を担当する。マッチング方式(指摘の同一性判定)の設計判断は
[ADR-0014](../../../docs/adr/0014-re-review-finding-matching.md)を参照。

前回・今回のレビューはそれぞれ独立したClaude Codeの実行結果であり、同じ問題を指しても
`rationale`/`suggestion`の文面や`line`は実行のたびに揺れる。行番号やテキストの完全一致は
再レビューのたびに一致しなくなるため実用的でない一方、Claude Code自身に前回の指摘一覧を渡して
分類させる方式は「実LLM呼び出し無しでテストする」というこのリポジトリのテスト方針
(CLAUDE.md)と相性が悪い。そのためここでは標準ライブラリの`difflib`のみを使い、
決定的でテスト可能なテキスト類似度マッチングを行う。
"""

from __future__ import annotations

from difflib import SequenceMatcher

from .types import Finding, ReviewComparison, ReviewResult

# 「同じ指摘の言い換え」と「別の指摘」を実際のレビュー文面の揺れを踏まえて分離できる値として
# 0.6を採用した(ADR-0014)。将来チューニングの余地はある
_SIMILARITY_THRESHOLD = 0.6


def compare_findings(
    previous: ReviewResult | None, current: ReviewResult
) -> ReviewComparison | None:
    """`previous`(前回レビュー)と`current`(今回レビュー)のfindingsを突き合わせる。

    `previous`が`None`(前回レビューが存在しない、すなわち初回レビュー)の場合は
    比較自体が成立しないため`None`を返す。
    """
    if previous is None:
        return None

    # 全(今回, 前回)ペアのうち、同一ファイルかつしきい値以上の類似度を持つものを候補にする
    candidates: list[tuple[float, int, int]] = []
    for cur_idx, cur_finding in enumerate(current.findings):
        for prev_idx, prev_finding in enumerate(previous.findings):
            score = _similarity(cur_finding, prev_finding)
            if score >= _SIMILARITY_THRESHOLD:
                candidates.append((score, cur_idx, prev_idx))

    # スコアの高い候補から確定させる貪欲法(1件の今回findingsは1件の前回findingsとのみ対応)。
    # 厳密な最大重みマッチング(ハンガリー法等)までは行わない。指摘の件数はMR1件あたり
    # 高々数十件程度で、貪欲法でも実用上十分な精度が出るという判断(ADR-0014)
    candidates.sort(key=lambda item: item[0], reverse=True)

    matched_current: set[int] = set()
    matched_previous: set[int] = set()
    for _score, cur_idx, prev_idx in candidates:
        if cur_idx in matched_current or prev_idx in matched_previous:
            continue
        matched_current.add(cur_idx)
        matched_previous.add(prev_idx)

    new = tuple(
        finding
        for idx, finding in enumerate(current.findings)
        if idx not in matched_current
    )
    unresolved = tuple(
        finding
        for idx, finding in enumerate(current.findings)
        if idx in matched_current
    )
    resolved = tuple(
        finding
        for idx, finding in enumerate(previous.findings)
        if idx not in matched_previous
    )
    return ReviewComparison(new=new, unresolved=unresolved, resolved=resolved)


def _similarity(current: Finding, previous: Finding) -> float:
    """2つのFindingが同一の指摘を指している度合い(0.0〜1.0)を返す。

    ファイルが異なれば別の指摘として扱う(0.0)。同一ファイル内では`rationale`/`suggestion`を
    連結したテキストの類似度(`SequenceMatcher.ratio`)で判定する。
    """
    if current.file != previous.file:
        return 0.0
    current_text = f"{current.rationale}\n{current.suggestion}"
    previous_text = f"{previous.rationale}\n{previous.suggestion}"
    return SequenceMatcher(None, current_text, previous_text).ratio()


__all__ = ["compare_findings"]
