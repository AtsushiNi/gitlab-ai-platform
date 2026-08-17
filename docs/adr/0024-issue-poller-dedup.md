# ADR-0024: Issue Poller の二重投入防止設計

- Issue: [#107](https://github.com/AtsushiNi/gitlab-ai-platform/issues/107) (M4-1)
- 状態: 決定

## 背景・制約

- M4(Issue駆動開発)は「無人実行に向くタスクかどうか」をAIに判定させず、Issueへのラベル付与
  (人間の事前判断)で無人実行トラックへ振り分ける設計に変更した(`docs/roadmap.md`「M4.
  Issue駆動開発」)。Issue Poller(M4-1)はその検出部分であり、MR Poller(M1-5、
  [ADR-0007](0007-mr-poller-design.md))と同じ設計パターン(GitLabReaderのみに依存、
  1件の失敗が他を止めない、`run`ループ+`on_detected`コールバック、Webhookは扱わない)を
  Issueに横展開する。
- MR Pollerは「起票」をState Store(`store/`、[ADR-0003](0003-state-store-interface.md))への
  レコード作成に留め、実際のレビュー実行(≒Job化)はCLI(`cli/watch.py`)の`on_detected`が
  `execute_review_job`を呼ぶことで行う。一方、Issue #107本文は「無人実行**Jobをキューへ
  投入する**」ことをPoller自身のスコープと定めている。M4は無人実行トラックであり、実行は
  Runner Dispatcher(M3-3、[ADR-0022](0022-runner-process-separation.md))がJob Queueから
  拾う設計のため、検出と同時にJobを投入する形がM4のパイプライン全体の構造に合致する。
- 二重投入防止の状態管理をどう設計するかが本Issueの一番の判断ポイント(Issue本文)。
  State Store(`store/protocol.py`)は`(project, mr_iid, commit_sha)`という**レビュー固有**の
  複合キーで二重レビューを防ぐが、`commit_sha`に相当する「版」の概念がIssueには無い。
  Job抽象([ADR-0016](0016-job-abstraction.md))は「State Storeを置き換えず、統合もせず、
  別コンポーネントとして併存させる」ことを既に決定しており、Job Repositoryの
  `payload`/`result`は種別非依存の`dict`として扱う設計のため、Job自体に問い合わせ用の
  インデックス(project/issue_iid列など)を持たせる想定にはなっていない。

## 決定

### 専用の Issue Ticket Store(`issue_store/`)を新設し、State Store・Jobいずれとも統合しない

`(project, issue_iid)`単位で「無人実行Jobを起票済みか」だけを記録する、State Storeと同じ形の
`typing.Protocol`(`IssueTicketStore`)を新規パッケージとして追加する:

```python
@runtime_checkable
class IssueTicketStore(Protocol):
    def find(self, project: str, issue_iid: int) -> IssueTicketRecord | None: ...
    def create(self, project: str, issue_iid: int) -> IssueTicketRecord: ...
    def close(self) -> None: ...
```

`create`は同一`(project, issue_iid)`に対して呼ばれた場合`DuplicateIssueTicketError`を送出する
契約とし、一意性の実際の保証はSQLite実装(`PRIMARY KEY (project, issue_iid)`)が担う
(`StateStore.create`/`DuplicateReviewError`と同じ設計)。`status`のような進行状態は持たせない。
Issueの無人実行の進行状態はJob(`JobStatus`)が単独で管理するため、Issue Ticket Storeが
`status`を二重管理するとJobとの状態不整合が起きうる。

### JobRepository Protocolは変更しない。既存のJobのpayload検索による二重投入防止は採用しない

Issue本文が選択肢として挙げた「既存のJobRepositoryのpayloadを検索する仕組みを使う」は採用しない。
理由:

1. `JobRepository.list_by_status`は状態単位の一覧のみを提供し、`DONE`/`FAILED`まで含めた
   「(project, issue_iid)がこれまでに一度でも投入されたか」を調べる操作がない。追加するには
   Protocolの拡張(`payload`の中身に対するクエリ)が必要になるが、ADR-0016がJobの`payload`を
   「種別非依存のdictの箱」と位置づけた設計方針(Job抽象がJobType追加のたびに変更対象に
   ならないようにする)と相容れない。
2. 仮に「進行中(`PENDING`/`RUNNING`/`WAITING_HUMAN`)のJobのみ」を検索対象にしても、
   `DONE`後に同じラベルが付いたままのIssueを次のポーリングサイクルで再度検出し、無限に
   再投入してしまう(MRの「新しいcommit_shaなら再起票」に相当する自然な再処理条件が
   Issueには無いため、"処理済みなら二度と投入しない"という永続的な記録が必要)。
3. M3-2([ADR-0017](0017-job-queue.md))・M3-3・M3-7が既に`JobRepository`に依存しており、
   このProtocolへの変更は影響範囲が広い。M4-1単体のスコープでは変更しない。

### 起票の順序: 「Issue Ticket Storeへの記録」→「Job Queueへの投入」

`ticket_issue_if_unprocessed(store, job_repo, project, issue_iid)`(MR Pollerの
`ticket_if_unprocessed`と対になるモジュール関数、`poller/issue_poller.py`)は以下の順で行う:

1. `store.find` で未処理か確認し、`store.create` で起票する(MRと同じ find→create ダンス。
   `find`から`create`までの間の競合による`DuplicateIssueTicketError`は「既に起票済み」として
   無視する)
2. 起票に成功した場合のみ `job_repo.enqueue(JobType.ISSUE_ANALYSIS, payload)` を呼ぶ

この順序を選んだ理由は、二重投入(同一Issueに対して複数のJobが作られる)より、Job投入漏れ
(Issue Ticket Storeへの記録は成功したがJob投入に失敗し、そのIssueが以後再試行されない)の方が
運用上まだ検知・回復しやすいと判断したため。前者は無人実行パイプラインが同じIssueに対して
並行して2つの結果を出す事故に直結するが、後者は「ラベルが付いているのにいつまでも処理されない
Issue」としてログ(`issue_poller.job_enqueue_failed`)から発見でき、運用者がIssue Ticket Store
から該当レコードを削除すれば再度ポーリング対象に戻せる回復経路がある。

## 却下した選択肢

- **State StoreをIssue向けに拡張する(主キーを`(project, issue_iid, commit_sha | None)`のような
  形にする)**: ADR-0016がJobとState Storeの統合を却下した理由(レビュー以外のJob種別が増える
  たびにState Storeのスキーマを歪める)がそのまま当てはまる。State Storeは
  `(project, mr_iid, commit_sha)`というレビュー固有の複合キーのままにする。
- **JobRepository Protocolに`find_by_payload`のような汎用検索メソッドを追加する**:
  「決定」の理由の通り、Job抽象の設計方針(種別非依存の`dict`の箱)と相容れない上、
  M3-2/M3-3/M3-7が依存する安定済みProtocolへの変更は本Issueのスコープを超える。将来
  Job種別が増えて同様の需要が複数箇所で出た場合に改めて検討する。
- **Job投入を先に行い、成功したらIssue Ticket Storeに記録する(順序を逆にする)**:
  「find→enqueue→create」の順にすると、findからcreateまでの間に複数Pollerが競合した場合、
  片方の`create`が`DuplicateIssueTicketError`で失敗しても、その時点で両方が既に
  `job_repo.enqueue`を呼んでしまっているため、同一Issueに対して2つのJobが登録される
  (Job Queueに重複した`issue-analysis`エントリが残る)。「決定」の理由の通り、二重投入の方が
  投入漏れより運用上のリスクが大きいと判断し、この順序は採用しなかった。
- **Job投入失敗時にIssue Ticket Storeのレコードを削除してロールバックする**:
  実装は可能だが、ロールバック自体の失敗(削除操作がさらに失敗する)を考慮し始めると
  複雑さが増す。M4-1時点では「投入失敗はログに残し、運用者が手動で回復する」という
  MVPらしいシンプルな運用に留め、自動リトライ機構が必要になった場合は別Issueで検討する。

## 影響

- `src/gitlab_ai_platform/issue_store/`(新規パッケージ): `IssueTicketStore` Protocol、
  `SqliteIssueTicketStore`実装、`IssueTicketRecord`、`IssueTicketStoreError`/
  `DuplicateIssueTicketError`
- `src/gitlab_ai_platform/poller/issue_poller.py`(新規): `IssuePoller`、
  `ticket_issue_if_unprocessed`、`build_issue_analysis_job_payload`/
  `issue_analysis_job_payload_to_args`(payloadの組み立て・分解。`review/job.py`と同じ役割)
- `config`(M0-2)に`issue.label`(検出対象ラベル、既定`AI実装`)・`issue.ticket_db_path`
  (Issue Ticket Store用のSQLiteファイルパス、既定`issue_tickets.db`)を追加
- M4-2([#108](https://github.com/AtsushiNi/gitlab-ai-platform/issues/108))・M4-3
  ([#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109))は、
  `JobType.ISSUE_ANALYSIS`のJobHandler(Runner Dispatcherのディスパッチテーブルへの追加)を
  実装するだけで、本ADRで決めたPoller側の設計・payload形式(`{"project", "issue_iid"}`)を
  変更せずに着手できる見込み
- CLI(`cli/watch.py`)への結線(`IssuePoller.run`の呼び出し)は本Issueのスコープ外。
  MR Poller(M1-5)がCLI結線(M1-11)と別Issueだったのと同じ切り方。
