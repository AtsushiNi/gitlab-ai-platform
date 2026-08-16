# Windowsセットアップ手順

- ステータス: 完了
- 対応Issue: [#11](https://github.com/AtsushiNi/gitlab-ai-platform/issues/11) (D-7)

管理者権限なし・外部ダウンロード制限下のWindows環境で、M1完了時点(GitLab Adapter・
State Store・Workspace Manager・Claude Code Runner・Review・MR Poller・CLI単発実行)の
`gitlab-ai-platform`を動かすまでの手順。常駐(watch)モード(M1-11)はまだ実装されていないため、
ここでは`review`サブコマンドでの単発実行までを対象とする。

## 前提

- Windows 10 1809以降 / Windows 11(Claude Code CLIの最小要件)
- 管理者権限なし(ユーザー権限のみ)
- 社内ネットワークからの外部ダウンロードが制限されている前提。制限の程度は環境によって
  異なるため、各手順に「外部DL制限下の代替策」を添える
- 社内GitLabへの到達性とAI用GitLabアカウントは別途用意されている前提
  (未取得なら先にIT/GitLab管理者へ依頼する)

## 1. Python環境の構築

[ADR-0001](../adr/0001-repository-structure.md)の決定どおり、**Python 3.11以上**・
**pip + venv(標準ライブラリのみ、`uv`等の追加バイナリは使わない)**を使う。

1. Python 3.11以上を導入する。管理者権限が不要な入手方法:
   - [python.org](https://www.python.org/downloads/windows/)のインストーラーで
     「Install for me only(現在のユーザーのみ)」を選ぶ(管理者権限を要求されない)
   - 外部DL制限でpython.orgに到達できない場合は、社内ソフトウェア配布ポータルや
     Microsoft Store版Pythonなど、社内で許可されている経路を確認する
   - インストーラー自体を別のネットワーク環境で取得し、USB等でオフライン転送する方法もある
2. リポジトリのルート(このworktreeのパス)で仮想環境を作成する:

   ```powershell
   python -m venv venv
   ```

3. 仮想環境を有効化する:

   ```powershell
   venv\Scripts\Activate.ps1
   ```

   PowerShellの実行ポリシーで`Activate.ps1`の実行が拒否される場合(管理者権限なしでも
   ユーザースコープなら変更可能):

   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   ```

   それでも変更できない場合は`venv\Scripts\activate.bat`(コマンドプロンプト用)を使う。

4. 依存関係をインストールする。`pyproject.toml`の依存は`requests`(実行用)と
   `pytest`(開発用)のみ:

   ```powershell
   pip install -e ".[dev]"
   ```

   **外部DL制限下でPyPIに到達できない場合**: 別環境で
   `pip download -d wheels -r <requests/pytestのwheelを含む要件>`しておいたものを
   USB等で転送し、以下のようにオフラインインストールする:

   ```powershell
   pip install --no-index --find-links=wheels -e ".[dev]"
   ```

   社内PyPIミラーがある場合は`pip.ini`(`%APPDATA%\pip\pip.ini`)で`index-url`を
   ミラーに向ける方法でもよい。

## 2. GitLab Personal Access Token(PAT)の発行

参照: [spike-S2-gitlab-rest-api.md](../../references/spike-S2-gitlab-rest-api.md) §3。

1. 社内GitLabの `User Settings > Access Tokens` からPATを発行する。
2. スコープは用途に応じて選ぶ:
   - **MRの一覧・詳細・diff・コメント取得のみ(現時点のM1の範囲)**: `read_api` で足りる
   - 将来MR作成・コメント投稿など書き込み操作(M2以降)が必要になったら `api` スコープに
     切り替える。`api`は常に読み書き全体を含み、GitLab側のスコープだけでは
     「コメントは許可するがmergeは禁止」のような細かい制御はできない点に注意
     (書き込み操作の許可制御はAdapter層のコードで機構的に絞り込む設計になっている)
3. 発行したトークンの値はこの時にしか表示されない。安全な場所に控える
   (Gitに含めない、Slack等に平文で貼らない)。
4. 可能であればAI用GitLabアカウントのロールをMaintainer未満(Developer等)に
   留めておくと、Adapter層の制御をすり抜けた場合でもGitLab側でmergeが拒否される
   二重の防御になる。

トークン自体の運用ルール(禁止/許可操作、ローテーション等)は
[docs/operations/security.md](security.md)を参照(未着手の場合はスコープ外)。

## 3. Claude Code CLI と Amazon Bedrock認証の設定

参照: [spike-s1-claude-code-headless.md](../../references/spike-s1-claude-code-headless.md)。

### 3.1 Claude Code CLIの導入

Windowsでは、Git for Windowsが入っていれば内部的にGit Bash経由で、入っていなければ
PowerShell経由でコマンドを実行する(公式ドキュメント記載。この挙動自体はWindows実機で
未検証、[spike-s1](../../references/spike-s1-claude-code-headless.md) §6参照)。

導入方法(いずれも管理者権限不要):

- ネイティブインストーラー(推奨、Node.js不要):

  ```powershell
  irm https://claude.ai/install.ps1 | iex
  ```

- Node.jsが既に使える場合はnpm経由でも導入できる:

  ```powershell
  npm install -g @anthropic-ai/claude-code
  ```

**外部DL制限で`claude.ai`やnpmレジストリに到達できない場合**: IT部門に対象ドメインの
許可を依頼するか、到達可能な別環境でインストーラー/パッケージを取得してオフライン転送する。
社内ミラーがある場合はnpmの`registry`設定をミラーに向ける方法もある。

導入後、PowerShellを開き直してから `claude --version` で疎通を確認する。

### 3.2 Amazon Bedrock認証の設定

Claude Code は AWS SDK標準のクレデンシャルチェーンを使う。設定は以下の環境変数で行う:

```powershell
setx CLAUDE_CODE_USE_BEDROCK 1
setx AWS_REGION us-east-1
setx AWS_ACCESS_KEY_ID <発行されたアクセスキー>
setx AWS_SECRET_ACCESS_KEY <発行されたシークレットキー>
```

（`AWS_SESSION_TOKEN`が発行されている場合は同様に`setx`で設定する。SSOプロファイルを
使う場合は`AWS_PROFILE`を設定し、事前に`aws sso login`しておく。`setx`はユーザー環境変数への
書き込みで、管理者権限は不要。設定後は新しいターミナルを開き直さないと反映されない点に注意）

**重要: これらの環境変数は実際のOS環境変数として設定する必要がある。** 後述の`.env`ファイルは
`config/loader.py`の実装上、GitLab PAT(`GITLAB_AI_PLATFORM_GITLAB_TOKEN`)専用の読み込み口
であり、Claude Code Runnerがsubprocessとして`claude`を起動する際の環境は
Pythonプロセス自身の`os.environ`をそのまま引き継ぐ(`runner/subprocess_runner.py`)。
つまり`AWS_*`や`CLAUDE_CODE_USE_BEDROCK`を`.env`に書いても`claude`プロセスには渡らない。

追加で推奨する設定:

```powershell
setx ANTHROPIC_DEFAULT_SONNET_MODEL <固定したいモデルのバージョン付きID>
```

エイリアス(`sonnet`等)ではなくバージョン固定のモデルIDを使うことが公式に推奨されている
(エイリアスの既定は将来変わりうるため)。

**クレデンシャルチェーン解決の詰まりに注意**: 認証情報が正しく解決できない場合、
チェーン解決が最大60秒ブロックしうる([spike-s1](../../references/spike-s1-claude-code-headless.md)
§5.3で実測済み)。上記のように`AWS_ACCESS_KEY_ID`等を明示的に設定しておくことでこのリスクを
下げられる。`config.toml`の`runner.timeout_seconds`(既定1800秒)はこの詰まりを吸収できる
十分な長さを保つこと。

## 4. config.toml / .env の作成

設定の読み込みは`src/gitlab_ai_platform/config/loader.py`が担う。`config.toml`(リポジトリに
コミットされうる設定)と`.env`(シークレット、Gitに含めない)を分けている。

### .env

リポジトリルートに`.env`を作成し、GitLab PATを書く(キー名は
`config/loader.py`の`GITLAB_TOKEN_ENV_KEY`と一致させる必要がある):

```text
GITLAB_AI_PLATFORM_GITLAB_TOKEN=<発行したPAT>
```

`.env`から読み込まれるのはこのキーのみ(§3.2の注意点を参照)。`.env`が存在しない場合は
空として扱われ、実際の環境変数(`setx`等で設定済みのもの)で上書きされる
(`config/loader.py`の`_load_env`)。CI・本番実行では環境変数を、ローカル開発では`.env`を
使う想定。

### config.toml

リポジトリルートに`config.toml`を作成する。フィールドと既定値は
`config/loader.py`・`config/models.py`の実装に対応する:

```toml
[gitlab]
url = "https://gitlab.example.com"
projects = ["group/project-a", "group/project-b"]

[poller]
interval_seconds = 60      # 既定: 60
max_parallel = 5           # 既定: 5

[review]
label = "レビュー待ち"      # 既定: "レビュー待ち"

[workspace]
root = "workspace"                # 既定: "workspace"
max_disk_mb = 5000                # 既定: 5000

[runner]
log_dir = "logs/runner"           # 既定: "logs/runner"
timeout_seconds = 1800            # 既定: 1800(Bedrock認証の詰まりを吸収できる長さ)

[reviews]
root = "reviews"                  # 既定: "reviews"

[store]
db_path = "state.db"              # 既定: "state.db"
```

省略したセクション・キーは上記の既定値が使われる(`gitlab.url`・`gitlab.projects`・
GitLab PATのみ必須で、他はすべて省略可)。

**Windowsのパス指定について**: TOMLの文字列内で`\`はエスケープ文字になるため、
Windowsの絶対パスを書く場合は`[workspace]`セクションの`root`に
`root = "C:/Users/me/workspace"`のようにスラッシュを使うか、
`"C:\\Users\\me\\workspace"`のように`\\`でエスケープする。
本手順では相対パス(リポジトリルート基準)を使っており、この問題を避けられる。

## 5. 初回起動確認

仮想環境を有効化した状態(§1手順3)で、まずヘルプが表示できることを確認する:

```powershell
python -m gitlab_ai_platform.cli.main --help
```

`pip install -e .`済みなら`pyproject.toml`の`[project.scripts]`で登録された
コマンド名でも同じことができる:

```powershell
gitlab-ai-platform --help
```

`review`サブコマンド(単発レビュー実行、デバッグ・プロンプト改善用)のヘルプ:

```powershell
gitlab-ai-platform review --help
```

`config.toml`の`gitlab.projects`に含まれるプロジェクトの、実在するMRを1件指定して
実行してみる:

```powershell
gitlab-ai-platform review group/project-a 123
```

正常に完了すると、保存先パスと指摘件数のサマリが標準出力に表示される
(`cli/main.py`の`_print_summary`):

```text
レビュー完了: group/project-a !123 (<sha先頭12桁>)
  概要: <レビューの概要>
  指摘件数: critical=0 major=1 minor=2
  結果(Markdown): reviews/.../result.md
  結果(JSON): reviews/.../result.json
  実行ログ: logs/runner/.../....json
  worktree: workspace/...
```

失敗した場合は標準エラー出力にどの段階(GitLab Adapter/Workspace/Runner/Review/
State Store)で失敗したかが表示され、終了コードでも判別できる
(`cli/exit_codes.py`: 設定エラー=10, GitLab Adapter=11, Workspace=12, Runner=13,
Review=14, State Store=15, 中断=130)。詳細なトラブルシューティングは
[docs/operations/troubleshooting.md](troubleshooting.md)を参照(未着手の場合はスコープ外)。

## 関連ドキュメント

- [docs/adr/0001-repository-structure.md](../adr/0001-repository-structure.md) — Python環境構築方針の決定
- [docs/specs/cli.md](../specs/cli.md) — CLI(`review`サブコマンド)の仕様
- [docs/operations/configuration.md](configuration.md) — 設定リファレンス(未着手の場合はスコープ外)
- [references/spike-S2-gitlab-rest-api.md](../../references/spike-S2-gitlab-rest-api.md) — PATスコープの調査
- [references/spike-s1-claude-code-headless.md](../../references/spike-s1-claude-code-headless.md) — Claude Code/Bedrock認証の調査
