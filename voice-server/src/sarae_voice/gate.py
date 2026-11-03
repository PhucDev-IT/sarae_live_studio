"""The single GPU lane: every model call (load, prompt, generate) runs on ONE worker thread.

* The FastAPI event loop only awaits; health checks and other requests keep answering during inference.
* At most one model call runs at a time, in arrival order.
* Bounded: more than `max_pending` calls waiting or running -> 429 TTS_BUSY (Retry-After).
* A call that has not *started* within `queue_timeout_s` is withdrawn (503 TTS_QUEUE_TIMEOUT) and never runs late.
  A call that already started cannot be interrupted on the GPU; it finishes and its result is dropped if nobody waits.
"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from sarae_voice.errors import ServiceError


class GpuGate:
    def __init__(self, max_pending: int, queue_timeout_s: float):
        self.max_pending = max_pending
        self.queue_timeout_s = queue_timeout_s
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu")
        self._lock = threading.Lock()
        self._pending = 0
        self._active = 0
        self.max_active_seen = 0          # stays 1: checked by the tests
        self.rejected = 0
        self.timed_out = 0

    @property
    def pending(self) -> int:
        return self._pending

    @property
    def busy(self) -> bool:
        return self._active > 0

    async def run(self, fn: Callable[..., Any], *args: Any, bounded: bool = True,
                  timeout: float | None = None) -> tuple[Any, float, float]:
        """Run fn(*args) on the GPU thread. Returns (result, queue_ms, run_ms)."""
        with self._lock:
            if bounded and self._pending >= self.max_pending:
                self.rejected += 1
                raise ServiceError("TTS_BUSY", f"{self._pending} GPU calls already waiting or running", 429,
                                   retry_after=2)
            self._pending += 1
        enqueued = time.perf_counter()
        times: dict[str, float] = {}

        def job():
            with self._lock:
                self._active += 1
                self.max_active_seen = max(self.max_active_seen, self._active)
            times["start"] = time.perf_counter()
            try:
                return fn(*args)
            finally:
                times["end"] = time.perf_counter()
                with self._lock:
                    self._active -= 1

        future = self._pool.submit(job)
        future.add_done_callback(lambda _: self._release())
        wrapped = asyncio.wrap_future(future)
        limit = self.queue_timeout_s if timeout is None else timeout
        try:
            if limit and limit > 0:
                done, _ = await asyncio.wait({wrapped}, timeout=limit)
                if not done and future.cancel():          # still waiting: withdrawn, never runs
                    self.timed_out += 1
                    raise ServiceError("TTS_QUEUE_TIMEOUT", f"GPU did not start within {limit:.0f} s", 503,
                                       retry_after=2)
            result = await wrapped
        except asyncio.CancelledError:
            future.cancel()        # client went away: drop it if it has not started
            raise
        queue_ms = (times.get("start", enqueued) - enqueued) * 1000
        run_ms = (times.get("end", enqueued) - times.get("start", enqueued)) * 1000
        return result, round(queue_ms, 1), round(run_ms, 1)

    def _release(self) -> None:
        with self._lock:
            self._pending -= 1

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
