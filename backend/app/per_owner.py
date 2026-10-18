"""Runtime state kept separately for each OWNER (chat readers, AI batch queues, usage counters…)."""
import time
from threading import Lock
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class PerOwner(Generic[T]):
    """Lazily creates one `factory(key)` object per OWNER (or LIVE desk) and returns the same one afterwards.

    Items remember when they were last used so idle ones can be dropped (see app/runtime.py)."""

    def __init__(self, factory: Callable[[str], T]):
        self._factory = factory
        self._items: dict[str, T] = {}
        self._used: dict[str, float] = {}
        self._lock = Lock()

    def __call__(self, key: str) -> T:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                item = self._items[key] = self._factory(key)
            self._used[key] = time.monotonic()
            return item

    def get(self, key: str) -> T | None:
        """The existing item, without creating one or marking it used."""
        with self._lock:
            return self._items.get(key)

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._items)

    def discard(self, key: str) -> T | None:
        with self._lock:
            self._used.pop(key, None)
            return self._items.pop(key, None)

    def prune(self, idle_seconds: float, busy: Callable[[T], bool] = lambda _item: False) -> list[str]:
        """Drop items unused for `idle_seconds` unless `busy(item)`; returns the dropped keys."""
        limit = time.monotonic() - idle_seconds
        with self._lock:
            dropped = [key for key, used in self._used.items() if used < limit and not busy(self._items[key])]
            for key in dropped:
                del self._items[key], self._used[key]
        return dropped


def live_key(owner_id: str, agent_id: str) -> str:
    """Runtime key of one LIVE desk: an OWNER's Windows Agent. Several Agents of an owner can be LIVE at the same time."""
    return f"{owner_id}:{agent_id}"
