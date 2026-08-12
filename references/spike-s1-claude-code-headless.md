# S-1 Spike: Claude Code のヘッドレス実行方式の検証

- Issue: [#25](https://github.com/AtsushiNi/gitlab-ai-platform/issues/25)
- 検証日: 2026-08-12
- 検証環境: macOS (Darwin 25.1.0) / Claude Code CLI v2.1.216
- 検証方法: 実機での `claude -p` 実行(実際にコマンドを叩いて確認)+ 公式ドキュメント調査
- 目的: Runner (M1-7) の設計前提を固める

> 本ドキュメントは一次資料(実行結果の記録)であり、`docs/adr/` が整備されたら
> 設計判断部分(「結論」節)は ADR として昇格させることを想定する。D-4 未着手のため、
> 現時点では `references/` に置く。

---

## 結論サマリ(Runner 設計への示唆)

| 論点 | 結論 |
|------|------|
| 起動方法 | `claude -p "<prompt>"`(`--print`)。TTY 不要、標準出力にのみ結果が出る |
| 出力フォーマット | `--output-format json` で構造化結果を取得できる。`--json-schema` で任意スキーマに強制可能 |
| 成功判定 | **`is_error` と `permission_denials` の両方を見る。`result` の文言だけでは信用できない**(下記参照) |
| 権限設定 | `--permission-mode` `--allowedTools` `--disallowedTools` `--dangerously-skip-permissions` で制御。既定(フラグなし)は書き込み系を全拒否 |
| タイムアウト | CLI に `--timeout` は無い。**外部から `timeout`(Python は `subprocess.run(timeout=...)`)で必ず包む** |
| 異常終了時 | SIGTERM を受けても最終 JSON (`terminal_reason: aborted_*`) を出力してから終了。子プロセスの残留は確認されず |
| Bedrock 認証 | `CLAUDE_CODE_USE_BEDROCK=1` + AWS 標準クレデンシャルチェーンで動作。**チェーン解決が詰まると最大60秒ブロックしうる**(公式ドキュメントで明記) |
| Windows/Git Bash | 実機未検証(macOS環境のため)。公式ドキュメントでは Git for Windows 経由の起動、および Git for Windows 不在時は PowerShell 経由での起動をサポートと明記。**Runner を実際にWindowsで動かす前に実機確認が必要** |

---

## 1. 非対話実行の起動方法

```bash
claude -p "<prompt>" [options]
```

- `-p` / `--print`: 応答を出力して終了する。TTY chrome(対話UI)は表示されない。
- `-p` を使うか、stdout が TTY でない(パイプ/リダイレクト)場合、workspace trust ダイアログは自動的にスキップされる(公式ヘルプに明記)。
- `--input-format stream-json` で標準入力からのストリーミング入力にも対応(今回は未検証、M1-7 では通常の text 入力で足りる想定)。
- 実行例(実測):

```bash
$ claude -p "1+1 は何ですか?数字だけ答えて" --output-format json
{"type":"result","subtype":"success","is_error":false, ...,
 "result":"2","session_id":"637f6fd5-...","total_cost_usd":0.0629, ...}
$ echo $?
0
```

**Runnerへの示唆**: MRのコンテキスト(タイトル・説明・コメント・diff)は `prompt` 引数として渡すか、`--append-system-prompt` / システムプロンプトファイルとして渡す設計にできる。`--add-dir` で worktree 外のディレクトリへのアクセスを追加可能。

## 2. 出力フォーマット(構造化出力の可否)

`--output-format` は `text`(既定) / `json`(単一結果) / `stream-json`(逐次) の3種。

### 2.1 `json`

実行結果を1つのJSONオブジェクトとして得られる。主なフィールド:

- `is_error` (bool), `subtype`("success" / "error_during_execution" / ...)
- `result`(モデルの最終応答テキスト)
- `session_id`, `total_cost_usd`, `usage`(トークン数)
- `permission_denials`(配列。拒否されたツール呼び出しの記録)
- `terminal_reason`("completed" / "aborted_streaming" / "aborted_tools" / ...)
- `stop_reason`, `num_turns`

### 2.2 `--json-schema`

任意の JSON Schema を渡すと、`result` がそのスキーマに準拠したJSON文字列になる(構造化出力の強制)。実測:

```bash
$ claude -p "Extract: name=Taro, age=30" --output-format json \
  --json-schema '{"type":"object","properties":{"name":{"type":"string"},"age":{"type":"integer"}},"required":["name","age"]}'
# result: {"name":"Taro","age":30}
```

レビュー結果スキーマ(M1-9)を JSON Schema として定義し、これで強制すれば、後処理でのパース失敗リスクを減らせる。ただし今回のスパイクの範囲では簡易プロンプトでの検証に留まる。実際のレビュー結果のような複雑なスキーマ(指摘の配列、重要度 enum 等)での安定性は M1-9 実装時に個別検証が必要。

### 2.3 `stream-json`

逐次イベント(`system/init` → `assistant` → ... → `result`)がNDJSON形式で流れる。実測では `2+2は?` という単純な問いで4行(init, assistant, ..., result)。長時間実行時の進捗把握やタイムアウト監視(最後のイベント受信からの経過時間で「ハング」を検知する等)に使える。

### 2.4 ⚠️ 重要な注意: `result` テキストと実際の成否が食い違うことがある

検証中、権限がない状態で書き込みを指示したところ、1回目は以下のように**実際にはファイルが作成されていないにもかかわらず、成功したかのような文章**が `result` に返った:

```
result: "Created `test_a.txt` with the content \"hello\" in the current working directory."
```

(実際には `test_a.txt` は存在せず、`permission_denials` に `Write` の拒否記録があった)

2回目の同種の試行では逆に「権限が無いので書けなかった」と正直に報告するテキストが返った。つまり **`result` の自然文だけをパースして成否判定するのは危険**。`is_error` と `permission_denials` を必ず確認し、可能なら副作用(ファイルの存在、git diff の有無)も突き合わせる設計にすべき。

**Runnerへの示唆**: レビュー結果の保存(M1-9)は「Claude Codeが書いたと自己申告したファイル」を信用せず、実行後に Runner 側で成果物ファイルの存在・内容を検証する。

## 3. 権限設定

| フラグ | 挙動(実測) |
|--------|-------------|
| (指定なし) | 書き込み系ツール(`Write` 等)は**拒否**され `permission_denials` に記録。プロセス自体は `is_error:false, exit 0` で正常終了する(=拒否はエラー扱いではない) |
| `--permission-mode acceptEdits` | ファイル編集系を自動許可。実測でファイル作成成功 |
| `--allowedTools "Write"` | 指定ツールのみ明示許可。実測でファイル作成成功、`permission_denials` は空 |
| `--disallowedTools "Bash"` | 指定ツールを機構的に利用不可にする。実測では「Bashツールが手元にない」と自己申告し、そもそも呼び出しを試行しない(denial すら発生しない = ツール一覧から消える) |
| `--dangerously-skip-permissions` | 全許可。実測でファイル作成・削除(`rm`)まで成功。**サンドボックス外では使うべきでない**と公式ヘルプにも明記 |

**Runnerへの示唆**:
- MVP のレビュー実行は「diffを読んでコメントを生成するだけ」で worktree への書き込みは不要なはずなので、既定(フラグなし)または `--allowedTools "Read Grep Glob Bash(git diff*)"` 程度の最小権限で足りる可能性が高い。
- 将来 M4(Issue駆動実装)で `Write`/`Bash(git commit*)` 等を許可する場合も、**禁止したい操作(merge, protected branch push, branch削除)は Claude Code の権限フラグではなく GitLab Adapter 側の許可リスト機構(M1-3)で機構的に止める**方針が引き続き妥当(Claude Code 側の権限はツール単位の粗い制御であり、「git pushは許可するが特定ブランチへは禁止」のような粒度は表現できない)。

## 4. タイムアウト

- CLI 自体に `--timeout` オプションは存在しない(`--help` で確認済み)。
- 検証: `timeout 5 claude -p "...Bashでsleep 8..." --allowedTools Bash` → 5秒でexit code `124`(外部timeoutによるkill)。この時、SIGTERM 受信後も Claude Code は最終結果 JSON(`terminal_reason: "aborted_tools"`, `is_error:true`)を出力してから終了していた。
- kill後に子プロセス(sleep等)が残留していないことを `ps` で確認。プロセスツリーのクリーンアップは機能している。

**Runnerへの示唆**: Runner 実装では Python の `subprocess.run(..., timeout=N)` (または `Popen` + `communicate(timeout=N)`)で必ず外側からタイムアウトを掛ける。Claude Code 内部にタイムアウト機構がない前提で設計する。stream-json 出力を使う場合は「最後のイベント受信からのアイドル時間」でのタイムアウト検知も選択肢になる。

## 5. Bedrock 認証の引き回し

公式ドキュメント([Claude Code on Amazon Bedrock](https://code.claude.com/docs/en/amazon-bedrock))と実機での挙動確認結果:

### 5.1 有効化

```bash
export CLAUDE_CODE_USE_BEDROCK=1
export AWS_REGION=us-east-1   # プロファイルにregionがあれば省略可
```

### 5.2 AWS認証情報の解決順序

Claude Code は AWS SDK 標準のクレデンシャルチェーンを使う(独自実装ではない)。主な選択肢:

- `aws configure` (共有クレデンシャルファイル)
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN`
- `AWS_PROFILE`(SSOプロファイル、`aws sso login` 併用)
- `AWS_BEARER_TOKEN_BEDROCK`(Bedrock APIキー。フルAWS認証情報が不要な簡易方式)

### 5.3 ⚠️ 解決の詰まりとタイムアウト(実機確認済み)

クレデンシャル情報を一切与えずに(`env -i` でクリーンな環境から)`CLAUDE_CODE_USE_BEDROCK=1` を設定して実行したところ、**20秒の外部タイムアウトを使い切っても応答が返らなかった**(chain解決がハング)。
一方、明示的に(無効な)静的キーを与えた場合は約15.6秒で `error_during_execution` として失敗した。

公式ドキュメントにも「チェーン解決は毎回最大60秒でタイムアウトし、`credential_process` 等の1ステップがスタールすると `AWS default-chain credential resolve timed out` になる。v2.1.207以前はこの詰まりが**無期限にブロック**していた」と明記されている。今回の実機観測(20秒でも終わらない)はこの挙動と整合する。

- 解決結果は解決のたびにキャッシュされ、以後は期限切れ5分前まで再利用される(v2.1.207以降)。
- チェーン解決のタイムアウト値は `CLAUDE_CODE_AWS_CHAIN_RESOLVE_TIMEOUT_MS` で調整可能。
- 認証情報の取得に毎回チェーンを再解決させたい場合は `CLAUDE_CODE_SKIP_AWS_CRED_CACHE=1`。

**Runnerへの示唆**:
- Runner 用の AWS 認証情報は**チェーン解決に頼らず、明示的な環境変数(`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN` か `AWS_PROFILE` を確実に設定した状態)** で渡すことで、詰まりのリスクを下げる。
- それでも Bedrock 認証系の詰まりが最大60秒(または `CLAUDE_CODE_AWS_CHAIN_RESOLVE_TIMEOUT_MS`)発生しうる前提で、**外部タイムアウト(§4)は60秒より十分長く設定する**必要がある(でないと「認証が詰まっただけ」を「レビューがタイムアウトした」と誤判定する)。
- モデルはエイリアス(`sonnet`等)ではなく `ANTHROPIC_DEFAULT_SONNET_MODEL` 等でバージョン固定すべき(公式推奨。エイリアスは既定が動くたびに変わりうる)。

## 6. Windows / Git Bash での起動可否

**本検証は macOS 上で実施しており、Windows実機では未検証。** 公式ドキュメント調査による情報:

- Claude Code は Windows ネイティブで動作する(WSLは必須ではなく任意)。
- **Git for Windows があれば、内部的に Git Bash 経由でコマンドを実行する**(PowerShellやCMDから起動した場合でも)。
- Git for Windows が入っていない環境では、PowerShell をシェルツールとして使う形にフォールバックする。
- 最小要件: Windows 10 1809以降 または Windows Server 2019以降、RAM 4GB以上、x64/ARM64。
- WSL2 はサンドボックス化されたコマンド実行やLinux依存ツールが必要な場合にのみ必要(必須ではない)。

**未検証・要フォローアップ**:
- `references/タスク整理.md` の前提「Windows・管理者権限なし・外部DL制限あり」という制約下で、Git for Windows のセットアップ自体が可能か(既に入っているか、管理者権限なしでインストールできるか)。
- `-p` / `--output-format json` を実際に Git Bash / PowerShell から実行し、本ドキュメントの§1〜4と同じ結果になるか(改行コードやシェルのクォーティング差異による影響がないか)。
- タイムアウトの外部実装(§4)は Windows 標準ツールに `timeout`(GNU版)が無いため、**Python の `subprocess.run(timeout=...)` を使う設計であれば OS 差異を吸収できる**(実装言語がPython確定である前提と整合)。Git Bash の `timeout` コマンド依存は避けるべき。
- → 実機(Windows)での再検証を別タスクとして積む(このIssueの残課題としてクローズ前にコメントするか、フォローアップIssueを起票する)。

## 7. 検証に使ったコマンド(再現用)

```bash
# 基本のJSON出力
claude -p "1+1 は何ですか?数字だけ答えて" --output-format json

# 権限なしでの書き込み試行(拒否されるか)
claude -p "write the text 'hello' to a file named test.txt" --output-format json

# 明示許可
claude -p "..." --output-format json --allowedTools "Write"
claude -p "..." --output-format json --permission-mode acceptEdits
claude -p "..." --output-format json --dangerously-skip-permissions

# 禁止ツール
claude -p "..." --output-format json --disallowedTools "Bash"

# 構造化出力(JSON Schema)
claude -p "Extract: name=Taro, age=30" --output-format json \
  --json-schema '{"type":"object","properties":{"name":{"type":"string"},"age":{"type":"integer"}},"required":["name","age"]}'

# タイムアウト(外部 timeout コマンドで包む)
timeout 5 claude -p "..." --output-format json --allowedTools "Bash"

# Bedrock 有効化(認証情報なしでの詰まり確認)
env -i HOME="$HOME" PATH="$PATH" CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-east-1 \
  claude -p "1+1?" --output-format json
```

## 8. 参考資料

- [Claude Code on Amazon Bedrock (公式)](https://code.claude.com/docs/en/amazon-bedrock)
- [Claude Code Advanced Setup (公式)](https://code.claude.com/docs/en/setup)
- `claude --help` の出力(v2.1.216)
