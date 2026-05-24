"""LRU cache for per-session MCP tool name prefix labels."""

from collections import OrderedDict

from src.config.server_config import get_config


class AccountPrefixCache:
    """LRU label cache plus negative cache for get_me() == None."""

    def __init__(self) -> None:
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._unresolved: set[str] = set()

    def get(self, token: str) -> str | None:
        label = self._cache.get(token)
        if label is None:
            return None
        self._cache.move_to_end(token)
        return label

    def is_unresolved(self, token: str) -> bool:
        return token in self._unresolved

    def remember_unresolved(self, token: str) -> None:
        self._unresolved.add(token)
        self._cache.pop(token, None)

    def put(self, token: str, label: str) -> None:
        max_size = max(1, get_config().max_active_sessions)
        self._unresolved.discard(token)
        if token in self._cache:
            self._cache.move_to_end(token)
        else:
            if len(self._cache) >= max_size:
                oldest, _ = self._cache.popitem(last=False)
                self._unresolved.discard(oldest)
        self._cache[token] = label

    def clear(self) -> None:
        self._cache.clear()
        self._unresolved.clear()


_account_prefix_cache = AccountPrefixCache()


def clear_account_prefix_cache() -> None:
    """Clear the account-prefix cache (for tests)."""
    _account_prefix_cache.clear()
