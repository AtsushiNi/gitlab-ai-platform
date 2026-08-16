# FAQ

- ステータス: 完了
- 対応Issue: [#17](https://github.com/AtsushiNi/gitlab-ai-platform/issues/17) (D-13)

[getting-started.md](getting-started.md) の入口で聞かれそうな短い疑問への即答集。
詳しい内容はそれぞれのリンク先を参照。

### レビュー結果は自動でGitLabに投稿されるのか?

されない。結果はローカルの `reviews/<project>/<mr_iid>/<sha>/` に保存されるだけで、
GitLabへ書き込む処理はレビューのパイプラインに含まれていない。詳細と実装上の根拠は
[getting-started.md「何をしないか」](getting-started.md#何をしないか重要)を参照。

### マージも自動でされるのか?

されない。`merge` はGitLab Adapterにメソッドとして存在せず、Adapter経由では
呼び出しようがない([getting-started.md「何をしないか」](getting-started.md#何をしないか重要)参照)。

### 誤検知が多かったらどうすればいいか?

現時点ではプロンプト側の調整が主な対応策になる。`review` サブコマンドは
デバッグ・プロンプト改善用の単発実行として用意されており、同じMRに対して
繰り返し実行できる。AIレビューがどこまで見つけて何を見逃すか、誤検知の傾向は
[limitations.md](limitations.md)(ステータス: 未着手)にまとめる予定。

### 対象外にしたいMRはどうするか?

MR Pollerが検出するのは `レビュー待ち` ラベル(`config.toml` の `review.label` で
変更可能)が付いたMRだけなので、ラベルを付けなければ検出されない。ただしMR
Pollerを配線するwatchモード(M1-11)は本ドキュメント作成時点でまだ未実装で、
現状動くのは指定した1件のMRを都度手動で実行する `review` サブコマンドのみ
([cli-reference.md](cli-reference.md)参照)。既にラベルを付けてしまった
MRを除外する運用フローは [review-workflow.md](review-workflow.md)(ステータス: 未着手)を参照。

### 対象外にしたいプロジェクトはどうするか?

`config.toml` の `gitlab.projects` はMR Pollerの走査対象を絞る設定だが、これも
watchモード(M1-11)向けで現状は未配線。`review` サブコマンドはproject/MR
IIDを直接コマンドライン引数で指定する単発実行のため、`gitlab.projects` に
含まれていないプロジェクトのMRでも実行できてしまう。実行対象を絞りたい場合は
今のところ運用(実行するコマンドライン)で管理する必要がある。設定項目の詳細は
[operations/configuration.md](../operations/configuration.md)を参照。

### 同じMRが何度もレビューされないか?

されない。`(project, MR IID, commit SHA)` の組み合わせをState Storeが記録しており、
同一commitへの二重レビューを防ぐ。新しいpushがあれば新しいcommit SHAとして
再レビュー対象になる([architecture.md](../architecture.md)「コンポーネントの責務と境界」
表のState Store行参照)。ただし
`review` サブコマンドでの単発実行は、デバッグ用途のため同一commitへの再実行を
あえて許可している(既存レコードを `RUNNING` に更新して実行し直す)。

### レビューにどれくらい時間がかかるか?

Claude Codeのヘッドレス実行にかかる時間次第で、既定のタイムアウトは1800秒
(`config.toml` の `runner.timeout_seconds`)。Bedrockの認証情報解決が詰まると
最大60秒程度余分にかかることがある。詳細は
[operations/setup-windows.md §3.2](../operations/setup-windows.md#32-amazon-bedrock認証の設定)を参照。

### Claude Codeが誤ってコード自体を書き換えてしまわないか?

レビューはMR単位のworktree上で実行され、変更はcommit・pushされない。実行後の
worktreeに変更が残っていたとしても、GitLabへは伝播しない(上記「自動投稿・マージ」の
通り、書き込み系のAdapterメソッドをレビューパイプラインが呼び出さないため)。

### エラーで失敗した。何を見ればいいか?

`review` サブコマンドは失敗した段階(GitLab Adapter/Workspace/Runner/Review/
State Store)を標準エラー出力と終了コードで示す。詳しい切り分け方は
[operations/troubleshooting.md](../operations/troubleshooting.md)
(ステータス: 未着手の場合はスコープ外)、コマンドの全オプションは
[cli-reference.md](cli-reference.md)を参照。
