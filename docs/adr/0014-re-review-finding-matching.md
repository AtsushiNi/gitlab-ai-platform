# ADR-0014: 再レビュー時の指摘マッチング方式

- Issue: [#81](https://github.com/AtsushiNi/gitlab-ai-platform/issues/81) (M2-2)
- 状態: 決定

## 背景・制約

- `references/タスク整理.md` M2-2は「新規 push 検出時の再実行。前回の指摘を引き継ぎ、
  『修正済み / 未対応 / 新規』を区別する」ことを求めている。
- 新規push自体の検出は追加実装を必要としない。MR Poller(`poller/poller.py`)は既に
  `(project, mr_iid, commit_sha)`をState Storeと突き合わせて未処理commitを起票しており
  (`docs/specs/poller.md`「同一MRでも新しい`commit_sha`(再push)は別レコードとして起票される」)、
  再pushされたcommitも通常の未処理MRと同様にレビュー実行(`cli/single_run.py`の
  `execute_review`)へつながる。このADRの対象は「前回レビューの指摘一覧と今回の指摘一覧を、
  どうやって同一の指摘として突き合わせるか」という判断のみ。
- 前回・今回のレビューはそれぞれ独立したClaude Codeの実行結果(`review.parser.parse_review_output`
  が返す`ReviewResult`)であり、同じ問題を指していても`rationale`/`suggestion`の文面や`line`が
  実行のたびに変わりうる。行番号や本文の完全一致では再レビューのたびに「別の指摘」として
  扱われてしまい実用に耐えない。
- CLAUDE.mdのテスト方針は「外部依存(GitLab API等)に触れるテストはモック/フィクスチャを使い、
  実サービスへは繋がない」ことを求めており、`docs/specs/review-output.md`のテスト方針も
  「実際にClaude Codeを起動するテストは行わない」と明記している。マッチング判定自体を
  実LLM呼び出しに頼る設計は、この方針の下では決定的にテストできない。
- ADR-0001はこのリポジトリの外部依存を`requests`/`pytest`等に絞る方針を採っており、
  新規の重い依存(埋め込みベクトルライブラリ等)の追加はMVPの範囲を超える。

## 決定

### 指摘の同一性は「同一ファイル」+「rationale/suggestionのテキスト類似度」で判定する

`review/comparison.py`の`compare_findings(previous, current)`で、今回・前回の`Finding`の
全ペアについて次の類似度を計算する。

- `file`が異なれば類似度0(別ファイルの指摘は同一視しない)
- 同一ファイルの場合、`f"{rationale}\n{suggestion}"`を連結したテキストに対して標準ライブラリ
  `difflib.SequenceMatcher.ratio()`を適用し、0.0〜1.0の類似度を得る

類似度がしきい値(0.6)以上のペアを候補とし、スコアの高い順に貪欲法で1対1に確定させる
(同一の今回指摘・前回指摘は一度しか対応付けない)。マッチした今回指摘は「未対応」、
マッチしなかった今回指摘は「新規」、マッチしなかった前回指摘は「修正済み」とする。

しきい値0.6は、実際のレビュー文面(「〜していません」「〜が不足しています」等の言い回りの
揺れ)を踏まえ、「同じ指摘の言い換え」と「別の指摘」を分離できる経験的な値として採用した。
将来チューニングが必要になった場合は`review/comparison.py`の`_SIMILARITY_THRESHOLD`のみを
変更すればよい。

### 前回レビューが存在しない場合は比較しない

`compare_findings`は`previous`が`None`の場合`None`を返す。`ReviewComparison`という「比較結果」
の型自体を、比較が成立する場合にのみ作る(初回レビューに空の比較結果を持たせて「新規」に
全件分類する、といった曖昧な表現をしない)。

### 「前回」は同一MRの直近のレビュー(今回と異なるcommit)とする

`cli/single_run.py`の`_find_previous_review_result`が、レビュー結果の索引(`review.read_index`)
から同一`(project, mr_iid)`のエントリを絞り込み、今回のcommit以外で`reviewed_at`が最も新しい
ものを前回とする。索引の絞り込み(フィルタ)自体は`review/index.py`の責務としない
(`docs/specs/review-output.md`「索引の検索・フィルタ・表示は行わない」)ため、CLI層
(`cli/single_run.py`)に置いた。

### 比較結果はレビュー結果ファイル(`result.json`/`result.md`)にのみ持たせ、索引(`index.jsonl`)は変更しない

`review/storage.py`の`save_review`が`comparison: ReviewComparison | None`を受け取り、
`result.json`に`comparison`フィールド(無ければ`null`)として保存し、`result.md`では各指摘に
「新規」「未対応」のバッジを付け、末尾に「前回から修正された指摘」セクションを追加する。
索引(`IndexEntry`)のスキーマは変更しない(「却下した選択肢」参照)。

### プロンプト(`review/prompts.py`)は変更しない

`build_review_instructions()`は引数を取らない純粋関数のままとし、前回の指摘一覧をプロンプトに
含めない。比較はレビュー実行後にプログラム的に行う(「却下した選択肢」のLLM分類案を参照)。

## 却下した選択肢

- **行番号・本文の完全一致でマッチングする**: diffによって行番号がずれる、`rationale`の文面が
  実行のたびに変わる、という2つの理由で再レビューのたびに一致しなくなり実用に耐えない。
- **前回の指摘一覧をプロンプトに含め、Claude Code自身に「修正済み/未対応/新規」を分類させる**:
  - 意味的な同一性判断はLLMの方が優れている可能性があるが、この判断の正しさを実LLM呼び出し無しに
    テストできない(CLAUDE.mdのテスト方針・`docs/specs/review-output.md`のテスト方針に反する)。
  - `review/prompts.py`(M1-8で「MR固有の情報を含まない純粋関数」と確定させた設計、
    `docs/specs/prompts.md`)と`review/types.py`の出力スキーマ(M1-9、
    [ADR-0006](0006-review-output-schema.md))の両方を拡張する必要があり、変更範囲が
    このADRのスコープを超える。
  - 将来、決定的なマッチングの精度が実用上不足すると分かった場合に、補助的な手段として
    再検討する余地は残す。
- **埋め込みベクトル(embeddings)による意味的類似度判定**: 新規の外部依存・API呼び出し
  (追加のBedrockモデル呼び出し等)が必要になり、ADR-0001の依存方針・MVPのシンプルさ方針に反する。
  `difflib`は標準ライブラリで追加依存が不要なため、まずこちらで十分な精度が出るか試す。
- **索引(`IndexEntry`)に新規/未対応/修正済みの件数を追加する**: 索引一覧からの俯瞰
  (M2-3、レビュー結果の確認UX)には有用だが、既存の`index.jsonl`の後方互換性
  (`_entry_from_dict`が辞書のキーを直接参照する実装)への影響、およびスキーマ変更の
  テスト範囲が広がることから、今回は見送った。`result.json`/`result.md`側に持たせれば
  個々のレビュー結果としては閲覧可能であり、索引側の拡張が必要になった場合はM2-3で
  改めて設計する。
- **最大重みマッチング(ハンガリー法等)による厳密な最適割当て**: 指摘の件数はMR1件あたり
  高々数十件程度であり、スコア降順の貪欲法でも実用上十分な精度が出ると判断した。
  厳密な最適化アルゴリズムを導入するほどの規模ではない。

## 影響

- `review/types.py`: `ReviewComparison`(`new`/`unresolved`/`resolved`の3つの`Finding`タプル)
  を追加。
- `review/comparison.py`: 新規モジュール。`compare_findings`を実装。
- `review/storage.py`: `save_review`が`comparison`引数を受け取れるようになった。前回結果を
  読み戻すための`load_review_result`を追加。
- `review/markdown.py`: `render_markdown`が`comparison`引数を受け取れるようになった。
  `comparison`省略時の出力は従来と完全に同じ(後方互換)。
- `cli/single_run.py`: `execute_review`が前回レビュー結果を索引から探し、比較結果を
  `save_review`に渡すようになった(`_find_previous_review_result`)。
- `docs/specs/review-output.md`・`docs/specs/poller.md`: 上記の挙動を反映。
