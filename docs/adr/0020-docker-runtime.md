# ADR-0020: Linux/Docker 実行環境の構築

- Issue: [#94](https://github.com/AtsushiNi/gitlab-ai-platform/issues/94) (M3-4)
- 状態: 決定

## 背景・制約

- [`docs/architecture.md`](../architecture.md)「Windows/Linuxの分担」の設計方針:
  **人間が介在する処理はWindows、無人で回すAI処理はLinux/Docker**。M3以降、無人実行が前提になる
  フェーズ(Job Queue・Orchestrator・複数Runnerの並列稼働)ではDocker上のRunnerが必要になる
- 共通コード([ADR-0001](0001-repository-structure.md)の`src/`レイアウト)は両環境で動くことを
  制約として維持する。GitLab Adapter・Workspace Manager・Review pipelineのロジック自体は
  変えず、実行環境(コンテナの有無)だけを追加する
- Claude Code CLIのヘッドレス実行そのものの挙動(起動方法・出力フォーマット・タイムアウト・
  Bedrock認証)は[references/spike-s1-claude-code-headless.md](../../references/spike-s1-claude-code-headless.md)
  で検証済み。本ADRはこれをコンテナ内でも成立させるための環境構築側の決定のみを扱う
  (Runner自体の実行ロジックである[ADR-0005](0005-claude-code-runner-design.md)は変更しない)
- シークレット(GitLab PAT・AWS/Bedrock認証情報)は
  [docs/operations/security.md](../operations/security.md)の方針(OS環境変数・`.env`経由、
  コードやイメージに焼き込まない)をコンテナ環境でも維持する必要がある
- Workspace Manager([`workspace/git_workspace.py`](../../src/gitlab_ai_platform/workspace/git_workspace.py))は
  `<root>/repos/`(bare clone)と`<root>/worktrees/`(MR単位worktree)を1つのルートディレクトリ
  配下に持つ設計。コンテナ環境ではこのルートをコンテナのライフサイクルと切り離して永続化する
  必要がある(コンテナを再作成するたびに毎回全プロジェクトを再cloneするのは非現実的)

## 決定

### ベースイメージ

**`python:3.11-slim`(Debian bookworm系)** を使う。[ADR-0001](0001-repository-structure.md)の
「Python 3.11以上」という決定に一致するバージョンを固定タグで選び、`pip`が標準で使える
Debian系を選ぶことでnpm経由のNode.js導入(後述)も素直に行える。

### Claude Code CLIのインストール方法

**npm経由**(`npm install -g @anthropic-ai/claude-code`)で導入する。ベースイメージに
Node.js 20.x(NodeSourceのAPTリポジトリ経由)を追加してから導入する。

[`docs/operations/setup-windows.md`](../operations/setup-windows.md)ではネイティブ
インストーラー(`irm https://claude.ai/install.ps1 | iex`)を推奨と併記しているが、
Docker側はnpm固定にする:

- バージョンを`@anthropic-ai/claude-code@<version>`のようにピン留めしやすく、
  イメージの再現性(同じDockerfileから同じバージョンが常に出来上がること)を確保しやすい
- 社内の外部ダウンロード制限下でも、npmの`registry`設定を社内ミラーに向けるだけで
  Windows側([setup-windows.md](../operations/setup-windows.md) §3.1と同じ考え方)と
  同様の代替経路が使える

### Bedrock認証情報・GitLab PATの受け渡し

**シークレットはイメージに一切焼き込まない。** [docs/operations/security.md](../operations/security.md)
の既存方針(OS環境変数経由)をコンテナ環境向けに以下の形で踏襲する:

- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` /
  `CLAUDE_CODE_USE_BEDROCK` / `AWS_REGION`、GitLab PAT
  (`GITLAB_AI_PLATFORM_GITLAB_TOKEN`)は、`docker run -e` または Docker Composeの
  `env_file`(`.env`、リポジトリの`.gitignore`済みファイルをそのまま使う)経由でコンテナの
  環境変数として注入する。`Dockerfile`の`ENV`・`ARG`では一切設定しない
- `AWS_PROFILE` + SSO等、静的キーではなく認証情報ファイルを使いたい場合の代替として、
  ホストの`~/.aws`ディレクトリを`:ro`(read-only)でコンテナにマウントする方式も
  `docker-compose.yml`にコメントで示す(既定では有効化しない。静的キーより設定が
  環境依存になるため)
- `config.toml`(GitLab接続先・プロジェクト一覧等、シークレットを含まない設定)は
  ホストからread-onlyでバインドマウントする。イメージには`config.example.toml`のみ
  同梱しない(イメージをビルドする段階でGitLab接続先を焼き込まない)

### Workspace用ボリューム

コンテナの`/data`配下を単一のマウントポイントとし、`config.toml`の
`workspace.root`(既定`workspace`) / `runner.log_dir`(既定`logs/runner`) /
`reviews.root`(既定`reviews`) / `store.db_path` / `job.db_path`をすべてこの配下
(`/data/workspace`・`/data/logs`・`/data/reviews`・`/data/state.db`・`/data/job.db`)に
向ける。Docker Composeでは名前付きボリューム1つ(`gitlab-ai-platform-data`)を`/data`に
マウントする。

bare clone(`repos/`)とworktree(`worktrees/`)を別ボリュームに分けることはしない。
[`GitWorkspaceManager`](../../src/gitlab_ai_platform/workspace/git_workspace.py)は
両者を1つのルートディレクトリ配下に持つ設計であり、分割しても得られる利点がない
(下記「却下した選択肢」参照)。

### 実行ユーザー

イメージ内に非rootユーザー(`runner`)を作成し、`USER runner`で実行する。無人実行である
ことを踏まえ、コンテナ内での権限を必要最小限に絞る。

### ビルド確認・CIとの接続

本Issueのスコープは実行環境の構築であり、実際にBedrock/GitLabへ接続する動作確認は行わない。
確認は次の2点に留める:

1. `docker build`でイメージが構築できること(ローカルで実施・確認済み)
2. コンテナ内で`pip install -e ".[dev]"`相当のセットアップ(`pyproject.toml`の依存解決)が
   通ること(イメージビルド自体に含まれるため、ビルド成功がそのまま確認になる)

GitHub Actions CI(`ci.yml`)への`docker build`の追加は本Issueでは行わない。M3-4は
「実行環境の構築」自体がゴールであり、CI組み込みの要否(イメージのpush先レジストリ選定を
含む)は運用が固まってから別Issueで判断する。

## 却下した選択肢

- **ネイティブインストーラー(`curl https://claude.ai/install.sh | bash`)でのClaude Code導入**:
  Windows手順([setup-windows.md](../operations/setup-windows.md))と経路を揃えられる利点は
  あるが、バージョンピン留め・社内ミラー経由のオフライン代替のしやすさでnpmに劣るため見送り。
  Node.js自体はnpm経由導入に必要なため追加コストにはならない
- **`python:3.11-alpine`をベースにする**: イメージサイズは小さくなるが、musl libc環境での
  Node.js/npmパッケージ(ネイティブアドオンを含みうる)の互換性リスクがあり、
  「まず動く実行環境を作る」という本Issueの目的に対してリスクに見合わないため見送り
- **AWS認証情報・GitLab PATを`ARG`/`ENV`でイメージに焼き込む**: ビルド時にシークレットが
  イメージレイヤーに残り、イメージを別環境へ持ち出す/レジストリにpushする際に漏洩しうる。
  [docs/operations/security.md](../operations/security.md)の既存方針(シークレットは
  コード・イメージに含めない)に反するため却下
- **bare clone用ボリュームとworktree用ボリュームを分割する**: `GitWorkspaceManager`の
  設計(`<root>/repos/`・`<root>/worktrees/`という単一ルート前提、ディスク上限GCも
  `worktrees/`配下のサイズのみで判定)を変更する理由がなく、分割してもバックアップ単位が
  細かくなる以外の利点がない。将来的に必要になれば別Issueで再検討する
- **Docker Composeを必須構成にせず`docker run`の手順のみ記載する**: `env_file`・複数
  ボリューム・ポート(将来のWebhook受信、M3-6)をまとめて指定できるほうが運用者の手順が
  減るため、`docker-compose.yml`をあわせて用意する方針を採用(ADRとしては両方を提供し、
  `docker run`単体でも動く設計自体は維持する)

## 影響

- `Dockerfile`(リポジトリルート): 本ADRの決定に基づくRunnerイメージ定義
- `docker-compose.yml`(リポジトリルート): ワークスペース用ボリューム・環境変数の受け渡しを
  定義
- `.dockerignore`(リポジトリルート): イメージビルド時に`workspace/`・`reviews/`・
  `logs/`・`.env`・`.git`等を除外
- [docs/operations/docker-runtime.md](../operations/docker-runtime.md): 導入・セットアップ手順
  ([setup-windows.md](../operations/setup-windows.md)のLinux/Docker版に相当)
- [docs/architecture.md](../architecture.md)「Windows/Linuxの分担」: Linux/Docker側の
  実体ができたことを反映
