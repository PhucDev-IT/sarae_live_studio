"""Lifecycle of the Backend's in-memory runtime state.

What lives here (per OWNER or per LIVE desk = OWNER + Agent):
- TikTok chat readers (app/tiktok_chat.readers), AI batch queues and answer caches (app/ai_live_batches.states),
  comment processing logs (app/comment_processing.monitors), AI usage counters (app/ai_service.COUNTS).

Rules:
- Disconnecting the chat stops its reader; an Agent revoked by its owner or the platform loses all its runtime at once.
- Anything idle for IDLE_SECONDS and not busy (reader connected, comments waiting for a batch) is dropped by a janitor.
- Nothing here is a source of truth: settings and credentials are persisted, LIVE sessions and commands are in the DB.

Limits (by design, documented for deployment):
- The state is in the memory of ONE process: run the Backend with a single uvicorn worker. Several workers would each
  have their own readers/queues and a request could hit a worker that does not hold the reader.
- A Backend restart loses it: chat readers must be connected again, pending AI batches and counters are lost.
"""
import logging
import threading
import time

log = logging.getLogger(__name__)
IDLE_SECONDS = 30 * 60
JANITOR_INTERVAL_SECONDS = 5 * 60
_janitor: threading.Thread | None = None


def _reader_busy(reader) -> bool:
    with reader.lock:
        return reader.status in ("connecting", "connected", "stopping")


def _batch_busy(state) -> bool:
    with state.lock:
        return bool(state.pending or state.timers)


def _drop_batch(state):
    if state is None:
        return
    with state.lock:
        for timer in state.timers.values():
            timer.cancel()
        state.timers.clear()
        state.pending.clear()


def release_agent(owner_id: str, agent_id: str):
    """Stop and forget everything the Backend holds in memory for one LIVE desk (e.g. the Agent was revoked)."""
    from app.ai_live_batches import states
    from app.comment_processing import monitors
    from app.per_owner import live_key
    from app.tiktok_chat import readers
    key = live_key(owner_id, agent_id)
    reader = readers.discard(key)
    if reader is not None:
        reader.stop()
    _drop_batch(states.discard(key))
    monitors.discard(key)


def prune(idle_seconds: float = IDLE_SECONDS) -> dict:
    from app.ai_live_batches import states
    from app.comment_processing import monitors
    from app.tiktok_chat import readers
    dropped = {"readers": readers.prune(idle_seconds, _reader_busy),
               "batches": states.prune(idle_seconds, _batch_busy),
               "monitors": monitors.prune(idle_seconds)}
    return {name: len(keys) for name, keys in dropped.items()}


def _loop():
    while True:
        time.sleep(JANITOR_INTERVAL_SECONDS)
        try:
            dropped = prune()
            if any(dropped.values()):
                log.info("runtime janitor dropped %s", dropped)
        except Exception:  # the janitor must never die
            log.exception("runtime janitor failed")


def start_janitor():
    global _janitor
    if _janitor is None:
        _janitor = threading.Thread(target=_loop, name="sarae-runtime-janitor", daemon=True)
        _janitor.start()
