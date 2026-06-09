"""LRU cache for per-session MCP tool name prefix labels."""

import time
from collections import OrderedDict

from src.config.server_config import cfg

# Retry get_me after a transient None; avoids hammering Telegram every list_tools call.
_UNRESOLVED_TTL_SECONDS = 120.0


class AccountPrefixCache:
    """LRU label cache plus time-bounded negative cache for get_me() == None."""

    def __init__(self) -> None:
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._unresolved: dict[str, float] = {}

    def get(self, token: str) -> str | None:
        label = self._cache.get(token)
        if label is None:
            return None
        self._cache.move_to_end(token)
        return label

    def is_unresolved(self, token: str) -> bool:
        marked_at = self._unresolved.get(token)
        if marked_at is None:
            return False
        if time.monotonic() - marked_at >= _UNRESOLVED_TTL_SECONDS:
            del self._unresolved[token]
            return False
        return True

    def remember_unresolved(self, token: str) -> None:
        self._unresolved[token] = time.monotonic()
        self._cache.pop(token, None)

    def put(self, token: str, label: str) -> None:
        max_size = max(1, cfg().max_active_sessions)
        self._unresolved.pop(token, None)
        if token in self._cache:
            self._cache.move_to_end(token)
        elif len(self._cache) >= max_size:
            oldest, _ = self._cache.popitem(last=False)
            self._unresolved.pop(oldest, None)
        self._cache[token] = label

    def clear(self) -> None:
        self._cache.clear()
        self._unresolved.clear()


_account_prefix_cache = AccountPrefixCache()


def clear_account_prefix_cache() -> None:
    """Clear the account-prefix cache (for tests)."""
    _account_prefix_cache.clear()
