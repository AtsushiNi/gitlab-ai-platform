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

    def __repr__(self) -> str:
        # gitlab_tokenが誤ってログ・例外メッセージに出力されるのを防ぐため、reprでマスクする
        return (
            f"Config(gitlab_url={self.gitlab_url!r}, gitlab_token='***', "
            f"projects={self.projects!r}, "
            f"poll_interval_seconds={self.poll_interval_seconds!r}, "
            f"max_parallel={self.max_parallel!r}, "
            f"review_label={self.review_label!r})"
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

        if errors:
            raise ConfigError("; ".join(errors))

        assert clean_url is not None
        assert isinstance(gitlab_token, str)
        assert clean_interval is not None
        assert clean_max_parallel is not None
        assert clean_label is not None

        return cls(
            gitlab_url=clean_url.rstrip("/"),
            gitlab_token=gitlab_token.strip(),
            projects=clean_projects,
            poll_interval_seconds=clean_interval,
            max_parallel=clean_max_parallel,
            review_label=clean_label,
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
