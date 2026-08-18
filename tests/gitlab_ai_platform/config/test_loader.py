from pathlib import Path

import pytest

from gitlab_ai_platform.config import (
    API_TOKEN_ENV_KEY,
    GITLAB_MCP_TOKEN_ENV_KEY,
    GITLAB_TOKEN_ENV_KEY,
    GITLAB_WEBHOOK_SECRET_ENV_KEY,
    STORE_POSTGRES_PASSWORD_ENV_KEY,
    ConfigError,
    load_config,
)
from gitlab_ai_platform.config.loader import parse_env_file

VALID_CONFIG_TOML = """
[gitlab]
url = "https://gitlab.example.com"
projects = ["group/project-a", "group/project-b"]

[poller]
interval_seconds = 30
max_parallel = 3

[review]
label = "レビュー待ち"

[issue]
label = "AI実装"
ticket_db_path = "custom-issue-tickets.db"

[workspace]
root = "custom-workspace"
max_disk_mb = 1000

[runner]
log_dir = "custom-logs"
timeout_seconds = 900

[reviews]
root = "custom-reviews"

[store]
db_path = "custom-state.db"
backend = "postgresql"

[store.postgres]
host = "db.example.com"
port = 5433
dbname = "custom-db"
user = "custom-user"

[job]
db_path = "custom-job.db"

[webhook]
enabled = true
host = "127.0.0.1"
port = 9090
path = "/gitlab/webhook"

[api]
host = "127.0.0.1"
port = 9191
"""


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_env_file_reads_key_value_pairs(tmp_path):
    env_path = _write(
        tmp_path / ".env",
        """
        # comment line, should be ignored
        FOO=bar
        QUOTED="quoted value"
        SINGLE_QUOTED='single'
        EMPTY_IGNORED
        """.strip()
        + "\n",
    )

    values = parse_env_file(env_path)

    assert values == {
        "FOO": "bar",
        "QUOTED": "quoted value",
        "SINGLE_QUOTED": "single",
    }


def test_parse_env_file_returns_empty_dict_when_missing(tmp_path):
    assert parse_env_file(tmp_path / "does-not-exist.env") == {}


def test_load_config_merges_config_file_and_env_file(tmp_path):
    config_path = _write(tmp_path / "config.toml", VALID_CONFIG_TOML)
    env_path = _write(
        tmp_path / ".env",
        f"{GITLAB_TOKEN_ENV_KEY}=from-dotenv\n"
        f"{GITLAB_MCP_TOKEN_ENV_KEY}=mcp-from-dotenv\n"
        f"{GITLAB_WEBHOOK_SECRET_ENV_KEY}=whsec-from-dotenv\n"
        f"{STORE_POSTGRES_PASSWORD_ENV_KEY}=pgpw-from-dotenv\n"
        f"{API_TOKEN_ENV_KEY}=apitoken-from-dotenv\n",
    )

    config = load_config(config_path=config_path, env_path=env_path)

    assert config.gitlab_url == "https://gitlab.example.com"
    assert config.gitlab_token == "from-dotenv"
    assert config.gitlab_token_mcp == "mcp-from-dotenv"
    assert config.projects == ("group/project-a", "group/project-b")
    assert config.poll_interval_seconds == 30
    assert config.max_parallel == 3
    assert config.review_label == "レビュー待ち"
    assert config.issue_label == "AI実装"
    assert config.issue_ticket_db_path == "custom-issue-tickets.db"
    assert config.workspace_root == "custom-workspace"
    assert config.workspace_max_disk_mb == 1000
    assert config.runner_log_dir == "custom-logs"
    assert config.runner_timeout_seconds == 900
    assert config.reviews_root == "custom-reviews"
    assert config.state_db_path == "custom-state.db"
    assert config.store_backend == "postgresql"
    assert config.store_postgres_host == "db.example.com"
    assert config.store_postgres_port == 5433
    assert config.store_postgres_dbname == "custom-db"
    assert config.store_postgres_user == "custom-user"
    assert config.store_postgres_password == "pgpw-from-dotenv"
    assert config.job_db_path == "custom-job.db"
    assert config.webhook_enabled is True
    assert config.webhook_host == "127.0.0.1"
    assert config.webhook_port == 9090
    assert config.webhook_path == "/gitlab/webhook"
    assert config.webhook_secret_token == "whsec-from-dotenv"
    assert config.api_host == "127.0.0.1"
    assert config.api_port == 9191
    assert config.api_token == "apitoken-from-dotenv"


def test_load_config_prefers_real_env_var_over_dotenv_file(tmp_path, monkeypatch):
    config_path = _write(tmp_path / "config.toml", VALID_CONFIG_TOML)
    env_path = _write(
        tmp_path / ".env",
        f"{GITLAB_TOKEN_ENV_KEY}=from-dotenv\n"
        f"{GITLAB_WEBHOOK_SECRET_ENV_KEY}=whsec-from-dotenv\n",
    )
    monkeypatch.setenv(GITLAB_TOKEN_ENV_KEY, "from-real-env")

    config = load_config(config_path=config_path, env_path=env_path)

    assert config.gitlab_token == "from-real-env"


def test_load_config_falls_back_mcp_token_to_gitlab_token_when_unset(
    tmp_path, monkeypatch
):
    config_path = _write(tmp_path / "config.toml", VALID_CONFIG_TOML)
    env_path = _write(
        tmp_path / ".env",
        f"{GITLAB_TOKEN_ENV_KEY}=only-review-token\n"
        f"{GITLAB_WEBHOOK_SECRET_ENV_KEY}=whsec-from-dotenv\n",
    )
    monkeypatch.delenv(GITLAB_MCP_TOKEN_ENV_KEY, raising=False)

    config = load_config(config_path=config_path, env_path=env_path)

    assert config.gitlab_token == "only-review-token"
    assert config.gitlab_token_mcp == "only-review-token"


def test_load_config_applies_defaults_when_optional_sections_missing(
    tmp_path, monkeypatch
):
    config_path = _write(
        tmp_path / "config.toml",
        """
        [gitlab]
        url = "https://gitlab.example.com"
        projects = ["group/project-a"]
        """,
    )
    monkeypatch.setenv(GITLAB_TOKEN_ENV_KEY, "token")

    config = load_config(config_path=config_path, env_path=tmp_path / "missing.env")

    assert config.poll_interval_seconds == 60
    assert config.max_parallel == 5
    assert config.review_label == "レビュー待ち"
    assert config.issue_label == "AI実装"
    assert config.issue_ticket_db_path == "issue_tickets.db"
    assert config.workspace_root == "workspace"
    assert config.workspace_max_disk_mb == 5000
    assert config.runner_log_dir == "logs/runner"
    assert config.runner_timeout_seconds == 1800
    assert config.reviews_root == "reviews"
    assert config.state_db_path == "state.db"
    assert config.store_backend == "sqlite"
    assert config.store_postgres_host == "localhost"
    assert config.store_postgres_port == 5432
    assert config.store_postgres_dbname == "gitlab_ai_platform"
    assert config.store_postgres_user == "gitlab_ai_platform"
    assert config.store_postgres_password == ""
    assert config.job_db_path == "job.db"
    assert config.webhook_enabled is False
    assert config.webhook_host == "0.0.0.0"
    assert config.webhook_port == 8088
    assert config.webhook_path == "/webhook"
    assert config.webhook_secret_token == ""
    assert config.api_host == "127.0.0.1"
    assert config.api_port == 8090
    assert config.api_token == ""


def test_load_config_raises_without_leaking_token_when_other_fields_invalid(
    tmp_path, monkeypatch
):
    config_path = _write(
        tmp_path / "config.toml",
        """
        [gitlab]
        url = "https://gitlab.example.com"
        projects = []
        """,
    )
    monkeypatch.setenv(GITLAB_TOKEN_ENV_KEY, "super-secret-pat")

    with pytest.raises(ConfigError) as excinfo:
        load_config(config_path=config_path, env_path=tmp_path / "missing.env")

    assert "super-secret-pat" not in str(excinfo.value)


def test_load_config_raises_when_token_missing(tmp_path, monkeypatch):
    config_path = _write(tmp_path / "config.toml", VALID_CONFIG_TOML)
    monkeypatch.delenv(GITLAB_TOKEN_ENV_KEY, raising=False)

    with pytest.raises(ConfigError):
        load_config(config_path=config_path, env_path=tmp_path / "missing.env")


def test_load_config_raises_when_config_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(GITLAB_TOKEN_ENV_KEY, "token")

    with pytest.raises(ConfigError):
        load_config(
            config_path=tmp_path / "missing-config.toml",
            env_path=tmp_path / "missing.env",
        )


def test_load_config_raises_when_webhook_enabled_without_secret_token(
    tmp_path, monkeypatch
):
    config_path = _write(
        tmp_path / "config.toml",
        """
        [gitlab]
        url = "https://gitlab.example.com"
        projects = ["group/project-a"]

        [webhook]
        enabled = true
        """,
    )
    monkeypatch.setenv(GITLAB_TOKEN_ENV_KEY, "token")
    monkeypatch.delenv(GITLAB_WEBHOOK_SECRET_ENV_KEY, raising=False)

    with pytest.raises(ConfigError, match="Secret Token"):
        load_config(config_path=config_path, env_path=tmp_path / "missing.env")
