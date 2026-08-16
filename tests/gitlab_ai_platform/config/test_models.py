import pytest

from gitlab_ai_platform.config import Config, ConfigError


def _valid_kwargs(**overrides):
    kwargs = dict(
        gitlab_url="https://gitlab.example.com",
        gitlab_token="secret-token",
        projects=["group/project-a"],
        poll_interval_seconds=60,
        max_parallel=5,
        review_label="レビュー待ち",
        workspace_root="workspace",
        workspace_max_disk_mb=5000,
        runner_log_dir="logs/runner",
        runner_timeout_seconds=1800,
        reviews_root="reviews",
        state_db_path="state.db",
    )
    kwargs.update(overrides)
    return kwargs


def test_from_raw_builds_config_with_valid_values():
    config = Config.from_raw(**_valid_kwargs())

    assert config.gitlab_url == "https://gitlab.example.com"
    assert config.gitlab_token == "secret-token"
    assert config.projects == ("group/project-a",)
    assert config.poll_interval_seconds == 60
    assert config.max_parallel == 5
    assert config.review_label == "レビュー待ち"
    assert config.workspace_root == "workspace"
    assert config.workspace_max_disk_mb == 5000
    assert config.runner_log_dir == "logs/runner"
    assert config.runner_timeout_seconds == 1800
    assert config.reviews_root == "reviews"
    assert config.state_db_path == "state.db"


def test_from_raw_strips_trailing_slash_from_url():
    config = Config.from_raw(**_valid_kwargs(gitlab_url="https://gitlab.example.com/"))

    assert config.gitlab_url == "https://gitlab.example.com"


def test_from_raw_strips_whitespace_from_projects():
    config = Config.from_raw(
        **_valid_kwargs(projects=[" group/project-a ", "group/project-b"])
    )

    assert config.projects == ("group/project-a", "group/project-b")


@pytest.mark.parametrize(
    "overrides",
    [
        {"gitlab_url": ""},
        {"gitlab_url": "ftp://gitlab.example.com"},
        {"gitlab_token": ""},
        {"gitlab_token": "   "},
        {"gitlab_token": None},
        {"projects": []},
        {"projects": [""]},
        {"projects": [123]},
        {"poll_interval_seconds": 0},
        {"poll_interval_seconds": -1},
        {"poll_interval_seconds": True},
        {"poll_interval_seconds": "60"},
        {"max_parallel": 0},
        {"review_label": ""},
        {"workspace_root": ""},
        {"workspace_max_disk_mb": 0},
        {"workspace_max_disk_mb": -1},
        {"runner_log_dir": ""},
        {"runner_timeout_seconds": 0},
        {"runner_timeout_seconds": True},
        {"reviews_root": ""},
        {"state_db_path": ""},
    ],
)
def test_from_raw_rejects_invalid_values(overrides):
    with pytest.raises(ConfigError):
        Config.from_raw(**_valid_kwargs(**overrides))


def test_repr_masks_gitlab_token():
    config = Config.from_raw(**_valid_kwargs(gitlab_token="super-secret-pat"))

    assert "super-secret-pat" not in repr(config)
    assert "***" in repr(config)


def test_config_error_message_does_not_leak_token_on_other_failures():
    with pytest.raises(ConfigError) as excinfo:
        Config.from_raw(
            **_valid_kwargs(gitlab_token="super-secret-pat", max_parallel=0)
        )

    assert "super-secret-pat" not in str(excinfo.value)
