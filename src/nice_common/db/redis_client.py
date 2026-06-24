from functools import lru_cache

from redis import Redis

from nice_common.config import get_settings


@lru_cache
def get_redis() -> Redis:
    s = get_settings()
    return Redis.from_url(s.redis_url, decode_responses=True)
