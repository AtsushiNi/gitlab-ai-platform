# Docker実行環境セットアップ手順

- ステータス: 完了(初版)
- 対応Issue: [#94](https://github.com/AtsushiNi/gitlab-ai-platform/issues/94) (M3-4)

Linux/Docker上で`gitlab-ai-platform`のRunnerを動かすまでの手順。
[setup-windows.md](setup-windows.md)のLinux/Docker版に相当する。
[docs/architecture.md](../architecture.md)「Windows/Linuxの分担」の方針どおり、
本手順は**人間が張り付かない無人実行**(M3以降のJob Queue/Orchestratorから起動される
Runner)を想定しており、単発の`review`実行や`watch`常駐も同じイメージで行える。

設計判断の背景は[ADR-0020: Linux/Docker実行環境の構築](../adr/0020-docker-runtime.md)を
参照。本ドキュメントは手順のみを扱う。

## 前提

- Docker Engine(または Docker Desktop)が使えること。本ドキュメントの手順は
  `docker`/`docker compose`コマンドで確認済み
- 社内GitLabへの到達性とAI用GitLabアカウントは別途用意されている前提
  (未取得なら[setup-windows.md §2](setup-windows.md#2-gitlab-personal-access-tokenpatの発行)と
  同じ手順でPATを発行する)
- Amazon BedrockのAWS認証情報(アクセスキーまたはSSOプロファイル)が別途用意されている前提

## 1. イメージのビルド

リポジトリルート(`Dockerfile`・`docker-compose.yml`があるディレクトリ)で実行する:

```bash
docker compose build
```

`Dockerfile`は以下を行う(詳細は[ADR-0020](../adr/0020-docker-runtime.md)参照):

- ベースイメージ`python:3.11-slim`([ADR-0001](../adr/0001-repository-structure.md)の
  Python 3.11以上に一致)
- `git`(Workspace Manager用)とNode.js 20.x + npm経由のClaude Code CLIを導入
- `pip install .`でこのリポジトリの実行時依存(`requests`・`mcp`)をインストール
- 非rootユーザー(`runner`)で実行する設定

`docker compose build`が失敗せず完了すれば、[ADR-0020](../adr/0020-docker-runtime.md)が
定める「イメージのビルド確認」は完了。**この手順ではBedrock/GitLabへの実接続は行わない。**

## 2. シークレットの準備(`.env`)

[`.env.example`](../../.env.example)をコピーして`.env`を作成し、GitLab PATを設定する
(このリポジトリの`.gitignore`対象であり、コミットされない):

```bash
cp .env.example .env
```

`.env`にGitLab PATに加えて、Bedrock用のAWS認証情報を追記する
(GitLab PAT以外の変数は`docs/operations/security.md`の方針どおり、`config/loader.py`の
`.env`読み込み対象ではなくコンテナの環境変数として直接渡るだけの経路になる。
`env_file`はキーを問わず全行を環境変数として注入するため、同じファイルにまとめて書ける):

```text
GITLAB_AI_PLATFORM_GITLAB_TOKEN=<発行したPAT>

AWS_ACCESS_KEY_ID=<発行されたアクセスキー>
AWS_SECRET_ACCESS_KEY=<発行されたシークレットキー>
# AWS_SESSION_TOKEN=<一時認証情報を使う場合のみ>
ANTHROPIC_DEFAULT_SONNET_MODEL=<固定したいモデルのバージョン付きID>
```

**シークレットはイメージに焼き込まない。** `.env`は`docker-compose.yml`の`env_file`経由で
コンテナ起動時にのみ環境変数として渡され、`Dockerfile`側では一切参照しない
([ADR-0020](../adr/0020-docker-runtime.md)「却下した選択肢」)。

AWS SSO等、静的キーではなくプロファイル(`AWS_PROFILE`)を使いたい場合は、`.env`に
`AWS_PROFILE=<プロファイル名>`を設定した上で、`docker-compose.yml`の
`~/.aws:/home/runner/.aws:ro`のマウント行のコメントを外す。

## 3. `config.toml`の準備

[`config.example.toml`](../../config.example.toml)をコピーして`config.toml`を作成し、
GitLab接続先・対象プロジェクトを設定する:

```bash
cp config.example.toml config.toml
```

Docker環境では、bare clone・worktree・実行ログ・レビュー結果・State/Job DBを
すべて`/data`配下の1つのボリュームへ永続化する設計のため([ADR-0020](../adr/0020-docker-runtime.md)
「Workspace用ボリューム」)、以下のセクションを`/data`配下を指すよう上書きする
(既定値のままだと`/app`相対のパスになり、コンテナを再作成するたびに消える):

```toml
[gitlab]
url = "https://gitlab.example.com"
projects = ["group/project-a", "group/project-b"]

[workspace]
root = "/data/workspace"
max_disk_mb = 5000

[runner]
log_dir = "/data/logs/runner"
timeout_seconds = 1800

[reviews]
root = "/data/reviews"

[store]
db_path = "/data/state.db"

[job]
db_path = "/data/job.db"
```

他の項目([configuration.md](configuration.md)参照)は既定値のままでよい。

## 4. 動作確認(ヘルプ表示まで)

Bedrock/GitLabへの実接続は行わず、CLIが正しくセットアップされていることだけを確認する:

```bash
docker compose run --rm runner --help
```

`gitlab-ai-platform`コマンド(`pyproject.toml`の`[project.scripts]`)がイメージ内で
インストール済みで、ヘルプが表示されれば、`pip install -e ".[dev]"`相当のセットアップ
(このイメージでは開発用ツールを含まない`pip install .`)が正しく通っていることが確認できる。

Claude Code CLI自体が導入されていることは、イメージビルド時に実行される
`claude --version`(`Dockerfile`内)がビルド失敗せず完了することで確認済み。

## 5. 単発レビュー実行・常駐(watch)実行

実際にGitLab/Bedrockへ接続する運用は、[setup-windows.md](setup-windows.md)の
`review`/`watch`サブコマンドと同じ引数体系で行える。`docker-compose.yml`の
`command`を上書きして実行する:

```bash
# 単発レビュー実行
docker compose run --rm runner review group/project-a 123

# 常駐(watch)実行。ホストのターミナルを占有せず動かす場合は `up -d`
docker compose run --rm runner watch
```

`docker compose run`はデフォルトのvolume(`gitlab-ai-platform-data`)・`env_file`・
`config.toml`のマウントを引き継ぎつつ、実行するコマンドだけを差し替える。

## 6. ボリュームの永続化・削除

`gitlab-ai-platform-data`という名前付きボリュームに、`/data`配下
(bare clone・worktree・実行ログ・レビュー結果・State/Job DB)がすべて永続化される。

```bash
# ボリュームの実体・使用量を確認する
docker volume inspect gitlab-ai-platform-data

# 完全にクリーンな状態からやり直したい場合(既存レビュー結果・ログもすべて消える)
docker compose down --volumes
```

## 関連ドキュメント

- [ADR-0020: Linux/Docker実行環境の構築](../adr/0020-docker-runtime.md) — 本手順の設計判断
  (ベースイメージ・認証情報の受け渡し・ボリューム設計)
- [docs/adr/0001-repository-structure.md](../adr/0001-repository-structure.md) — Python版数の制約
- [docs/architecture.md](../architecture.md)「Windows/Linuxの分担」節
- [operations/setup-windows.md](setup-windows.md) — Windows版のセットアップ手順(対になる文書)
- [operations/configuration.md](configuration.md) — `config.toml`/`.env`の全項目リファレンス
- [operations/security.md](security.md) — シークレットの管理方針
- [references/spike-s1-claude-code-headless.md](../../references/spike-s1-claude-code-headless.md) —
  Claude Code/Bedrock認証の調査
