"""設定ファイル(config.toml)と.env(シークレット)からConfigを組み立てる。"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .models import Config

# GitLab PATはコード非公開の.envまたは実際の環境変数で渡す。config.toml(リポジトリに
# コミットされうる)には置かない
GITLAB_TOKEN_ENV_KEY = "GITLAB_AI_PLATFORM_GITLAB_TOKEN"

DEFAULT_CONFIG_PATH = Path("config.toml")
DEFAULT_ENV_PATH = Path(".env")

DEFAULT_POLL_INTERVAL_SECONDS = 60
DEFAULT_MAX_PARALLEL = 5
DEFAULT_REVIEW_LABEL = "レビュー待ち"
DEFAULT_WORKSPACE_ROOT = "workspace"
DEFAULT_WORKSPACE_MAX_DISK_MB = 5000
DEFAULT_RUNNER_LOG_DIR = "logs/runner"
DEFAULT_RUNNER_TIMEOUT_SECONDS = 1800
DEFAULT_REVIEWS_ROOT = "reviews"
DEFAULT_STATE_DB_PATH = "state.db"
DEFAULT_JOB_DB_PATH = "job.db"


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

    raw: dict = {}
    if config_path.is_file():
        with config_path.open("rb") as f:
            raw = tomllib.load(f)

    gitlab_section = raw.get("gitlab", {})
    poller_section = raw.get("poller", {})
    review_section = raw.get("review", {})
    workspace_section = raw.get("workspace", {})
    runner_section = raw.get("runner", {})
    reviews_section = raw.get("reviews", {})
    store_section = raw.get("store", {})
    job_section = raw.get("job", {})

    return Config.from_raw(
        gitlab_url=gitlab_section.get("url", ""),
        gitlab_token=gitlab_token,
        projects=gitlab_section.get("projects", []),
        poll_interval_seconds=poller_section.get(
            "interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS
        ),
        max_parallel=poller_section.get("max_parallel", DEFAULT_MAX_PARALLEL),
        review_label=review_section.get("label", DEFAULT_REVIEW_LABEL),
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
        job_db_path=job_section.get("db_path", DEFAULT_JOB_DB_PATH),
    )
