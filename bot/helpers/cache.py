"""In-memory cache with TTL for guild settings."""
import time
from typing import Any


class TTLCache:
    def __init__(self, ttl: int = 30):
        self._cache: dict[str, tuple[Any, float]] = {}
        self._ttl = ttl

    def get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        value, expires = entry
        if time.time() > expires:
            del self._cache[key]
            return None
        return value

    def set(self, key: str, value: Any):
        self._cache[key] = (value, time.time() + self._ttl)

    def delete(self, key: str):
        self._cache.pop(key, None)

    def clear(self):
        self._cache.clear()


# Singleton caches
settings_cache = TTLCache(ttl=30)  # Cache guild settings for 30 seconds
prefix_cache = TTLCache(ttl=60)    # Cache prefixes for 60 seconds
