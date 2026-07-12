"""Small Redis-backed search cache and rate guard."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import suppress
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from api.config import settings
from api.services.search_utils import normalize_query

logger = logging.getLogger(__name__)
_client: Redis[str] | None = None

_LRU_EVICT_SCRIPT = """
local excess = redis.call('ZCARD', KEYS[1]) - tonumber(ARGV[1])
if excess > 0 then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, excess - 1)
    if #oldest > 0 then
        redis.call('DEL', unpack(oldest))
        redis.call('ZREM', KEYS[1], unpack(oldest))
    end
end
return excess
"""


def _redis() -> Redis[str]:
    global _client
    if _client is None:
        _client = Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_timeout=1.0)
    return _client


def index_generation() -> str:
    try:
        return _redis().get("search:index-generation") or "1"
    except RedisError:
        return "1"


def bump_index_generation() -> None:
    try:
        _redis().incr("search:index-generation")
    except RedisError as exc:
        logger.warning("Could not invalidate search cache: %s", exc)


def make_search_cache_key(query: str, limit: int, book_id: str | None, rerank: bool, generation: str) -> str:
    raw = json.dumps([normalize_query(query), limit, book_id or "", rerank, generation], separators=(",", ":"))
    return "search:v3:" + hashlib.sha256(raw.encode()).hexdigest()


def get_cached(key: str) -> dict[str, Any] | None:
    try:
        value = _redis().get(key)
        return json.loads(value) if value else None
    except (RedisError, json.JSONDecodeError):
        return None


def set_cached(key: str, value: dict[str, Any], ttl: int = 600) -> None:
    serialized = json.dumps(value, separators=(",", ":"))
    if len(serialized.encode()) > 262_144:
        return
    try:
        client = _redis()
        now = time.time()
        pipe = client.pipeline()
        pipe.setex(key, ttl, serialized)
        pipe.zadd("search:lru", {key: now})
        pipe.zremrangebyscore("search:lru", 0, now - ttl)
        pipe.execute()
        client.eval(_LRU_EVICT_SCRIPT, 1, "search:lru", "256")  # type: ignore[no-untyped-call]
    except RedisError as exc:
        logger.warning("Could not cache search response: %s", exc)


def allow_request(client_id: str, limit: int = 60, window_seconds: int = 60) -> bool:
    bucket = f"search:rate:{client_id}"
    try:
        pipe = _redis().pipeline()
        pipe.incr(bucket)
        pipe.expire(bucket, window_seconds, nx=True)
        count, _ = pipe.execute()
        return int(count) <= limit
    except RedisError:
        return True


def acquire_flight(key: str, token: str, ttl: int = 45) -> bool:
    try:
        return bool(_redis().set(f"flight:{key}", token, nx=True, ex=ttl))
    except RedisError:
        return True


def release_flight(key: str, token: str) -> None:
    script = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
    with suppress(RedisError):
        _redis().eval(script, 1, f"flight:{key}", token)  # type: ignore[no-untyped-call]
