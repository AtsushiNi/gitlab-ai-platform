# ADR-0007: MR Poller の設計

- Issue: [#33](https://github.com/AtsushiNi/gitlab-ai-platform/issues/33) (M1-5)
- 状態: 決定

## 背景・制約

- `docs/architecture.md` により、MR Pollerの責務は「30〜60秒間隔で対象プロジェクトを走査し、
  `レビュー待ち` ラベルのMRを抽出、State Storeと突き合わせて未処理commitを検出、レビューを
  起票する」こと。境界として「Webhookは当面採用しない(MVP時点)」「GitLabへの書き込みは
  しない」が明記されている。
- 「起票する」の具体的な意味は、この時点(M1-5)ではState Store(M1-4)にレコードを
  作ることまでで、実際のレビュー実行(Workspace Manager準備→Runner起動→Review解析)は
  後続(M1-12でE2E結線)。GitLab Adapter(M1-1/M1-2)・State Store(M1-4)は実装済みで、
  Pollerはこの2つを組み合わせる最初のコンポーネントになる。
- 複数プロジェクトを対象にでき、1プロジェクトの障害が他の走査を止めない必要がある
  (長時間稼働する常駐プロセスとして使われるため)。

## 決定

### `GitLabAdapter`ではなく`GitLabReader`にのみ依存する

`MrPoller.__init__`の型ヒントは`gitlab_adapter.protocol.GitLabReader`(読み取り専用)を使い、
書き込み操作を持つ`GitLabWriter`/`GitLabAdapter`は登場させない。GitLab Adapter自身が
許可リスト方式で書き込みを機構的に絞り込んでいる(ADR-0002)のと同じ考え方で、「Pollerは
GitLabに書き込まない」という境界をプロンプトの約束事ではなく型で表現する。

### `find`→`create`の順で起票し、`DuplicateReviewError`は競合として無視する

State Store(ADR-0003)の`find`で`(project, mr_iid, commit_sha)`が未処理かを確認してから
`create`する。`find`から`create`までの間に別プロセス(複数Poller稼働・手動再実行等)が
同じレコードを作っていた場合、`create`は`DuplicateReviewError`を送出する。これは
「既に起票済み」を意味するだけで異常ではないため、Pollerはこれを握りつぶし
`PollResult`にも計上しない。二重起票そのものの防止はState Store側の一意制約が担い、
Poller側の`find`は「同じサイクル内で無駄な`create`呼び出しを減らす」ための事前チェックに
留める。

### 1プロジェクト・1レコード単位で失敗を分離し、`PollResult.errors`に集約する

`poll_once`は対象プロジェクトを順に走査する。`list_merge_requests`が`GitLabAdapterError`を
送出した場合、そのプロジェクトの走査を諦めて次のプロジェクトに進む。同様に、個々のMRの
起票時に(`DuplicateReviewError`以外の)`StateStoreError`が発生した場合も、そのMRの処理を
諦めて次のMRに進む。いずれも例外を上位に伝播させず、`PollResult.errors`
(`PollError(project, mr_iid, message)`)に集約して返す。常駐ループの中で1件の障害が
プロセス全体を落とすことを避け、呼び出し側(CLI)がログ・監視のために失敗内容を参照できる
形にした。

### Poller自身のための`protocol.py`は作らない

GitLab Adapter・State Store・Workspace Manager・Claude Code Runnerは、いずれも
「今の実装」と「将来の差し替え実装」が具体的に想定されているため`typing.Protocol`で
抽象化している(REST→MCP、Windows git→Linux/Docker、subprocess→コンテナ等)。
Pollerには現時点でそのような差し替え候補がなく、`MrPoller`は`GitLabReader`と
`StateStore`という既に抽象化済みの2つのProtocolを組み合わせるだけの具象クラスとした。
将来Webhookベースの検出(M3-6、`docs/architecture.md`の成長パス)を追加する際に、
Poller/Webhook双方が満たすべき共通インターフェースが必要になれば、その時点で
`protocol.py`を追加する。

### ポーリングループ(`run`)はPollerが持ち、停止は`stop_event`で受け取る

「30〜60秒間隔で走査する」というループ自体はPollerの責務(`docs/architecture.md`)のため、
`MrPoller.run(*, interval_seconds, stop_event)`として実装した。一方、プロセスの
graceful shutdown(SIGINT/SIGTERM等のシグナルハンドリング、多重起動防止)はCLI
(M1-10/11)の責務であり、Pollerはシグナルを直接扱わない。`threading.Event`を
`stop_event`として受け取り、セットされたら実行中のサイクル完了後に停止する形で、
ループの所有者(Poller)と停止のトリガー(CLI)を分離した。

## 却下した選択肢

- **`create`だけを呼び、`find`による事前チェックを省く**: `DuplicateReviewError`だけで
  二重起票防止は成立するため機能的には十分だが、`docs/architecture.md`が
  「State Storeと突き合わせて未処理commitを検出」と明示的に書いており、`find`による
  意図の可読性(「まず確認してから作る」という流れ)を優先した。
- **1プロジェクトの走査失敗やレコード起票失敗を例外として伝播させ、`poll_once`全体を
  失敗させる**: 常駐プロセスとして30〜60秒ごとに繰り返し呼ばれる想定のため、1件の
  一時的な障害(ネットワーク・DBロック等)でサイクル全体、ひいてはプロセスが落ちるのは
  過剰。エラーを集約して返す形にし、継続動作を優先した。
- **Poller用の`protocol.py`/`errors.py`を他モジュールに合わせて機械的に用意する**:
  現時点で差し替え候補・独自例外のどちらも無く、YAGNI(`CLAUDE.md`の
  「hypothetical future requirementsのために設計しない」)に反するため見送った。
- **`run`のループ制御にシグナルハンドラ(`signal.signal`)を直接組み込む**: シグナル
  ハンドリングはプロセス全体に関わる関心事であり、CLI(M1-10/11、常駐モードの入口)の
  責務と重複する。Pollerを「CLIからもテストコードからも同じように停止できる」部品に
  保つため、`stop_event`という汎用的な合図の受け渡しに留めた。

## 影響

- CLI(M1-10/11)は`Config`(`gitlab_url`/`gitlab_token`/`projects`/`poll_interval_seconds`/
  `review_label`)から`GitLabRestAdapter`・`SqliteStateStore`・`MrPoller`を組み立て、
  `MrPoller.run(interval_seconds=config.poll_interval_seconds, stop_event=...)`を
  呼び出す形になる。シグナルハンドラでの`stop_event.set()`と多重起動防止はCLI側で実装する。
- M1-12(E2E結線)では、`PollResult.created`(新規起票された`DetectedReview`一覧)を
  受け取った後続処理として、Workspace Manager準備→Claude Code Runner実行→Review解析→
  `StateStore.update_status`という一連の流れを追加することになる。Poller自身の
  `poll_once`/`run`のインターフェースは変更不要な想定。
