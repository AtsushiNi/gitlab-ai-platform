"""HTTP API サーバー(`api`サブコマンド)の合成ルート。

方針(M3-7 [#97](https://github.com/AtsushiNi/gitlab-ai-platform/issues/97)、
`docs/adr/0023-http-api.md`):

- `api/server.py`の`ApiServer`(Job Repositoryのみに依存する薄いHTTP層)を`config`から
  組み立てて起動する。`cli/watch.py`/`cli/dispatcher.py`と同じ「合成ルートで具象実装
  (`SqliteJobRepository`)を組み立て、`stop_event`がセットされるまでブロックする」構成だが、
  このサブコマンドは常駐ループ(claim/poll)を持たず、`ApiServer`はリクエスト受信時のみ
  動くバックグラウンドスレッドのため`run_api_server`自体は`stop_event.wait()`するだけでよい。
- `worker`と同じ理由(ADR-0022)で`ProcessLock`は使わない。複数の`api`プロセスが同一
  `job_db_path`に対して同時に稼働することを妨げない(将来的な水平スケール・ロードバランサ
  配下での複数インスタンス運用を想定)。
- `config.api_token`が空の場合は`ConfigError`を送出して起動を拒否する(`webhook_enabled=true`
  時のSecret Token必須チェックと同じ考え方。`api`はConfig自体に有効/無効フラグを持たず、
  このサブコマンドの実行そのものが有効化を意味するため、必須チェックはConfig.from_rawではなく
  ここで行う。ADR-0023「決定」参照)。
"""

from __future__ import annotations

import threading

from ..api import ApiServer
from ..config import Config, ConfigError
from ..job import SqliteJobRepository
from ..logging_ import get_logger

_logger = get_logger(__name__)


def run_api_server(
    config: Config, *, stop_event: threading.Event | None = None
) -> None:
    """`config`からHTTP APIサーバーを組み立てて起動し、`stop_event`がセットされるまで待つ。

    `config.api_token`が空の場合は`ConfigError`を送出する(無認証での起動を防ぐため)。
    """
    if not config.api_token:
        raise ConfigError(
            "api.token が設定されていません"
            "(.envのGITLAB_AI_PLATFORM_API_TOKENを確認してください)"
        )

    job_repo = SqliteJobRepository(config.job_db_path)
    server = ApiServer(
        job_repo,
        token=config.api_token,
        host=config.api_host,
        port=config.api_port,
    )
    effective_stop_event = stop_event if stop_event is not None else threading.Event()
    server.start()
    try:
        effective_stop_event.wait()
        _logger.info("api.shutdown_requested")
    finally:
        server.stop()
        job_repo.close()


__all__ = ["run_api_server"]
