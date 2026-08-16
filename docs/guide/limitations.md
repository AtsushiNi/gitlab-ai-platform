# AIレビューの限界

- ステータス: 完了(初版。[M2-7](#m2-7実施後に育てる)実施後に定量データを反映して更新予定)
- 対応Issue: [#21](https://github.com/AtsushiNi/gitlab-ai-platform/issues/21) (D-17)

**AIレビューは人間のレビューを代替するものではなく、事前チェックである。最終判断は常に
人間が行う。** この設計前提は[要件定義](../requirements.md)(「AIが見つけた指摘をそのまま
全部GitLabへ投稿する運用は望んでいない…最終判断は常に人間が行う」)に明記されており、
GitLabへの自動コメント投稿・自動マージをしないという実装上の制約
([architecture.md](../architecture.md)の設計原則、[getting-started.md「何をしないか」](getting-started.md#何をしないか重要))
として具体化されている。

この文書は、その前提を維持するために「AIレビューは何を見つけられて何を見逃すか」を
具体的に書く。[getting-started.md](getting-started.md)がツール全体として何をしない
(自動投稿・自動マージをしない)かを扱うのに対し、本書はレビューという処理そのものの
中身の限界(渡している情報・渡していない情報、指摘の性質)に絞る。

## 現時点の限界の書き方について

M2-7(レビュー品質の評価とプロンプト改善ループ)は本書初版執筆時点でまだ着手されておらず、
対応するGitHub Issueもまだ存在しない。既知のMRサンプルセットに対する誤検知率・見逃し率と
いった定量データはまだ存在しないため、本書ではそれを捏造しない。代わりに、現在のプロンプト
設計([docs/specs/prompts.md](../specs/prompts.md))とClaude Code Runnerの実装
([docs/specs/claude-code-runner.md](../specs/claude-code-runner.md))から読み取れる
**構造的な限界**(何を渡していて何を渡していないか、レビュー対象外の範囲、出力形式に
起因する制約)を中心にまとめる。M2-7実施後にどう育てるかは[末尾](#m2-7実施後に育てる)を参照。

## 1. レビューに渡している情報・渡していない情報

AIレビューが実際に見ているのは、GitLab Adapterが取得したMRタイトル・説明・コメント
(discussion内のsystemノートは除く)・diffと、worktree上に展開されたリポジトリそのものである
([claude-code-runner.md](../specs/claude-code-runner.md)の「入出力スキーマ」)。

- **周辺コード・関連実装・テストコード・`docs/`との整合性は「探索して確認せよ」という
  指示のみで、実際に読むかどうかはClaude Code自身の判断に委ねられている。**
  レビュープロンプト([prompts.md](../specs/prompts.md)「進め方」)はdiffの断片だけで
  判断しないよう指示しているが、これは指示であり保証ではない。Claude Code Runnerは
  `instructions`の中身を解釈・分岐せず不透明な文字列として渡すだけなので
  ([claude-code-runner.md](../specs/claude-code-runner.md)「前提と非対象」)、実際に
  周辺ファイルを読みに行ったかどうかは指摘内容(`rationale`)を読んで人間が判断するしかない
- **CIの実行結果・テストの実際の実行結果はAIに渡されず、AIも実行しない。**
  Workspace Managerの責務は「git操作以外(ビルド・テスト実行など)はしない」と明記されており
  ([architecture.md](../architecture.md)のコンポーネント表)、Claude Codeはテストコードを
  「読んで」矛盾がないかを判断するだけで、実際に`pytest`等を走らせて green/red を確認して
  いるわけではない。ビルドの成否・型チェック・lintの結果も同様に渡されない
- **チーム固有の運用・過去の意思決定・他リポジトリとの整合性はAIは知らない**
  ([reading-results.md](reading-results.md)「採用/棄却の考え方」)。渡されるのはMRの
  タイトル・説明・コメントと対象リポジトリの中身だけで、それ以外の背景知識は無い
- **対象は指定したMRのdiff・関連ファイルのみ。** 複数MR・複数プロジェクトにまたがる影響
  (例: 他プロジェクトが依存しているインターフェースの変更)は対象外
- レビューは特定のcommit SHAに対して1回実行される(State Storeが`(project, mr_iid,
  commit_sha)`単位で管理、[architecture.md](../architecture.md)「状態管理・冪等性」)。
  レビュー後に追加pushがあれば、その内容は新しいSHAでの再レビューでしか見られない

## 2. ツール権限の制限による見落とし(気づきにくい)

`review`サブコマンドの`--allowed-tools`/`--disallowed-tools`/`--permission-mode`は
既定値が空/未指定であり([docs/specs/cli.md](../specs/cli.md)のオプション表)、明示的に
指定しない限りClaude Code側の権限判断に委ねられる。この状態でツール呼び出しが権限拒否
(`permission_denials`)された場合、プロンプトが指示している「リポジトリを実際に探索する」
が実行時に一部できていない可能性がある。

**この見落としは`result.md`を読むだけでは気づけない。** `review.parser.parse_review_output`は
`permission_denials`が空でなければ警告ログのみを残して解析を続行する仕様であり
([docs/specs/review-output.md](../specs/review-output.md)「前提」)、保存される
`result.json`/`result.md`には反映されない。探索できずに書かれた指摘・要約であっても、
表面上は「探索した上での結論」のように読めてしまう。指摘の信頼性を深掘りしたい場合は
`run_log.json`(実行ログ)を確認する必要がある([reading-results.md](reading-results.md)の
出力ファイル一覧を参照)。

## 3. レビュー観点そのものによる限定

プロンプト([prompts.md](../specs/prompts.md))は「重視する観点」7項目(致命的なバグ/
仕様との不整合/既存コードとの不整合/デグレ/テスト不足/将来問題になる設計/明らかな実装ミス)
に絞る一方、「抑制する観点」として次の2点を**意図的に**指摘させないよう指示している。

- 個人の好みレベルの指摘(フォーマットや命名の好みなど)
- 大量の些末な指摘の羅列(ノイズになるもの)

これはノイズを減らし本当に重要な指摘を埋もれさせないための設計判断だが、裏を返せば
**軽微に見える指摘は意図的に握りつぶされている可能性がある。** パフォーマンスの実測、
セキュリティ専用スキャン(SAST等)、実際のUI・実行時挙動の確認は、そもそもこの観点の
どれにも該当しないため対象外になる。

## 4. 重要度(severity)の性質

指摘には`critical`/`major`/`minor`のいずれかが付くが、プロンプトはこの3段階に厳密な
採点基準を与えているわけではなく、「重視する観点」を渡した上でClaude Code自身に
判断させている([reading-results.md](reading-results.md)「重要度の判断基準」)。
severityの値そのものを鵜呑みにせず、`rationale`(根拠)を読んで実際に妥当かを判断する
必要がある。特にminorとされていても文脈次第で実は無視できない、ということもありうる。

## 5. 出力形式に起因する制約

- 1指摘 = 1提案の形式(`Finding`、[review-output.md](../specs/review-output.md))で
  出力されるため、複数の指摘が同じ根本原因を指している場合でも、その関連性は考慮されない
- `review.parser.parse_review_output`が検証するのはJSONとしての構造(`severity`が
  3値のいずれか、`file`/`rationale`/`suggestion`が空でない文字列、`line`が整数か
  `null`)のみであり、**指摘内容が事実として正しいかどうかは検証していない。**
  構文的に正しいJSONでも、存在しないファイルパスを参照していたり、diffの解釈を
  誤っていたりする指摘を機械的に弾く仕組みは無い

## AIの指摘をそのまま信用してはいけない理由

上記の構造的な限界に加え、AIレビューの指摘はLLMの応答である以上、次の性質を前提に
読む必要がある。

- 事実と異なる内容(存在しないファイル参照、誤った行番号、diffの解釈違い)を確信ありげに
  書く可能性がある。プロンプトは「想像や推測だけで指摘を作らず、実際にファイルを読んで
  から指摘してください」と指示しているが、これは指示であり、Runner・パーサーいずれの層でも
  内容の正しさを検証・強制する仕組みは無い([claude-code-runner.md](../specs/claude-code-runner.md)
  「前提と非対象」、上記4節)
- 応答冒頭の「確認事項」(確信が持てない点)は見落としのサインである。`findings`に
  挙がらなかったことは「問題なし」を意味しない
- 指摘0件(「特に指摘なし」)は人間のレビューを省略してよいという意味ではない
  ([reading-results.md](reading-results.md)「採用/棄却の考え方」)。AIが確認していないこと
  (実際に実行して確認した挙動、UI、外部システムとの結合、要件との整合など)は常に残っている

これらを踏まえた指摘の採用/棄却の実務的な考え方は[reading-results.md](reading-results.md)
「指摘を採用/棄却するときの考え方」を参照。誤検知が多いと感じた場合の当面の対応は
[faq.md](faq.md#誤検知が多かったらどうすればいいか)を参照。

## M2-7実施後に育てる

本書は`references/タスク整理.md`の方針(「育て続ける」文書としてD-17を位置づけ、
「M2-7の品質評価結果を反映する」)に従い、以下が揃った時点で内容を更新する。

- 既知のMRサンプルセットに対する実際の誤検知率・見逃し率
- プロンプト変更前後の回帰比較結果(見逃し/ノイズの増減)
- 上記から見えてくる、観点別(致命的なバグ/仕様との不整合/デグレ/…)の得意・不得意の傾向

それまでの間、本書の内容は上記1〜5節の構造的な限界の記述にとどめ、存在しないデータを
記載しない。

## 関連ドキュメント

- [requirements.md](../requirements.md) — 「最終判断は人間」という設計前提の一次記述
- [architecture.md](../architecture.md) — Reviewの責務と境界、GitLabへ自動投稿しないという
  設計原則
- [getting-started.md](getting-started.md) — ツール全体として何をしない(自動投稿・自動マージ)か
- [reading-results.md](reading-results.md) — 結果の読み方、重要度の判断基準、指摘の採用/棄却
- [faq.md](faq.md) — 「誤検知が多かったらどうすればいいか」等の短い疑問への回答
- [docs/specs/prompts.md](../specs/prompts.md) — レビュー観点そのものの指示内容
- [docs/specs/claude-code-runner.md](../specs/claude-code-runner.md) — Runnerが渡す情報・
  渡さない情報、ツール権限(`allowed_tools`/`disallowed_tools`/`permission_mode`)
- [docs/specs/review-output.md](../specs/review-output.md) — 出力スキーマの検証範囲、
  `permission_denials`の扱い
- `references/タスク整理.md` D-17・M2-7 — 本書の位置づけと「育て続ける」方針の元ネタ
