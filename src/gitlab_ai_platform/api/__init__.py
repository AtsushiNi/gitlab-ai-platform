"""Job Repositoryへの最小限のHTTP API(M3-7、`docs/adr/0023-http-api.md`)。

Job投入(POST /jobs)・状態/結果参照(GET /jobs/<id>)・一覧取得(GET /jobs?status=...、
GET /jobs/dead-letters)を提供する。将来のUI・他ツール連携の口として、`cli/api_server.py`の
`api`サブコマンドから起動される。詳細は`docs/specs/http-api.md`参照。
"""

from .errors import ApiError, InvalidRequestError, InvalidTokenError
from .server import ApiServer

__all__ = [
    "ApiError",
    "ApiServer",
    "InvalidRequestError",
    "InvalidTokenError",
]
