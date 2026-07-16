"""Search history — recent queries stored in Redis."""

from __future__ import annotations

import json
import logging
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from api.config import settings

logger = logging.getLogger(__name__)
_client: Redis[str] | None = None
_MAX_HISTORY = 20


def _redis() -> Redis[str]:
    global _client
    if _client is None:
        _client = Redis.from_url(
            settings.REDIS_URL, decode_responses=True, socket_timeout=1.0
        )
    return _client


def record_query(query: str, client_id: str = "default") -> None:
    """Record a search query in history."""
    try:
        key = f"search:history:{client_id}"
        entry = json.dumps({"query": query.strip()})
        pipe = _redis().pipeline()
        pipe.lpush(key, entry)
        pipe.ltrim(key, 0, _MAX_HISTORY - 1)
        pipe.expire(key, 86400 * 30)  # 30 days
        pipe.execute()
    except RedisError as exc:
        logger.warning("Could not record search query: %s", exc)


def get_history(client_id: str = "default", limit: int = 10) -> list[dict[str, Any]]:
    """Get recent search queries."""
    try:
        key = f"search:history:{client_id}"
        entries = _redis().lrange(key, 0, limit - 1)
        return [json.loads(e) for e in entries]
    except (RedisError, json.JSONDecodeError):
        return []
