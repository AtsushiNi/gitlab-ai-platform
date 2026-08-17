from __future__ import annotations

import threading
from pathlib import Path

import pytest

from gitlab_ai_platform.cli.api_server import run_api_server
from gitlab_ai_platform.config import Config, ConfigError

_PROJECT = "group/project"


def _config(tmp_path: Path, **overrides) -> Config:
    kwargs = dict(
        gitlab_url="https://gitlab.example.com",
        gitlab_token="secret-token",
        projects=[_PROJECT],
        poll_interval_seconds=60,
        max_parallel=5,
        review_label="レビュー待ち",
        workspace_root=str(tmp_path / "workspace"),
        workspace_max_disk_mb=1000,
        runner_log_dir=str(tmp_path / "logs"),
        runner_timeout_seconds=1800,
        reviews_root=str(tmp_path / "reviews"),
        state_db_path=":memory:",
        job_db_path=":memory:",
        webhook_enabled=False,
        webhook_host="127.0.0.1",
        webhook_port=8088,
        webhook_path="/webhook",
        webhook_secret_token="",
        store_backend="sqlite",
        store_postgres_host="localhost",
        store_postgres_port=5432,
        store_postgres_dbname="gitlab_ai_platform",
        store_postgres_user="gitlab_ai_platform",
        store_postgres_password="",
        # config.api_portは正の整数が必須(`_require_positive_int`)のため`0`は使えない。
        # OS割り当てポートで起動したいテストは`ApiServer`を直接検証する
        # `tests/gitlab_ai_platform/api/test_server.py`側で行う
        api_host="127.0.0.1",
        api_port=18090,
        api_token="",
    )
    kwargs.update(overrides)
    return Config.from_raw(**kwargs)


def test_run_api_server_raises_config_error_when_token_missing(tmp_path):
    # api_tokenが空のまま起動しようとするとConfigErrorになり、ポートをbindしない
    # (ADR-0023「決定」)
    config = _config(tmp_path, api_token="")

    with pytest.raises(ConfigError, match="api.token"):
        run_api_server(config)


def test_run_api_server_starts_and_stops_when_stop_event_already_set(tmp_path):
    # stop_eventを起動前にセットしておけば、start()直後にwait()が即座に戻り、
    # 実サービスに繋がらない範囲でrun_api_server全体(組み立て→start→stop→close)を検証できる
    config = _config(tmp_path, api_token="test-api-token")
    stop_event = threading.Event()
    stop_event.set()

    run_api_server(config, stop_event=stop_event)
