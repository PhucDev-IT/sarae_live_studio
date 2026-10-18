"""Runtime state kept separately for each OWNER (chat readers, AI batch queues, usage counters…)."""
from threading import Lock
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class PerOwner(Generic[T]):
    """Lazily creates one `factory(owner_id)` object per OWNER and returns the same one afterwards."""

    def __init__(self, factory: Callable[[str], T]):
        self._factory = factory
        self._items: dict[str, T] = {}
        self._lock = Lock()

    def __call__(self, owner_id: str) -> T:
        with self._lock:
            item = self._items.get(owner_id)
            if item is None:
                item = self._items[owner_id] = self._factory(owner_id)
            return item


def live_key(owner_id: str, agent_id: str) -> str:
    """Runtime key of one LIVE desk: an OWNER's Windows Agent. Several Agents of an owner can be LIVE at the same time."""
    return f"{owner_id}:{agent_id}"
