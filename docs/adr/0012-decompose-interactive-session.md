# ADR-0012: 要件→Issue分解ワークフロー(`decompose`)の対話型セッション設計

- Issue: [#48](https://github.com/AtsushiNi/gitlab-ai-platform/issues/48) (M2-11)
- 状態: 決定

## 背景・制約

### 何を解決するか

`docs/requirements.md` 3-C「新規の開発要件をIssueへ分解する(Windows・対話型)」。何か新しい
開発要件が出てきたとき、それを個々のGitLab Issueへ分解・起票する作業をAIに担わせたい。
このリポジトリ自身が `references/タスク整理.md` を人手でGitHub Issueへ分解してきた作業と
同種のものを、対象プロジェクトに対してAI支援で行えるようにする。

### B(M4のIssue駆動パイプライン)との違い

`docs/requirements.md` 3-Bは「既にあるIssue」を起点に要求分析・設計・実装・MR作成まで進める、
主に無人実行(M3以降Linux/Docker)のパイプラインである。本Issue(C)はその**前段**、すなわち
Issueがまだ存在しない状態から始まる。要件の大きさ・優先度・依存関係の切り方には人間の判断が
本質的に必要であり、`docs/architecture.md`の設計原則(人間が介在する処理はWindows、無人で回す
AI処理はLinux/Docker)に従い、Windows端末上でCLIを介した**対話型**の機能として提供する
(M4のLinux/Docker無人トラックには含めない)。

つまりB/M4のheadless実行(`claude -p ... --output-format json`、`runner/subprocess_runner.py`)
とは要求そのものが異なり、人間がターミナルでそのままClaude Codeと対話できる必要がある。

### GitLab Adapter MCP Server(M2-12)が既に前提基盤を提供している

[ADR-0010](0010-gitlab-mcp-tool-bridge.md)で、対話型Claude Codeが実行中にエージェント自身の
判断でGitLab操作(`create_issue`含む)をツールとして呼び出せるMCPサーバー
(`python -m gitlab_ai_platform.adapter_mcp_server`)を既に用意した。ADR-0010自体が
「M2-4(追加調査モード)・M2-11(本Issue)が前提とする基盤」と明記しており、本ADRは
このMCPサーバーをどう対話型セッションに組み込むかを決める。

### 認証情報を対話型プロセスの引数に残してはならない

`adapter_mcp_server`自体は`config.load_config`(`--config`/`--env`)経由でGitLab PATを取得する
契約が既に確立している([docs/specs/adapter-mcp-server.md](../specs/adapter-mcp-server.md))。
`decompose`側はこの契約を壊さず、`--config`/`--env`のパスをそのまま引き継ぐだけにとどめ、
トークンの値そのものをコマンドライン引数・ログに一切書かないようにする必要がある。

## 決定

### `claude`を`-p`なしでサブプロセス起動し、stdin/stdout/stderrを継承する

`src/gitlab_ai_platform/cli/decompose.py`の`run_decompose`が、`subprocess.Popen(command)`を
`stdout`/`stderr`を`PIPE`に繋がずに(=継承させて)起動する。これにより人間がそのまま
ターミナルでClaude Codeと対話できる。`runner/subprocess_runner.py`(`-p --output-format json`、
`stdout=PIPE`でJSONをパースする)とは対照的に、本コマンドは`-p`を一切付けず、
構造化された`RunResult`相当のものも持たない。`claude`プロセスが終了した時点の終了コードを
そのままCLIの終了コードとして返す。

### `--mcp-config`にGitLab Adapter MCP Serverの起動コマンドをJSON文字列で渡す

`build_mcp_config(config_path, env_path, ...)`が次の形の辞書を組み立て、`json.dumps`した文字列を
`--mcp-config`引数として渡す(JSONファイルへの書き出しはしない。「却下した選択肢」参照)。

```json
{
  "mcpServers": {
    "gitlab-adapter": {
      "command": "<sys.executable>",
      "args": [
        "-m", "gitlab_ai_platform.adapter_mcp_server",
        "--config", "<config_path>",
        "--env", "<env_path>"
      ]
    }
  }
}
```

- `command`には`sys.executable`(`decompose`自身を実行しているPython)を既定値として使う。
  PATH上の`python`/`python3`の存在に依存せず、`decompose`と同じ仮想環境の
  `adapter_mcp_server`を確実に起動できるようにするため。
- `--config`/`--env`は`decompose`が呼び出し元(`cli.main`)から受け取った値をそのまま渡す。
  `decompose`自身は`Config`(パース済みの値、`gitlab_token`含む)を扱わず、ファイルパスの
  引き継ぎに徹する。
- あわせて`--strict-mcp-config`を付与し、ユーザー環境の他のMCP設定
  (グローバル設定・起動時cwdの`.mcp.json`等)が意図せず有効化されないようにする。
  「起票時は必ず`create_issue`ツールを使う」という前提を、同名ツールを持つ別のMCPサーバーとの
  衝突可能性ごと排除して安定させる。

### システムプロンプト(`--append-system-prompt`)で「人間の判断を仰ぐ」ことを明示する

`build_system_prompt(project)`が、セッション全体に効く追加システムプロンプトとして次を含む
文字列を返す(`--append-system-prompt`で渡す。デフォルトのシステムプロンプトを丸ごと
置き換える`--system-prompt`は使わない。CLAUDE.md読み込み等の既存の振る舞いを壊したくないため):

- 対象プロジェクトが`project`であることの明示
- 要件の粒度・優先度・依存関係の切り方には人間の判断が本質的に必要であり、勝手に断定しないこと
- Issueを実際に起票する前に、分解案を一覧で提示し人間の承認を得ること
- `create_issue`/`update_issue`使用時は`project`引数を必ず明示すること(MCPサーバーの
  デフォルトプロジェクト自動解決に頼らない。誤操作で別プロジェクトへ起票することを防ぐ)

初期メッセージ(`build_initial_prompt`)は`-p`を付けない位置引数として渡し、対話セッション開始時に
最初のユーザーメッセージとして自動送信される。こちらは1ターン目の呼び水(「要件の概要を教えて」)
に徹し、繰り返し効かせたいルールはシステムプロンプト側に置く、という役割分担にした。

### GitLab認証は呼び出し元の`config.load_config`検証結果に乗るだけにする

`cli.main`は`review`/`watch`と同様、サブコマンド分岐前に`load_config(config_path=args.config,
env_path=args.env)`を1回呼び、`ConfigError`をそのまま`EXIT_CONFIG_ERROR`(10)へ変換する。
`decompose`はこの検証を再利用し、`run_decompose`自体は`Config`オブジェクトを受け取らず、
検証済みの`--config`/`--env`パス(`args.config`/`args.env`)だけを引数に取る。これにより

- `decompose`のコードパス自体がGitLab PATの値を一度も変数として保持しない
- `adapter_mcp_server`側の認証・ログ出力契約([ADR-0010](0010-gitlab-mcp-tool-bridge.md)
  「セキュリティ上の考慮」)にそのまま乗っかれる

### 終了コードは「起動前後の失敗」のみを独自にハンドリングする

対話型セッションには構造化された成否判定が存在しない(人間が直接対話し、いつでも`/exit`や
Ctrl+Dで終了できる)。そのため`decompose`固有のエラーハンドリングは次の2つに絞る。

1. `claude`コマンド自体が見つからない(`FileNotFoundError`) →
   `decompose.ClaudeCommandNotFoundError`を送出し、`cli.main`が新設の
   `exit_codes.EXIT_CLAUDE_NOT_FOUND`(17)に変換する
2. 設定エラー(`ConfigError`) → 既存の`EXIT_CONFIG_ERROR`(10)経路(サブコマンド分岐前)

上記以外(対話セッション中にユーザーが`/exit`する、Ctrl+Cで抜ける等)は`claude`プロセス自身の
終了コードをそのままCLIの終了コードとして返し、`review`/`watch`のようなパイプライン段階別の
終了コード変換は行わない(そもそも「どの段階で失敗したか」という概念が対話型セッションには
存在しないため)。

## 却下した選択肢

- **`--mcp-config`にJSONファイルのパスを渡す(一時ファイルに書き出す)**: `--mcp-config`は
  「JSON files or strings」の両方を受け付ける。一時ファイル方式は書き出し先・クリーンアップ
  タイミングの管理が余分に必要になる一方、JSON文字列自体にGitLab PAT等の値そのものが
  含まれることは元々なく(`--config`/`--env`という**パス**しか含まない)、一時ファイル化で
  セキュリティ上得られるものがない。文字列方式のほうがシンプルなためこちらを採用した。
- **`decompose`が`Config`を直接受け取り、`gitlab_token`等を使って何かする**:
  `adapter_mcp_server`が既にconfig層を再利用する契約を持っているため、`decompose`が
  トークンの値を扱う理由がない。トークンを扱うコードパスを増やさないことを優先し、
  `decompose`はファイルパスの引き継ぎに徹する設計にした。
- **`--system-prompt`でシステムプロンプト全体を置き換える**: デフォルトのシステムプロンプトが
  提供する既存の振る舞い(CLAUDE.md読み込み等)を保ったまま、Issue分解に必要な追加ルールだけを
  載せたかったため、全置換ではなく追記(`--append-system-prompt`)を選んだ。
- **`--permission-mode`を固定値(例: `plan`)に決め打ちする**: `review`サブコマンドが
  `--permission-mode`をオプションとして公開しているのと揃え、`decompose`でも同様にオプション化
  した。既定値は指定せず(`claude`自身のデフォルト挙動に委ねる)、対話型なので人間がその場で
  権限を確認しながら進められることを優先した。
- **`decompose`独自の軽量Config読み込みを新設し、`review`/`watch`が要求する全フィールド
  (`workspace.root`等、`decompose`自体は使わない値)の入力を不要にする**: `cli.main`が
  サブコマンド分岐前に一括で`load_config`する既存の構造(`review`/`watch`と共通)を崩さず
  一貫性を優先した。`decompose`専用に不要な設定を求めることになるが、影響は「config.tomlに
  一通り値を書く必要がある」程度であり、MVPでは許容範囲と判断した。将来的に負担が大きければ、
  サブコマンドごとに必要なフィールドだけを検証する軽量版の導入を別途検討する。

## 影響

- `src/gitlab_ai_platform/cli/decompose.py`を新設(`build_mcp_config`/`build_system_prompt`/
  `build_initial_prompt`/`build_claude_command`/`run_decompose`、`ClaudeCommandNotFoundError`)。
- `src/gitlab_ai_platform/cli/main.py`に`decompose`サブコマンドを追加
  (`project`位置引数、`--permission-mode`オプション)。
- `src/gitlab_ai_platform/cli/exit_codes.py`に`EXIT_CLAUDE_NOT_FOUND`(17)を追加。
- `docs/specs/cli.md`に`decompose`サブコマンドの仕様を追記。
- 新しい外部依存の追加はない(`json`/`subprocess`/`sys`はいずれも標準ライブラリ)。
- 今後M2-4(追加調査モード)が同様の対話型セッションを必要とする場合、`build_mcp_config`等の
  組み立てロジックの一部(GitLab Adapter MCP Serverの登録)を再利用できる見込み。
