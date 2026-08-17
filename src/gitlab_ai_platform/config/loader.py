"""設定ファイル(config.toml)と.env(シークレット)からConfigを組み立てる。"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .models import Config

# GitLab PATはコード非公開の.envまたは実際の環境変数で渡す。config.toml(リポジトリに
# コミットされうる)には置かない
GITLAB_TOKEN_ENV_KEY = "GITLAB_AI_PLATFORM_GITLAB_TOKEN"

# 対話型GitLab Adapter MCP Server専用のPAT(M3-8, docs/adr/0019-gitlab-token-scoping.md)。
# 未設定の場合はGITLAB_AI_PLATFORM_GITLAB_TOKENにフォールバックする(後方互換。用途別トークンの
# 分離は運用者が任意に導入できるオプションであり、必須ではない)
GITLAB_MCP_TOKEN_ENV_KEY = "GITLAB_AI_PLATFORM_GITLAB_TOKEN_MCP"

# Webhook Secret Token(M3-6, docs/adr/0018-webhook-receiver.md)も同じ理由で.env/環境変数経由
GITLAB_WEBHOOK_SECRET_ENV_KEY = "GITLAB_AI_PLATFORM_WEBHOOK_SECRET"

# HTTP APIのトークン(M3-7, docs/adr/0023-http-api.md)も同じ理由で.env/環境変数経由
API_TOKEN_ENV_KEY = "GITLAB_AI_PLATFORM_API_TOKEN"

# PostgreSQL State Store(M3-5, docs/adr/0021-state-store-postgresql.md)のパスワードも
# 同じ理由で.env/環境変数経由。ホスト・ポート・DB名・ユーザー名はconfig.tomlに書く
STORE_POSTGRES_PASSWORD_ENV_KEY = "GITLAB_AI_PLATFORM_STORE_POSTGRES_PASSWORD"

DEFAULT_CONFIG_PATH = Path("config.toml")
DEFAULT_ENV_PATH = Path(".env")

DEFAULT_POLL_INTERVAL_SECONDS = 60
DEFAULT_MAX_PARALLEL = 5
DEFAULT_REVIEW_LABEL = "レビュー待ち"
# Issue Poller(M4-1, docs/adr/0024-issue-poller-dedup.md)。無人実行に回すことを人間が明示した
# Issueにこのラベルを付ける運用を想定する
DEFAULT_ISSUE_LABEL = "AI実装"
DEFAULT_ISSUE_TICKET_DB_PATH = "issue_tickets.db"
DEFAULT_WORKSPACE_ROOT = "workspace"
DEFAULT_WORKSPACE_MAX_DISK_MB = 5000
DEFAULT_RUNNER_LOG_DIR = "logs/runner"
DEFAULT_RUNNER_TIMEOUT_SECONDS = 1800
DEFAULT_REVIEWS_ROOT = "reviews"
DEFAULT_STATE_DB_PATH = "state.db"
# PostgreSQL対応(M3-5, docs/adr/0021-state-store-postgresql.md)。既定は従来通りSQLite
DEFAULT_STORE_BACKEND = "sqlite"
DEFAULT_STORE_POSTGRES_HOST = "localhost"
DEFAULT_STORE_POSTGRES_PORT = 5432
DEFAULT_STORE_POSTGRES_DBNAME = "gitlab_ai_platform"
DEFAULT_STORE_POSTGRES_USER = "gitlab_ai_platform"
DEFAULT_JOB_DB_PATH = "job.db"
DEFAULT_WEBHOOK_ENABLED = False
# 既定は全インターフェース待受(社内GitLabサーバーからの到達性を優先)。有効化自体が
# 既定OFFのオプション機能であり、有効化する運用者が`webhook.host`で明示的に絞り込める
DEFAULT_WEBHOOK_HOST = "0.0.0.0"
DEFAULT_WEBHOOK_PORT = 8088
DEFAULT_WEBHOOK_PATH = "/webhook"
# HTTP API(M3-7, docs/adr/0023-http-api.md)。Webhookと異なり社内GitLabからの外部到達性は
# 不要で、`api`サブコマンドを明示実行した運用者が意図的にhostを変えない限り外部に晒さない
# ((docs/adr/0023-http-api.md)「決定」参照)
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8090


def parse_env_file(path: Path) -> dict[str, str]:
    """.env ファイルを最小限の KEY=VALUE 形式でパースする(外部ライブラリ不使用)。

    存在しないファイルは空辞書として扱う。
    """
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        if key:
            values[key] = value

    return values


def _load_env(env_path: Path) -> dict[str, str]:
    # .env ファイルの値をベースに、実際の環境変数(既にexport済みのもの)で上書きする。
    # CI・本番実行では環境変数を、ローカル開発では.envを使う想定
    merged = parse_env_file(env_path)
    merged.update(os.environ)
    return merged


def load_config(
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    env_path: Path | str = DEFAULT_ENV_PATH,
) -> Config:
    """設定ファイルとシークレットを読み込み、検証済みのConfigを返す。

    値が不正な場合は ConfigError を送出する(PATの値そのものは例外メッセージに含めない)。
    """
    config_path = Path(config_path)
    env_path = Path(env_path)

    env = _load_env(env_path)
    gitlab_token = env.get(GITLAB_TOKEN_ENV_KEY, "")
    # 未設定なら自動実行系と同じトークンにフォールバックする(Config.from_raw側で解決)
    gitlab_token_mcp = env.get(GITLAB_MCP_TOKEN_ENV_KEY, "")
    webhook_secret_token = env.get(GITLAB_WEBHOOK_SECRET_ENV_KEY, "")
    store_postgres_password = env.get(STORE_POSTGRES_PASSWORD_ENV_KEY, "")
    api_token = env.get(API_TOKEN_ENV_KEY, "")

    raw: dict = {}
    if config_path.is_file():
        with config_path.open("rb") as f:
            raw = tomllib.load(f)

    gitlab_section = raw.get("gitlab", {})
    poller_section = raw.get("poller", {})
    review_section = raw.get("review", {})
    issue_section = raw.get("issue", {})
    workspace_section = raw.get("workspace", {})
    runner_section = raw.get("runner", {})
    reviews_section = raw.get("reviews", {})
    store_section = raw.get("store", {})
    store_postgres_section = store_section.get("postgres", {})
    job_section = raw.get("job", {})
    webhook_section = raw.get("webhook", {})
    api_section = raw.get("api", {})

    return Config.from_raw(
        gitlab_url=gitlab_section.get("url", ""),
        gitlab_token=gitlab_token,
        gitlab_token_mcp=gitlab_token_mcp,
        projects=gitlab_section.get("projects", []),
        poll_interval_seconds=poller_section.get(
            "interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS
        ),
        max_parallel=poller_section.get("max_parallel", DEFAULT_MAX_PARALLEL),
        review_label=review_section.get("label", DEFAULT_REVIEW_LABEL),
        issue_label=issue_section.get("label", DEFAULT_ISSUE_LABEL),
        issue_ticket_db_path=issue_section.get(
            "ticket_db_path", DEFAULT_ISSUE_TICKET_DB_PATH
        ),
        workspace_root=workspace_section.get("root", DEFAULT_WORKSPACE_ROOT),
        workspace_max_disk_mb=workspace_section.get(
            "max_disk_mb", DEFAULT_WORKSPACE_MAX_DISK_MB
        ),
        runner_log_dir=runner_section.get("log_dir", DEFAULT_RUNNER_LOG_DIR),
        runner_timeout_seconds=runner_section.get(
            "timeout_seconds", DEFAULT_RUNNER_TIMEOUT_SECONDS
        ),
        reviews_root=reviews_section.get("root", DEFAULT_REVIEWS_ROOT),
        state_db_path=store_section.get("db_path", DEFAULT_STATE_DB_PATH),
        store_backend=store_section.get("backend", DEFAULT_STORE_BACKEND),
        store_postgres_host=store_postgres_section.get(
            "host", DEFAULT_STORE_POSTGRES_HOST
        ),
        store_postgres_port=store_postgres_section.get(
            "port", DEFAULT_STORE_POSTGRES_PORT
        ),
        store_postgres_dbname=store_postgres_section.get(
            "dbname", DEFAULT_STORE_POSTGRES_DBNAME
        ),
        store_postgres_user=store_postgres_section.get(
            "user", DEFAULT_STORE_POSTGRES_USER
        ),
        store_postgres_password=store_postgres_password,
        job_db_path=job_section.get("db_path", DEFAULT_JOB_DB_PATH),
        webhook_enabled=webhook_section.get("enabled", DEFAULT_WEBHOOK_ENABLED),
        webhook_host=webhook_section.get("host", DEFAULT_WEBHOOK_HOST),
        webhook_port=webhook_section.get("port", DEFAULT_WEBHOOK_PORT),
        webhook_path=webhook_section.get("path", DEFAULT_WEBHOOK_PATH),
        webhook_secret_token=webhook_secret_token,
        api_host=api_section.get("host", DEFAULT_API_HOST),
        api_port=api_section.get("port", DEFAULT_API_PORT),
        api_token=api_token,
    )
