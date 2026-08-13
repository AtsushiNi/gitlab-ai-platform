"""設定値の型とバリデーション。"""

from __future__ import annotations

from dataclasses import dataclass


class ConfigError(ValueError):
    """設定の読み込み・検証に失敗したことを表す例外。

    メッセージにPAT等のシークレット値そのものを含めないこと。
    """


@dataclass(frozen=True)
class Config:
    gitlab_url: str
    gitlab_token: str
    projects: tuple[str, ...]
    poll_interval_seconds: int
    max_parallel: int
    review_label: str
    workspace_root: str
    workspace_max_disk_mb: int
    runner_log_dir: str
    runner_timeout_seconds: int
    reviews_root: str
    state_db_path: str

    def __repr__(self) -> str:
        # gitlab_tokenが誤ってログ・例外メッセージに出力されるのを防ぐため、reprでマスクする
        return (
            f"Config(gitlab_url={self.gitlab_url!r}, gitlab_token='***', "
            f"projects={self.projects!r}, "
            f"poll_interval_seconds={self.poll_interval_seconds!r}, "
            f"max_parallel={self.max_parallel!r}, "
            f"review_label={self.review_label!r}, "
            f"workspace_root={self.workspace_root!r}, "
            f"workspace_max_disk_mb={self.workspace_max_disk_mb!r}, "
            f"runner_log_dir={self.runner_log_dir!r}, "
            f"runner_timeout_seconds={self.runner_timeout_seconds!r}, "
            f"reviews_root={self.reviews_root!r}, "
            f"state_db_path={self.state_db_path!r})"
        )

    @classmethod
    def from_raw(
        cls,
        *,
        gitlab_url: object,
        gitlab_token: object,
        projects: object,
        poll_interval_seconds: object,
        max_parallel: object,
        review_label: object,
        workspace_root: object,
        workspace_max_disk_mb: object,
        runner_log_dir: object,
        runner_timeout_seconds: object,
        reviews_root: object,
        state_db_path: object,
    ) -> "Config":
        """未検証の生値からConfigを組み立てる。不正な値があれば ConfigError をまとめて送出する。"""
        errors: list[str] = []

        clean_url = _require_nonempty_str(gitlab_url, "gitlab.url", errors)
        if clean_url and not (
            clean_url.startswith("http://") or clean_url.startswith("https://")
        ):
            errors.append("gitlab.url は http:// または https:// から始まる必要があります")

        if not isinstance(gitlab_token, str) or not gitlab_token.strip():
            errors.append(
                "GitLab PAT が設定されていません(.env の GitLab トークン用の値を確認してください)"
            )

        clean_projects = _require_project_list(projects, errors)
        clean_interval = _require_positive_int(
            poll_interval_seconds, "poller.interval_seconds", errors
        )
        clean_max_parallel = _require_positive_int(
            max_parallel, "poller.max_parallel", errors
        )
        clean_label = _require_nonempty_str(review_label, "review.label", errors)
        clean_workspace_root = _require_nonempty_str(
            workspace_root, "workspace.root", errors
        )
        clean_workspace_max_disk_mb = _require_positive_int(
            workspace_max_disk_mb, "workspace.max_disk_mb", errors
        )
        clean_runner_log_dir = _require_nonempty_str(
            runner_log_dir, "runner.log_dir", errors
        )
        clean_runner_timeout_seconds = _require_positive_int(
            runner_timeout_seconds, "runner.timeout_seconds", errors
        )
        clean_reviews_root = _require_nonempty_str(reviews_root, "reviews.root", errors)
        clean_state_db_path = _require_nonempty_str(state_db_path, "store.db_path", errors)

        if errors:
            raise ConfigError("; ".join(errors))

        assert clean_url is not None
        assert isinstance(gitlab_token, str)
        assert clean_interval is not None
        assert clean_max_parallel is not None
        assert clean_label is not None
        assert clean_workspace_root is not None
        assert clean_workspace_max_disk_mb is not None
        assert clean_runner_log_dir is not None
        assert clean_runner_timeout_seconds is not None
        assert clean_reviews_root is not None
        assert clean_state_db_path is not None

        return cls(
            gitlab_url=clean_url.rstrip("/"),
            gitlab_token=gitlab_token.strip(),
            projects=clean_projects,
            poll_interval_seconds=clean_interval,
            max_parallel=clean_max_parallel,
            review_label=clean_label,
            workspace_root=clean_workspace_root,
            workspace_max_disk_mb=clean_workspace_max_disk_mb,
            runner_log_dir=clean_runner_log_dir,
            runner_timeout_seconds=clean_runner_timeout_seconds,
            reviews_root=clean_reviews_root,
            state_db_path=clean_state_db_path,
        )


def _require_nonempty_str(value: object, field_name: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field_name} は空でない文字列である必要があります")
        return None
    return value.strip()


def _require_positive_int(value: object, field_name: str, errors: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        errors.append(f"{field_name} は正の整数である必要があります")
        return None
    return value


def _require_project_list(value: object, errors: list[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        errors.append("gitlab.projects は1件以上指定する必要があります")
        return ()

    cleaned = [p.strip() for p in value if isinstance(p, str) and p.strip()]
    if len(cleaned) != len(value):
        errors.append("gitlab.projects に空文字列や文字列以外の値が含まれています")
        return ()

    return tuple(cleaned)
