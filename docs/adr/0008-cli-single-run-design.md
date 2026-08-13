# ADR-0008: CLI 単発レビュー実行の設計

- Issue: [#38](https://github.com/AtsushiNi/gitlab-ai-platform/issues/38) (M1-10)
- 状態: 決定

## 背景・制約

- `docs/architecture.md`により、CLIの責務は「単発レビュー実行(デバッグ・プロンプト改善用)と、
  常駐(watch)モードの入口」。「オーケストレーション(Job間の遷移)はしない」ことが境界。
- M1-1〜M1-9でGitLab Adapter・State Store・Workspace Manager・Claude Code Runner・Review
  (プロンプト設計・結果スキーマ)が実装済みだが、まだ一度も組み合わせて実行されていない。
  M1-10はこれらを初めてエンドツーエンドで結線するタスクであり、各モジュールの
  Protocol/型が実際に噛み合うかを確認する意味も持つ。
- `Config`(`config/models.py`)には`gitlab_url`/`gitlab_token`/`projects`/
  `poll_interval_seconds`/`max_parallel`/`review_label`しかなく、Workspace Managerの
  worktree root・ディスク上限、Claude Code Runnerのログ出力先・タイムアウト、Reviewの
  保存先root、State StoreのDBパスが未定義だった。

## 決定

### `Config`を拡張し、パイプライン全体の設定を`config.toml`の一箇所に集約する

`workspace_root` / `workspace_max_disk_mb` / `runner_log_dir` / `runner_timeout_seconds` /
`reviews_root` / `state_db_path`を`Config`に追加した(`config.toml`の`[workspace]` /
`[runner]` / `[reviews]` / `[store]`セクションに対応)。CLI固有の設定ファイルを別に
作らず、`config/`(M0-2)を「GitLab PAT・対象プロジェクト一覧・ポーリング間隔・並列数などの
設定/シークレット管理」を担う唯一の設定源として一貫させる方針を踏襲した。M1-11(watch
モード)も同じ`Config`を再利用できる。値はすべて既存フィールドと同じ`from_raw`のバリデーション
パターン(空文字列/非正数を`ConfigError`にまとめて集約)に従う。

### パイプライン本体(`execute_review`)と合成ルート(`run_single_review`)を分離する

`cli/single_run.py`に2つの関数を用意した:

- `execute_review(adapter, workspace, runner, store, config, project, mr_iid, ...)`:
  GitLab Adapter・Workspace Manager・Claude Code Runner・State StoreをすべてProtocol型
  (`GitLabReader` / `WorkspaceManager` / `ClaudeCodeRunner` / `StateStore`)の引数として
  受け取るパイプライン本体。`MrPoller.__init__`(ADR-0007)が`GitLabReader`/`StateStore`を
  引数で受け取るのと同じ考え方で、具象実装(REST/git/subprocess/SQLite)に依存しない。
  テストは手書きフェイクを注入して行い、実サービス(実GitLab・実git・実Claude Code
  subprocess)には一切繋がない(CLAUDE.mdのテスト方針)。
- `run_single_review(config, project, mr_iid, ...)`: `execute_review`が必要とする4つの
  具象実装を`config`から組み立てる合成ルート。CLI(`cli/main.py`)はこちらを呼ぶ。

この分離により、パイプラインのロジック(段階の順序・エラー時のState Store更新)を、
実際にgitやClaude Code CLIをsubprocess起動せずに検証できる。

### State Storeは実行開始時に`RUNNING`で起票し、単発実行では同一commitの再実行を許容する

`(project, mr_iid, sha)`が未記録なら`create(status=RUNNING)`、既存なら
(`DuplicateReviewError`を捕まえて)`update_status(RUNNING)`で上書きする。MR Poller
(ADR-0007)が「既存レコードがあれば無視する」のとは異なり、単発実行は「デバッグ・
プロンプト改善用」(`docs/architecture.md`)という目的上、同一commitに対してプロンプトを
調整しながら繰り返し実行することが主な使い方になる。既存レコードを無視すると2回目以降の
実行が起票されず状態が更新されないため、単発実行では意図的に上書きを許容した。

Workspace Manager以降(`prepare`〜`save_review`)のいずれかで例外が発生した場合は、
`update_status(FAILED)`してから元の例外をそのまま再送出する。GitLab Adapter呼び出し
(`get_merge_request`等)の失敗はまだ起票前のため、State Storeには触れず素通しする。

### GitLab Adapterより前段の失敗も含め、各段階の例外は変換せずそのまま伝播させる

`execute_review`/`run_single_review`は独自の例外型を持たない。`GitLabAdapterError` /
`WorkspaceError` / `RunnerError` / `ReviewError` / `StateStoreError`をそのまま呼び出し側
(`cli/main.py`)へ伝播させ、`main`側でexcept節ごとに終了コード・エラーメッセージへ変換する
(下記)。パイプライン層とCLI表示層の責務を分離し、`execute_review`は将来M1-11(watch
モード)や他の呼び出し元からも同じ例外契約で再利用できる。

### 終了コードは10番台をパイプラインの段階ごとに割り当てる

`cli/exit_codes.py`:

| コード | 意味 |
|---|---|
| 0 | 成功 |
| 1 | 想定外の例外(上記いずれの型にも属さない) |
| 2 | (予約、`argparse`の引数エラーが自動的に使う) |
| 10 | `ConfigError` |
| 11 | `GitLabAdapterError` |
| 12 | `WorkspaceError` |
| 13 | `RunnerError` |
| 14 | `ReviewError`(結果パース失敗) |
| 15 | `StateStoreError` |
| 130 | `KeyboardInterrupt`(慣例通りSIGINTは128+2) |

「デバッグとプロンプト改善の主要導線」という目的上、失敗した段階を終了コードだけで
判別できることを優先した。`argparse`が引数エラーで使う`2`と衝突しないよう10番台から
割り当てている。

### GitLab認証はcredential helper経由でPATを都度供給し、`.git/config`やコマンド引数に残さない

Workspace Manager(ADR-0004)は`clone_url_for`(project名→clone URL)と`git_config`
(`-c key=value`として全gitコマンドに注入)を呼び出し側からの注入に委ねている。CLIは:

- `clone_url_for`: `f"{gitlab_url}/{project}.git"`(トークンをURLに埋め込まない。埋め込むと
  bare repoの`.git/config`の`remote.origin.url`に平文で残ってしまう、
  `references/spike-S3-git-worktree-windows.md` §8.1)
- `git_config={"credential.helper": "!f() { echo username=oauth2; echo \"password=$<ENV_VAR>\"; }; f"}`:
  Spike S-3 §8.1で検証済みの「環境変数からPATを都度供給するcredential helper」パターンを
  そのまま採用。`<ENV_VAR>`は`config.GITLAB_TOKEN_ENV_KEY`の**名前**のみで、値(トークン本体)は
  この文字列に含まれない(コマンド引数・エラーログに残らない)
- 実際のトークン値は、Workspace Managerのコンストラクタ引数`run`(subprocess実行用の
  差し替え可能なcallable)を`functools.partial(subprocess.run, env={**os.environ,
  GITLAB_TOKEN_ENV_KEY: config.gitlab_token})`にラップすることで、Workspace Managerが
  起動するgitプロセスの環境変数としてのみ注入する。credential helperのシェルコマンドが
  この環境変数を読み取って`username=`/`password=`を返す

### `runner.subprocess_runner._build_prompt`を`build_prompt`として公開する

`review.storage.save_review`の`input_prompt`は「Runnerに渡した完成後のプロンプト全文」を
保存する契約(`docs/specs/review-output.md`)。この文字列はinstructions(観点)とMRの
タイトル・説明・コメント・diffを結合したもので、結合ロジック自体はRunner
(`SubprocessClaudeCodeRunner._build_prompt`)にしかなかった。CLIがこれを再現する際に
同じロジックを重複実装しないよう、`_build_prompt`を`build_prompt`として`runner`パッケージの
公開インターフェースに昇格させた(`run`の内部実装は変更なし、可視性のみの変更)。

## 却下した選択肢

- **CLI専用の設定ファイル(`cli.toml`等)を新設する**: 設定源が分散し、M0-2が既に
  「GitLab PAT・対象プロジェクト一覧・ポーリング間隔・並列数などの設定/シークレット管理」
  として`config/`に集約する方針を立てているため、既存`Config`の拡張で一貫させた。
- **`execute_review`を作らず`run_single_review`に具象実装の構築とパイプラインロジックを
  混在させる**: MR Poller(ADR-0007)が確立したパターン(具象実装への依存をコンストラクタ/
  関数引数の外側に押し出し、Protocol型だけを見て実装する)から外れる。パイプラインの
  分岐・エラー処理を実サービス無しでテストできなくなるため不採用。
- **PATをclone URLに直接埋め込む(`https://oauth2:<token>@host/...`)**: Spike S-3
  §8.1が明示的に避けるべきとした方式(`.git/config`に平文で残る)。credential helper方式を
  採用した。
- **`--dangerously-skip-permissions`相当のフラグをCLIに追加する**: Claude Code Runner
  (ADR-0005)がこのフラグをインターフェースに一切公開しない方針を貫いているため、CLIも
  追加しない。`--permission-mode`/`--allowed-tools`/`--disallowed-tools`のみを公開する。
- **単発実行でも既存レコードがあれば起票をスキップする(MR Pollerと同じ挙動)**: 「デバッグ・
  プロンプト改善用」という目的(`docs/architecture.md`)上、同一commitへの繰り返し実行が
  主要なユースケースであり、スキップすると2回目以降の実行結果がState Storeに反映されない。
  単発実行とMR Pollerとで意図的に異なる挙動にした。
- **`RUNNING`→`FAILED`/`DONE`の状態遷移をやめ、成功時のみレコードを作成する**: 実行中に
  クラッシュした場合に「起票されたまま何も分からない」レコードが残らず追跡できなくなる。
  実行開始時点で`RUNNING`を記録することで、異常終了したレビューも`find`で発見できるようにした。

## 影響

- M1-11(watchモード)は同じ`Config`(拡張後)・同じ`execute_review`をMR Pollerの
  `PollResult.created`から呼び出す形で再利用できる想定。合成ルート(`run_single_review`)は
  単発実行専用のままでよいか、watchモード用に複数MRをループする合成ルートを別途追加するかは
  M1-11で判断する。
- `docs/operations/configuration.md`(D-8、未着手)着手時は、`config.toml`の
  `[workspace]` / `[runner]` / `[reviews]` / `[store]`セクションもあわせて記載する。
- `runner.build_prompt`は今後Review側(または将来のJob層)がRunnerに渡した実際のプロンプトを
  再現する必要がある場面(ログ調査、再実行等)全般で再利用できる。
