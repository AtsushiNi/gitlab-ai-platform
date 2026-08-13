# はじめに

- ステータス: 完了
- 対応Issue: [#17](https://github.com/AtsushiNi/gitlab-ai-platform/issues/17) (D-13)

このページは `docs/guide/` の入口。GitLab AI Platform(MRレビュー自動化ツール)が
何をするツールで何をしないツールか、実装者(MRを出す側)が知っておくべき最初の一歩を
まとめる。運用の詳細や結果の読み方は各ガイドへ譲り、ここでは全体像と不安の解消に絞る。

## 何ができるツールか

一言で言うと、**MRレビューに辿り着くまでの機械的な作業をなくすツール**([要件](../requirements.md)参照)。

指定した1件のMRに対し、次のパイプラインを1コマンドで実行できる
([アーキテクチャ](../architecture.md)、[cli.md](../specs/cli.md)参照)。

1. GitLab AdapterがMRのタイトル・説明・コメント・diffを取得する
2. Workspace ManagerがそのMR専用のworktreeを用意する
3. Claude Code Runnerがworktree上でヘッドレスのClaude Codeを起動し、1で取得した情報を
   渡す。Claude Code自身がコードベースを探索しながらレビューする
4. レビュー結果(重要度/ファイル/行/根拠/提案)がJSON+Markdownとして
   `reviews/<project>/<mr_iid>/<sha>/` に保存される

人間は、対象ブランチをローカルにcheckoutしたりClaude Codeに手動でレビューを依頼したりする
手間から解放され、**AIの事前レビュー結果を確認するところから始められる**。結果の読み方は
[reading-results.md](reading-results.md)を参照。

**現時点(本ドキュメント作成時点)では、この実行は `review` サブコマンドで対象の
project/MR IIDを人間が都度指定する形。**`レビュー待ち` ラベル付きMRを自動検出して
横断的にキューイングするMR Pollerというコンポーネントは設計・実装済みだが、CLIへの配線
(常駐のwatchモード、M1-11)はまだ行われておらず、`config.toml` の `gitlab.projects` /
`review.label` もまだ参照されない([cli.md](../specs/cli.md)、
[operations/configuration.md](../operations/configuration.md)参照)。ラベル付与だけで
自動的にレビューが始まる運用は今後のマイルストーンで実現する想定。日々の運用手順は
[review-workflow.md](review-workflow.md)(ステータス: 未着手)を参照。

## 何をしないか(重要)

**AIの指摘を勝手にGitLabへ投稿しない。勝手にマージもしない。**

これは実装の裏付けがある制約であり、単なる運用上の約束事ではない。

- レビュー結果の保存先は `review/storage.py` の `save_review` で、書き込み先は常に
  ローカルの `reviews/` 配下(`config.toml`の`reviews.root`、既定値`"reviews"`。
  詳細は [operations/configuration.md](../operations/configuration.md)を参照)。
  GitLabへの通信は一切発生しない
- GitLabへの書き込みができるのは `gitlab_adapter/rest.py` の `GitLabRestAdapter` が実装する
  `GitLabWriter` の4メソッド(`create_branch` / `push_file_changes` /
  `create_merge_request` / `create_merge_request_comment`)のみで、レビューパイプライン
  (`cli/single_run.py` の `execute_review`)はこれらを一切呼び出さない。レビュー時に
  GitLab Adapterへ渡しているのは読み取り専用の `GitLabReader` だけ
- `merge`・protected branchへの直push・branch削除・GitLabの管理操作は、そもそも
  `GitLabWriter` にメソッドとして存在しない(許可リスト方式。プロンプト上の約束事に
  頼らずコード側の機構として禁止している)。詳細は
  [architecture.md](../architecture.md)の設計原則、[ADR-0002](../adr/0002-gitlab-adapter-interface.md)を参照

つまり、MRを出した実装者に無断で通知が飛んだり、コメントが増えたりすることはない。
指摘を採用するかどうかは常に人間が判断し、必要な指摘だけをGitLabに手動でコメントする
([review-workflow.md](review-workflow.md)、ステータス: 未着手)。将来的に選択的な自動投稿
(M2-5)を検討する余地はあるが、現時点では実装されていない。

その他よくある不安([誤検知が多い場合はどうするか](faq.md)、
[対象外にしたいMRの扱い](faq.md)等)は [faq.md](faq.md) にまとめている。

## 最初の一歩

1. **環境構築**: [operations/setup-windows.md](../operations/setup-windows.md) の手順で
   Python環境・GitLab PAT・Claude Code/Bedrock認証・`config.toml`/`.env` を用意する
2. **単発レビューを試す**: `config.toml` の `gitlab.projects` に含まれるプロジェクトの、
   実在するMRを1件指定して `review` サブコマンドを実行する
   ([setup-windows.md §5](../operations/setup-windows.md#5-初回起動確認) 参照)

   ```powershell
   gitlab-ai-platform review group/project-a 123
   ```

   正常に完了すると、保存先パス(`reviews/.../result.md` / `result.json`)と
   指摘件数のサマリが標準出力に表示される。まずは1件試して挙動を確認し、
   プロンプトや観点の感触をつかむとよい
3. **保存された結果を読む**: [reading-results.md](reading-results.md)
   (出力ファイルの構成、重要度の判断基準、指摘の採用/棄却の考え方)
4. **日々の運用に組み込む**: `レビュー待ち` ラベルを使った運用フローは
   [review-workflow.md](review-workflow.md)(ステータス: 未着手)

コマンド全体のオプション・終了コードは [cli-reference.md](cli-reference.md)
(ステータス: 未着手。当面は `gitlab-ai-platform review --help` を参照)、
うまくいかない場合は [operations/troubleshooting.md](../operations/troubleshooting.md)
(ステータス: 未着手の場合はスコープ外)を参照。

## 次に読むもの

- [faq.md](faq.md) — このページで解消しきれない疑問への短い回答
- [reading-results.md](reading-results.md) — 結果の読み方
- [review-workflow.md](review-workflow.md) — 日々の運用フロー(未着手)
- [limitations.md](limitations.md) — AIレビューの限界(未着手)
- [cli-reference.md](cli-reference.md) — コマンド一覧(未着手)
- [operations/configuration.md](../operations/configuration.md) — `config.toml`/`.env`の全項目リファレンス
