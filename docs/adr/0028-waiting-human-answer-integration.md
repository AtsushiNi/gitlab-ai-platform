# ADR-0028: `WAITING_HUMAN`後の回答取り込み・Job完了の設計

- Issue: [#111](https://github.com/AtsushiNi/gitlab-ai-platform/issues/111) (M4-5)
- 状態: 決定

## 背景・制約

- M4-3([#109](https://github.com/AtsushiNi/gitlab-ai-platform/issues/109)、
  [ADR-0026](0026-job-waiting-human-transition.md))で、`issue-analysis`Jobが
  `ASK`判定の不明点を持つ場合に`WAITING_HUMAN`へ遷移させ、質問一覧を
  `Job.result["questions"]`(各要素`{"question": str, "severity": "critical"}`)にそのまま
  記録する仕組みができた。ADR-0026は「回答を受け取った後にJobをどう再開するか」を明示的に
  M4-5のスコープとして残していた
- `WAITING_HUMAN`はリース(`claim`)対象外の状態のため(ADR-0026)、`RunnerDispatcher`
  (`worker`)ではなく`cli/single_run.py`の`execute_review_job`と同じ非リース方式
  (`JobRepository.update_status`)で扱う。`WAITING_HUMAN → RUNNING`/`WAITING_HUMAN → FAILED`は
  `job/protocol.py`の`_ALLOWED_TRANSITIONS`(ADR-0016)に既に定義済み
- 現時点でM4は`issue-analysis`(M4-3)までしか実装されておらず、次フェーズ(`design`、M4-6)は
  存在しない。そのため今回の「再開」は次フェーズへのハンドオフではなく、人間の回答を
  `issue-analysis`の結果に統合してJobを完了(`DONE`)させる、という単純な形に収まる
- 初期実装はローカルログ+CLIでの回答で足りる(Issue #111本文)。GitLab Issueコメント経由の
  提示・回答収集などのリッチな経路は、実際に必要になってから追加する

## 決定

### 新しいCLIサブコマンド`respond`を追加する

`cli/main.py`の既存パターン(合成ルート`run_xxx(config, ...)`を`cli/<name>.py`に置き、
`main.py`は引数パースと呼び出しのみ)に合わせ、`cli/respond.py`に実装する。

```text
gitlab-ai-platform respond [job_id]
```

- `job_id`省略時: `JobRepository.list_by_status(JobStatus.WAITING_HUMAN)`で対象Jobを一覧表示する
  だけで、回答収集・状態遷移は行わない(`git status`的な確認用途)。人間はここで表示された
  `job_id`を指定して再実行する
- `job_id`指定時: そのJobの`result["questions"]`を1件ずつ提示し、`input()`で回答を集めたうえで
  再開・完了させる(下記「回答収集→再開→完了の手順」)

複数のJobが同時に`WAITING_HUMAN`であっても、1回の`respond`呼び出しでは1件のJobだけを
処理する(全件を自動で回すと、質問文が長い/複数件のJobを人間が混同しやすくなるため)。

### 回答収集→再開→完了の手順(`respond_to_job`)

1. `job.result["questions"]`を1件ずつターミナルに表示し、`input()`(既定。テストでは注入した
   関数に差し替える)で回答を集める。**この段階では`JobRepository`の状態を一切変更しない**
2. 全質問への回答が集まってから`update_status(job_id, RUNNING)`を呼ぶ
   (`WAITING_HUMAN → RUNNING`、既存の許可済み遷移をそのまま使う)
3. `issue_analysis.job.build_resolved_issue_analysis_job_result(result, answers)`で、
   `questions`と回答を統合した新しい`result`を組み立てる(構造は次節)
4. `update_status(job_id, DONE, result=統合後のresult)`を呼び、Jobを完了させる

手順2〜4を`try`で囲み、例外(`KeyboardInterrupt`を含む)が発生した場合は
`update_status(job_id, FAILED, error=...)`を呼んでから元の例外を再送出する。手順1(質問提示・
回答収集)を状態変更なしの読み取り専用ステップとして独立させたのは、ここが最も時間のかかる
(人間が考える時間を含む)区間であり、Ctrl+C等で中断される可能性が最も高いためである。
手順1で中断されてもJobは`WAITING_HUMAN`のまま変化しないため、`respond`をそのまま再実行
すればよい。手順2(`RUNNING`遷移)以降で中断されると、`RUNNING`のまま放置すると再実行不能な
孤立Jobになってしまう(`WAITING_HUMAN`と異なり`RUNNING`には`respond`からの再入り口がない)ため、
`FAILED`に倒して人間が状況を把握できるようにする。

### 統合後の`result`構造(`build_resolved_issue_analysis_job_result`)

`questions`の各要素(`{"question", "severity"}`)を、対応する回答とともに`resolved_questions`
(`{"question", "severity", "answer"}`)へ変換して記録する。加えて、既存の
`assumed_uncertainties`(ASSUME判定の不明点、M4-9でMR本文の「○○と仮定して実装した」の元に
使う想定)と**同じ形の項目として**`resolved_questions`の内容を`assumed_uncertainties`へ
合流させる(`answer`は`assumed_uncertainties`の`assumption`キーに転記する)。

```python
{
    "project": str,
    "issue_iid": int,
    "requirements": list[str],
    "acceptance_criteria": list[str],
    "assumptions": list[str],
    "assumed_uncertainties": [
        # 元からのASSUME判定分 + 今回ASKから解決した分(合流後)
        {"question": str, "severity": "minor" | "critical", "assumption": str},
        ...,
    ],
    "questions": [],  # 常に空(解決済みのため)
    "resolved_questions": [
        # 監査用: 元は人間への質問(ASK)だったこと・実際の回答文言を保持
        {"question": str, "severity": "critical", "answer": str},
        ...,
    ],
}
```

理由: M4-9(push/MR作成)は「無人実行が置いた仮定・人間から得た回答」をまとめてMR本文に
記載する想定(ADR-0026)。ASSUME(AIが仮定して継続)とASK→回答(人間が明示的に回答)は
発生経緯こそ異なるが、M4-9からは「実装時に前提とした情報」という点で同じ扱いをしたい。
`assumed_uncertainties`に合流させることで、M4-9は1つのリストを読むだけで両方を拾える。
一方で「これは元々ASKだった(人間の判断を要した)」という経緯そのものも監査上の価値がある
ため、`resolved_questions`として別途保持し、情報を欠落させない。

### 中断安全性はテストで担保する

`respond_to_job`に、回答収集用の関数(`ask: Callable[[str], str]`)と`JobRepository`を
差し替え可能な形で注入し、次をテストする(`unittest.mock`は使わず手書きフェイク、
CLAUDE.mdのテスト方針):

- 正常系: `WAITING_HUMAN → RUNNING → DONE`の順に状態が遷移し、`DONE`の`result`が
  `resolved_questions`/合流後の`assumed_uncertainties`を含むこと
- 回答収集中(`ask`呼び出し中)に例外(`KeyboardInterrupt`含む)が送出された場合、
  `JobRepository`への状態変更が一切呼ばれず(`WAITING_HUMAN`のまま)、例外がそのまま
  再送出されること
- `RUNNING`遷移後(`build_resolved_issue_analysis_job_result`やその後の`update_status(DONE)`)
  で例外が送出された場合、`update_status(FAILED, error=...)`が呼ばれてから元の例外が
  再送出されること

## 却下した選択肢

- **GitLab Issueコメント経由で質問を提示し、コメント返信を回答として取り込む**: Issue #111
  本文が明示的に「初期実装はローカルログ+CLIで足りる」としている。コメントの取得・
  差分検知(どのコメントが「回答」かの判定)は非自明な設計を要し、実際に必要になってから
  (Issueコメント経由のリッチな提示が要求されてから)追加するほうが投資対効果が良い
- **`respond`が`WAITING_HUMAN`の全Jobを自動的に順番に処理する**: 質問文・プロジェクト・
  Issueが異なる複数Jobをまとめて対話すると、人間がどのJobに答えているか混同しやすい。
  1回の実行につき1件ずつ`job_id`を指定させるほうが安全で、`list_by_status`による一覧表示と
  組み合わせれば手間もさほど増えない
- **回答を`resolved_questions`だけに記録し、`assumed_uncertainties`へは合流させない**:
  「合流」を省くとADR自体は単純になるが、M4-9が「無人実行時に前提とした情報」を集めるときに
  `questions`跡地(空配列)と`resolved_questions`の2箇所を見に行く必要が生じる。
  `assumed_uncertainties`に合流させ、`resolved_questions`は経緯を残すための補助情報と
  位置付けるほうがM4-9からの参照が単純になる
- **`RUNNING`遷移前に`build_resolved_issue_analysis_job_result`まで済ませてから
  `update_status`を1回(`WAITING_HUMAN → DONE`)で完了させる**: `_ALLOWED_TRANSITIONS`
  (ADR-0016)は`WAITING_HUMAN → DONE`を許可していない。`RUNNING`を経由させないと
  `InvalidJobTransitionError`になる。仮に遷移表を変更してこれを許可しても、`complete`/`fail`が
  暗黙に前提とする「JobはRUNNINGを経てから終端状態になる」という状態機械の一貫性
  (ADR-0016)が崩れるため、既存の`WAITING_HUMAN → RUNNING → DONE`の2段階遷移をそのまま使う

## 影響

- `src/gitlab_ai_platform/issue_analysis/job.py`に`build_resolved_issue_analysis_job_result`を
  追加
- `src/gitlab_ai_platform/cli/respond.py`(新規)に`collect_answers`/`list_waiting_human_jobs`/
  `respond_to_job`/`run_respond`を追加
- `src/gitlab_ai_platform/cli/main.py`に`respond`サブコマンドを追加(`EXIT_JOB_ERROR`(18)を
  再利用、新しい終了コードは追加しない)
- `docs/specs/issue-analysis.md`に「`WAITING_HUMAN`後の再開」節を追記、
  `docs/specs/cli.md`に`respond`サブコマンドを追記
- M4-6(設計フェーズ、Job種別`design`)以降が同様に`WAITING_HUMAN`を使う場合も、
  「質問提示→回答収集→`RUNNING`→フェーズ固有の結果統合→`DONE`」という同じ形の再開処理を
  踏襲できる。ただし`resolved_questions`統合ロジック自体は`issue-analysis`のresult構造に
  依存するため、`design`/`implement`が独自の統合関数を持つ必要がある点は今後の課題として残る
