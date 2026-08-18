# レビュー運用フロー

- ステータス: 完了
- 対応Issue: [#18](https://github.com/AtsushiNi/gitlab-ai-platform/issues/18) (D-14)

「レビュー待ち」ラベル付与からAI事前レビュー・人間の確認・GitLabへのコメント・再レビューまでの
日々の運用手順を、実際に叩くコマンド・GitLab上の操作に沿って書く。自分ひとりで使う間も、
毎回同じ手順を辿れるようにしておくことに価値がある。

## 対象範囲

このガイドは**日々の運用ループそのもの**(いつ何をするか)に絞る。関連する内容は他のガイドへ譲る。

- ツール全体の位置づけ・何をしない/できないかは [getting-started.md](getting-started.md)
- 保存されたレビュー結果ファイルの読み方・指摘の採用/棄却の考え方は
  [reading-results.md](reading-results.md)
- CLIの全オプション・終了コードは [cli-reference.md](cli-reference.md)
- `config.toml`の各項目は [operations/configuration.md](../operations/configuration.md)

## 全体の流れ

[architecture.md「データフロー(MVP)」](../architecture.md)で定義されている流れを、実際の
コマンド・操作に対応付けると次のようになる。

```text
1. GitLab側で実装者がMRに「レビュー待ち」ラベルを付与
2. AI事前レビューが実行される(下記「2. AI事前レビューを実行する」参照)
3. 人間がVS Code等でレビュー結果とdiffを確認する
4. 必要なら追加調査する
5. 人間が本当に必要な指摘だけをGitLabへ手動でコメントする
6. 実装者が指摘を踏まえて修正しpushする
7. 新しいcommitに対して2に戻る(再レビュー)
```

## 1. MRに「レビュー待ち」ラベルを付与する

実装者(自分自身でもよい)が、レビューしてほしいMRにGitLab上で`レビュー待ち`ラベルを付ける。
ラベル名は`config.toml`の`review.label`で変更可能(既定値`"レビュー待ち"`、
[operations/configuration.md](../operations/configuration.md)参照)。

このラベル付与という行為自体がAIレビューの起点になる(下記の`watch`常駐モードが動いている
前提)。ラベルを外さない限り、同じMRに新しいcommitがpushされるたびに再レビューの対象になる
(「6. 再レビューする」参照)。

## 2. AI事前レビューを実行する

レビューの実行方法には2通りある。使い分けの基準は「ラベル付与を検出させたいか、
1件を明示的に指定して試したいか」。

### 方法A: `watch`常駐モード(通常の運用)

`レビュー待ち`ラベル付きMRを`config.toml`の`gitlab.projects`に指定したプロジェクト群から
自動検出し、検出したMRを順次レビューし続ける。日々の運用はこちらが基本形になる。

```powershell
gitlab-ai-platform watch
```

- `config.toml`の`poller.interval_seconds`(既定60秒)間隔で対象プロジェクトを走査し、
  `レビュー待ち`ラベル付きMRのうち未処理のcommitを検出するとレビューを実行する
  ([specs/poller.md](../specs/poller.md)、[specs/cli.md](../specs/cli.md)参照)
- Ctrl+C(SIGINT)またはSIGTERMで、実行中のサイクル完了後にgraceful shutdownする
- 同一の`state.db`(`config.toml`の`store.db_path`)に対して二重起動すると
  `AlreadyRunningError`(終了コード16)になる。別の設定ファイルで別プロセスを動かす場合以外は
  同時に2つ起動しない
- 1件のMRのレビューが失敗しても(GitLab Adapter/Workspace/Runner/Review/State Storeいずれかの
  エラー)、ログに記録して次のMR・次のサイクルへ処理を続ける。想定外の例外(バグ)だけは
  プロセスごと落ちる(ADR-0009参照)
- `review`サブコマンドと違い、`watch`はMRごとの結果サマリを標準出力に表示しない。実行状況を
  追うには`--log-dir`で構造化ログを出力するか(下記)、`reviews/index.jsonl`を見る
  ([reading-results.md](reading-results.md)参照)

継続的に動かす場合は、ログをファイルに残しておくと後から追いやすい。

```powershell
gitlab-ai-platform --log-dir logs watch
```

### 方法B: `review`単発実行(デバッグ・プロンプト改善用)

対象のproject/MR IIDを直接指定して1件だけレビューする。`watch`を常時起動していない場合や、
特定のMRだけすぐに(ポーリング間隔を待たずに)レビューしたい場合、プロンプトの挙動を
確認したい場合に使う。

```powershell
gitlab-ai-platform review group/project-a 123
```

- `config.toml`の`gitlab.projects`/`review.label`は参照しない。ラベルの有無に関わらず、
  指定したMRを実行する
- 同一commitへの再実行を許可している(既存レコードを`RUNNING`に更新してやり直す)。
  プロンプトを変えて試行錯誤する用途を想定した挙動
- 完了すると、保存先パス(`result.md`/`result.json`)・実行ログ・worktreeのパス・
  指摘件数のサマリが標準出力にそのまま表示される

いずれの方法でも、レビュー結果は`reviews/<project>/<mr_iid>/<sha>/`に保存される
(4ファイルの内容は[reading-results.md](reading-results.md)参照)。

## 3. 人間がVS Codeで確認する

レビューが完了したら、まず`result.md`を読む(概要→重要度順の指摘、
[reading-results.md](reading-results.md)参照)。

- `review`サブコマンドを実行した直後なら、標準出力に表示された`result.md`のパスをそのまま
  VS Codeで開けばよい
- `watch`経由の場合は`reviews/index.jsonl`から対象の`result_dir`を探す
  ([reading-results.md](reading-results.md)「どこから読むか」参照)

レビュー実行に使ったworktree(標準出力に表示される、または
`workspace/<root配下>/worktrees/<slug>/mr-<iid>/`)は、レビュー完了後も自動では削除されない
(ディスク上限超過時のGCで最終利用時刻の古いものから破棄される、
[specs/workspace-manager.md](../specs/workspace-manager.md)参照)。そのため、実際のMRの
差分・コードそのものをVS Codeで直接開いて指摘の妥当性を確認できる。

## 4. 必要なら追加調査する

`result.md`の`rationale`(根拠)だけでは判断できない場合、次の手段がある。

- **`input.md`を読む**: Claude Codeに渡した完成後のプロンプト全文。AIが何を前提にその指摘を
  出したかを確認できる
- **worktree上でコードを直接読む**: 上記の通りworktreeは残っているので、MRのdiff以外の
  周辺コードも含めて自分で確認できる
- **対話型Claude Code + GitLab Adapter MCP Server**: VS Code拡張やCLIの対話型Claude Codeに
  GitLab Adapter MCP Server(M2-12、[specs/adapter-mcp-server.md](../specs/adapter-mcp-server.md))
  を`--mcp-config`で登録しておくと、セッション中にエージェント自身が関連するMR/Issueの取得
  (`list_merge_request_discussions`等)をツールとして呼び出しながら深掘りできる。
  このサーバーはMRのworktreeを持つプロジェクトのディレクトリで起動すればcwdのgit remoteから
  対象プロジェクトを自動解決する(`--mcp-config`起動時のcwdが解決対象になる。
  [specs/adapter-mcp-server.md「デフォルトプロジェクトの自動解決」](../specs/adapter-mcp-server.md)参照)。
  ただし「MRのworktreeで対話型Claude Codeをワンコマンド起動する」専用の導線(M2-4、
  `references/タスク整理.md`の追加調査モード)自体はまだ実装されていない。現時点では
  上記のMCPサーバーを汎用の対話型セッションから利用する形になる

## 5. 人間がGitLabにコメントする

**AIは指摘を勝手にGitLabへ投稿しない**([getting-started.md「何をしないか」](getting-started.md#何をしないか重要)
参照)。採用すると判断した指摘だけを、人間が次のいずれかの方法でGitLabへコメントする。

- **GitLab Web UI で直接コメントする**(最も単純で確実な方法)
- **対話型Claude Code + GitLab Adapter MCP Server経由で投稿する**: 上記のMCPサーバーには
  `create_merge_request_comment`ツールが含まれる。ただしこれはAIが自律的に判断して投稿する
  機能ではなく、人間が「この内容でこのMRにコメントして」と明示的に指示した場合にのみ、
  対話型セッションがそのツール呼び出しを代行するだけである(AIレビューパイプライン自体は
  引き続きこのツールを一切呼び出さない)

棄却する指摘は「なぜ違うか」を一言メモしておくと、同じ指摘が別MRで再び出た時の判断が速い
([reading-results.md「指摘を採用/棄却するときの考え方」](reading-results.md#指摘を採用棄却するときの考え方)参照)。

## 6. 実装者が修正する

指摘を踏まえてコードを修正し、通常通りMRへpushする。

## 7. 再レビューする

新しいcommitがpushされると、`(project, mr_iid, commit_sha)`の組み合わせが変わるため
State Storeにとっては未処理の対象になる。

- **`watch`常駐モードを動かしたままの場合**: `レビュー待ち`ラベルを外していなければ、次の
  ポーリングサイクル(既定60秒間隔)で新しいcommitが自動的に検出され、再レビューが実行される。
  何も操作する必要はない
- **`review`単発実行の場合**: 新しいcommitに対して同じコマンドをもう一度実行する

```powershell
gitlab-ai-platform review group/project-a 123
```

再レビューされても`レビュー待ち`ラベル自体は自動では外れない(AIレビューはラベルを操作しない。
`create_branch`/`push_file_changes`/`create_merge_request`/`create_merge_request_comment`
以外のGitLab書き込みは行わない、[getting-started.md「何をしないか」](getting-started.md#何をしないか重要)参照)。
同一commitへの再レビューは`watch`ではスキップされる(State Storeが既に処理済みと判断するため、
無駄な再実行にはならない)ので、確認・コメントが終わった後もラベルをそのまま残しておいて問題ない。
チームでラベルを「レビュー待ち」以外の状態(例: 対応完了)に張り替える運用にしたい場合は、
それはこのツールが強制するものではなく、チーム側で決めるとよい。

## 補足: 対象から外したい場合

- **特定のMRをAI事前レビューの対象から外したい**: `レビュー待ち`ラベルを付けない、または
  既に付けている場合は外す。`watch`常駐モードは`list_merge_requests(labels=(review_label,))`で
  絞り込んでいるため、ラベルが無ければそもそも検出されない
  ([specs/poller.md](../specs/poller.md)参照)
- **特定のプロジェクトを`watch`の走査対象から外したい**: `config.toml`の`gitlab.projects`から
  該当プロジェクトを外す([operations/configuration.md](../operations/configuration.md)参照)。
  ただし`review`単発実行は`gitlab.projects`を参照しないため、`gitlab.projects`に含まれない
  プロジェクトのMRでも`review`で明示的に指定すれば実行できてしまう点に注意
  ([faq.md](faq.md)も参照)
- **マージ済み・クローズ済みのMRは自動的に対象外**: `list_merge_requests`は既定で
  `state="opened"`のみを取得するため、`watch`が誤って完了済みMRを再走査することはない

## 関連ドキュメント

- [getting-started.md](getting-started.md) — ツール全体の位置づけ、何をしない/できないか
- [reading-results.md](reading-results.md) — 保存されたレビュー結果の読み方、指摘の採用/棄却の考え方
- [specs/cli.md](../specs/cli.md) — `review`/`watch`サブコマンドの公開インターフェース・終了コード
- [specs/poller.md](../specs/poller.md) — `レビュー待ち`ラベル付きMRの検出ロジック(MR Poller)
- [specs/adapter-mcp-server.md](../specs/adapter-mcp-server.md) — 対話型Claude Codeからの追加調査・
  コメント投稿に使えるGitLab Adapter MCP Server
- [operations/configuration.md](../operations/configuration.md) — `gitlab.projects`/`review.label`/
  `poller.interval_seconds`等の設定項目
- [faq.md](faq.md) — 誤検知・対象外MR・エラー時の切り分け等、短い疑問への回答
