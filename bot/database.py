"""Database backend switcher with cache invalidation."""
from config import config

if config.DB_BACKEND == "cockroach" and config.COCKROACH_DSN:
    from database_crdb import db as _db
else:
    from database_sqlite import db as _db


class CachedDatabase:

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name == "update_guild_settings":
            def wrapper(guild_id, settings):
                result = attr(guild_id, settings)
                try:
                    from helpers.cache import settings_cache, prefix_cache
                    settings_cache.delete(str(guild_id))
                    if "prefix" in settings:
                        prefix_cache.delete(str(guild_id))
                except ImportError:
                    pass
                return result
            return wrapper
        if name == "set_prefix":
            def wrapper(guild_id, prefix):
                result = attr(guild_id, prefix)
                try:
                    from helpers.cache import prefix_cache, settings_cache
                    prefix_cache.delete(str(guild_id))
                    settings_cache.delete(str(guild_id))
                except ImportError:
                    pass
                return result
            return wrapper
        return attr


db = CachedDatabase(_db)
__all__ = ["db"]
