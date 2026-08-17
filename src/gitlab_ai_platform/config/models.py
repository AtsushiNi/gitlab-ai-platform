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
    # 対話型GitLab Adapter MCP Server専用のPAT(M3-8, docs/adr/0019-gitlab-token-scoping.md)。
    # 用途別トークンを分けない運用では、from_rawがgitlab_tokenと同じ値にフォールバックする
    gitlab_token_mcp: str
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
    store_backend: str
    store_postgres_host: str
    store_postgres_port: int
    store_postgres_dbname: str
    store_postgres_user: str
    store_postgres_password: str
    job_db_path: str
    webhook_enabled: bool
    webhook_host: str
    webhook_port: int
    webhook_path: str
    webhook_secret_token: str
    api_host: str
    api_port: int
    api_token: str

    def __repr__(self) -> str:
        # gitlab_token/webhook_secret_token/store_postgres_password/api_tokenが誤ってログ・
        # 例外メッセージに出力されるのを防ぐため、reprでマスクする
        return (
            f"Config(gitlab_url={self.gitlab_url!r}, gitlab_token='***', "
            f"gitlab_token_mcp='***', "
            f"projects={self.projects!r}, "
            f"poll_interval_seconds={self.poll_interval_seconds!r}, "
            f"max_parallel={self.max_parallel!r}, "
            f"review_label={self.review_label!r}, "
            f"workspace_root={self.workspace_root!r}, "
            f"workspace_max_disk_mb={self.workspace_max_disk_mb!r}, "
            f"runner_log_dir={self.runner_log_dir!r}, "
            f"runner_timeout_seconds={self.runner_timeout_seconds!r}, "
            f"reviews_root={self.reviews_root!r}, "
            f"state_db_path={self.state_db_path!r}, "
            f"store_backend={self.store_backend!r}, "
            f"store_postgres_host={self.store_postgres_host!r}, "
            f"store_postgres_port={self.store_postgres_port!r}, "
            f"store_postgres_dbname={self.store_postgres_dbname!r}, "
            f"store_postgres_user={self.store_postgres_user!r}, "
            f"store_postgres_password='***', "
            f"job_db_path={self.job_db_path!r}, "
            f"webhook_enabled={self.webhook_enabled!r}, "
            f"webhook_host={self.webhook_host!r}, "
            f"webhook_port={self.webhook_port!r}, "
            f"webhook_path={self.webhook_path!r}, "
            f"webhook_secret_token='***', "
            f"api_host={self.api_host!r}, "
            f"api_port={self.api_port!r}, "
            f"api_token='***')"
        )

    @classmethod
    def from_raw(
        cls,
        *,
        gitlab_url: object,
        gitlab_token: object,
        gitlab_token_mcp: object = "",
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
        store_backend: object,
        store_postgres_host: object,
        store_postgres_port: object,
        store_postgres_dbname: object,
        store_postgres_user: object,
        store_postgres_password: object,
        job_db_path: object,
        webhook_enabled: object,
        webhook_host: object,
        webhook_port: object,
        webhook_path: object,
        webhook_secret_token: object,
        api_host: object,
        api_port: object,
        api_token: object,
    ) -> Config:
        """未検証の生値からConfigを組み立てる。不正な値があれば ConfigError をまとめて送出する。"""
        errors: list[str] = []

        clean_url = _require_nonempty_str(gitlab_url, "gitlab.url", errors)
        if clean_url and not (
            clean_url.startswith("http://") or clean_url.startswith("https://")
        ):
            errors.append(
                "gitlab.url は http:// または https:// から始まる必要があります"
            )

        if not isinstance(gitlab_token, str) or not gitlab_token.strip():
            errors.append(
                "GitLab PAT が設定されていません(.env の GitLab トークン用の値を確認してください)"
            )

        # MCP用PATは未設定を許容する(gitlab_tokenへフォールバックするため必須ではない)。
        # 型が違う場合のみエラーにする
        if not isinstance(gitlab_token_mcp, str):
            errors.append("GitLab PAT(MCP用)は文字列である必要があります")

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
        clean_state_db_path = _require_nonempty_str(
            state_db_path, "store.db_path", errors
        )

        # PostgreSQL対応(M3-5, docs/adr/0021-state-store-postgresql.md)。backendは
        # "sqlite"(既定)か"postgresql"のいずれかのみ許可する
        clean_store_backend = _require_nonempty_str(
            store_backend, "store.backend", errors
        )
        if clean_store_backend and clean_store_backend not in (
            "sqlite",
            "postgresql",
        ):
            errors.append(
                'store.backend は "sqlite" または "postgresql" のいずれかである必要があります'
            )

        clean_store_postgres_host = _require_nonempty_str(
            store_postgres_host, "store.postgres.host", errors
        )
        clean_store_postgres_port = _require_positive_int(
            store_postgres_port, "store.postgres.port", errors
        )
        clean_store_postgres_dbname = _require_nonempty_str(
            store_postgres_dbname, "store.postgres.dbname", errors
        )
        clean_store_postgres_user = _require_nonempty_str(
            store_postgres_user, "store.postgres.user", errors
        )
        # パスワードはGitLab PAT/Webhook Secretと同じ理由で.env/環境変数経由だが、必須にはしない
        # (ADR-0021: ローカルDocker Compose等のtrust認証運用ではパスワード無しがありうるため)
        clean_store_postgres_password = (
            store_postgres_password.strip()
            if isinstance(store_postgres_password, str)
            else ""
        )

        clean_job_db_path = _require_nonempty_str(job_db_path, "job.db_path", errors)

        clean_webhook_enabled = _require_bool(
            webhook_enabled, "webhook.enabled", errors
        )
        clean_webhook_host = _require_nonempty_str(webhook_host, "webhook.host", errors)
        clean_webhook_port = _require_positive_int(webhook_port, "webhook.port", errors)
        clean_webhook_path = _require_nonempty_str(webhook_path, "webhook.path", errors)
        if clean_webhook_path and not clean_webhook_path.startswith("/"):
            errors.append('webhook.path は "/" から始まる必要があります')

        # Secret TokenはGitLab PATと同じ理由でconfig.tomlではなく.env/環境変数経由で渡す
        # (`config/loader.py`のGITLAB_AI_PLATFORM_WEBHOOK_SECRET)。webhook.enabled=falseの
        # 場合は値が空でも構わない(Webhookサーバー自体を起動しないため)
        clean_webhook_secret_token = (
            webhook_secret_token.strip()
            if isinstance(webhook_secret_token, str)
            else ""
        )
        if clean_webhook_enabled and not clean_webhook_secret_token:
            errors.append(
                "webhook.enabled=true の場合、Secret Token"
                "(.envのGITLAB_AI_PLATFORM_WEBHOOK_SECRET)が必要です"
            )

        # HTTP API(M3-7, docs/adr/0023-http-api.md)。`api`サブコマンドの起動有無で
        # 必須/省略可が切り替わる`webhook_enabled`と異なり、Config自体には有効/無効の
        # フラグを持たない(サブコマンドの実行そのものが有効化を意味するため)。トークンの
        # 必須チェックは`api`サブコマンドの合成ルート(`cli/api_server.py`)側で行う
        clean_api_host = _require_nonempty_str(api_host, "api.host", errors)
        clean_api_port = _require_positive_int(api_port, "api.port", errors)
        # Secret Token/PostgreSQLパスワードと同じ理由でconfig.tomlではなく.env/環境変数経由
        clean_api_token = api_token.strip() if isinstance(api_token, str) else ""

        if errors:
            raise ConfigError("; ".join(errors))

        assert clean_url is not None
        assert isinstance(gitlab_token, str)
        assert isinstance(gitlab_token_mcp, str)
        assert clean_interval is not None
        assert clean_max_parallel is not None
        assert clean_label is not None
        assert clean_workspace_root is not None
        assert clean_workspace_max_disk_mb is not None
        assert clean_runner_log_dir is not None
        assert clean_runner_timeout_seconds is not None
        assert clean_reviews_root is not None
        assert clean_state_db_path is not None
        assert clean_store_backend is not None
        assert clean_store_postgres_host is not None
        assert clean_store_postgres_port is not None
        assert clean_store_postgres_dbname is not None
        assert clean_store_postgres_user is not None
        assert clean_job_db_path is not None
        assert clean_webhook_enabled is not None
        assert clean_webhook_host is not None
        assert clean_webhook_port is not None
        assert clean_webhook_path is not None
        assert clean_api_host is not None
        assert clean_api_port is not None

        # MCP用PATが未設定(空文字列)なら自動実行系と同じトークンにフォールバックする
        # (用途別トークンの分離は運用者が任意に導入できるオプションであり必須ではない)
        clean_gitlab_token_mcp = gitlab_token_mcp.strip() or gitlab_token.strip()

        return cls(
            gitlab_url=clean_url.rstrip("/"),
            gitlab_token=gitlab_token.strip(),
            gitlab_token_mcp=clean_gitlab_token_mcp,
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
            store_backend=clean_store_backend,
            store_postgres_host=clean_store_postgres_host,
            store_postgres_port=clean_store_postgres_port,
            store_postgres_dbname=clean_store_postgres_dbname,
            store_postgres_user=clean_store_postgres_user,
            store_postgres_password=clean_store_postgres_password,
            job_db_path=clean_job_db_path,
            webhook_enabled=clean_webhook_enabled,
            webhook_host=clean_webhook_host,
            webhook_port=clean_webhook_port,
            webhook_path=clean_webhook_path,
            webhook_secret_token=clean_webhook_secret_token,
            api_host=clean_api_host,
            api_port=clean_api_port,
            api_token=clean_api_token,
        )


def _require_nonempty_str(
    value: object, field_name: str, errors: list[str]
) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field_name} は空でない文字列である必要があります")
        return None
    return value.strip()


def _require_bool(value: object, field_name: str, errors: list[str]) -> bool | None:
    if not isinstance(value, bool):
        errors.append(f"{field_name} は真偽値である必要があります")
        return None
    return value


def _require_positive_int(
    value: object, field_name: str, errors: list[str]
) -> int | None:
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
