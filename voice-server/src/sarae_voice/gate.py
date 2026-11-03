"""The single GPU lane (Phase 1) extended into a small priority scheduler (Phase 2).

* ONE dedicated worker thread runs every model call (load, prompt, generate); the event loop only awaits.
* Priority classes: system (model load) < live < preview < prepare < long < benchmark (lower runs first).
* Bounded queue per class: a full class -> 429 TTS_BUSY with Retry-After.
* Strict priority, oldest first inside a class. Minimal fairness: once a lower-class call has waited
  `starvation_s`, it gets one turn after every `fair_every` higher-class starts. A burst of live work therefore
  delays live by at most one short lower-class call per `fair_every` live calls, and nothing waits forever behind a
  constant stream of higher work (deadlines still apply). Calls are never pre-empted once started.
* Deadline: a call whose deadline passed is never started (TTS_EXPIRED). `run(..., timeout=)` withdraws a call that
  has not started in time (TTS_QUEUE_TIMEOUT, Phase 1 behaviour).
* Cancel: a queued call is removed and never runs; a running call is flagged and its result is dropped by the caller.
* `hold(task)` lets admission keep a class waiting (long/benchmark during a LIVE lease) without rejecting it.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import itertools
import logging
import threading
import time
from collections import deque
from typing import Any, Callable

from sarae_voice.errors import ServiceError

log = logging.getLogger("sarae.voice.gate")
CLASSES = {"system": -1, "live": 0, "preview": 1, "prepare": 2, "long": 3, "benchmark": 4}


class Task:
    def __init__(self, fn: Callable, args: tuple, klass: str, seq: int, deadline: float | None, label: str,
                 est_s: float | None, on_start: Callable[["Task"], None] | None):
        self.fn, self.args, self.klass, self.seq = fn, args, klass, seq
        self.deadline, self.label, self.est_s, self.on_start = deadline, label, est_s, on_start
        self.future: concurrent.futures.Future = concurrent.futures.Future()
        self.state = "queued"                      # queued | running | done | failed | cancelled | expired
        self.enqueued = time.monotonic()
        self.started: float | None = None
        self.finished: float | None = None
        self.cancel_requested = False

    @property
    def queue_ms(self) -> float:
        return round(((self.started or time.monotonic()) - self.enqueued) * 1000, 1)

    @property
    def run_ms(self) -> float:
        if self.started is None:
            return 0.0
        return round(((self.finished or time.monotonic()) - self.started) * 1000, 1)


class GpuGate:
    def __init__(self, max_pending: int | dict[str, int], queue_timeout_s: float, starvation_s: float = 30.0,
                 fair_every: int = 4, hold: Callable[[Task], bool] | None = None):
        if isinstance(max_pending, int):          # Phase 1 form: one bound for every class
            max_pending = {k: max_pending for k in CLASSES}
        self.limits = {k: max_pending.get(k, 1000) for k in CLASSES}
        self.queue_timeout_s = queue_timeout_s
        self.starvation_s = starvation_s
        self.fair_every = fair_every
        self._streak = 0                  # higher-class starts while a lower-class call was starving
        self.fair_turns = 0
        self.hold = hold
        self._cond = threading.Condition()
        self._queue: list[Task] = []
        self._running: Task | None = None
        self._seq = itertools.count()
        self._closed = False
        self._active = 0
        self.max_active_seen = 0          # stays 1: checked by the tests
        self.rejected = 0
        self.timed_out = 0
        self.counts = {k: {"started": 0, "done": 0, "failed": 0, "cancelled": 0, "expired": 0, "rejected": 0}
                       for k in CLASSES}
        self.order: deque[tuple[str, str]] = deque(maxlen=200)    # (class, label) in start order, for tests
        self._thread = threading.Thread(target=self._loop, name="gpu", daemon=True)
        self._thread.start()

    # --- views ----------------------------------------------------------------------------------
    @property
    def pending(self) -> int:
        with self._cond:
            return len(self._queue) + (1 if self._running else 0)

    @property
    def busy(self) -> bool:
        return self._running is not None

    def queued_by_class(self) -> dict[str, int]:
        with self._cond:
            out = {k: 0 for k in CLASSES if k != "system"}
            for t in self._queue:
                out[t.klass] = out.get(t.klass, 0) + 1
            return out

    def running(self) -> dict | None:
        t = self._running
        if t is None:
            return None
        elapsed = time.monotonic() - (t.started or time.monotonic())
        return {"class": t.klass, "label": t.label, "elapsed_s": round(elapsed, 1),
                "estimated_total_s": round(t.est_s, 1) if t.est_s else None,
                "estimated_remaining_s": round(max(0.0, t.est_s - elapsed), 1) if t.est_s else None,
                "cancel_requested": t.cancel_requested}

    # --- submit / cancel -------------------------------------------------------------------------
    def submit(self, fn: Callable, *args: Any, klass: str = "preview", bounded: bool = True,
               deadline: float | None = None, label: str = "", est_s: float | None = None,
               on_start: Callable[[Task], None] | None = None) -> Task:
        if klass not in CLASSES:
            raise ValueError(klass)
        with self._cond:
            if self._closed:
                raise ServiceError("TTS_SHUTTING_DOWN", "Voice server is shutting down", 503, retry_after=10)
            if bounded and sum(1 for t in self._queue if t.klass == klass) >= self.limits[klass]:
                self.rejected += 1
                self.counts[klass]["rejected"] += 1
                raise ServiceError("TTS_BUSY", f"The {klass} queue is full", 429, retry_after=2)
            task = Task(fn, args, klass, next(self._seq), deadline, label, est_s, on_start)
            self._queue.append(task)
            self._cond.notify_all()
            return task

    def cancel(self, task: Task) -> bool:
        """True when the call was still queued (it will never run); False when it is running or finished."""
        with self._cond:
            if task.state == "queued" and task in self._queue:
                self._queue.remove(task)
                task.state = "cancelled"
                self.counts[task.klass]["cancelled"] += 1
                task.future.cancel()
                return True
            if task.state == "running":
                task.cancel_requested = True
            return False

    async def run(self, fn: Callable[..., Any], *args: Any, bounded: bool = True, timeout: float | None = None,
                  klass: str = "preview", deadline: float | None = None, label: str = "",
                  est_s: float | None = None) -> tuple[Any, float, float]:
        """Run fn(*args) on the GPU thread and await it. Returns (result, queue_ms, run_ms)."""
        task = self.submit(fn, *args, klass=klass, bounded=bounded, deadline=deadline, label=label, est_s=est_s)
        wrapped = asyncio.wrap_future(task.future)
        limit = self.queue_timeout_s if timeout is None else timeout
        try:
            if limit and limit > 0:
                deadline_at = time.monotonic() + limit
                while task.state == "queued":
                    done, _ = await asyncio.wait({wrapped}, timeout=max(0.0, deadline_at - time.monotonic()))
                    if done or task.state != "queued":
                        break
                    if self.cancel(task):     # still waiting: withdrawn, never runs
                        self.timed_out += 1
                        raise ServiceError("TTS_QUEUE_TIMEOUT", f"GPU did not start within {limit:.0f} s", 503,
                                           retry_after=2)
            result = await wrapped
        except asyncio.CancelledError:
            if not self.cancel(task):         # client went away: drop it if it has not started
                task.cancel_requested = True
            raise
        return result, task.queue_ms, task.run_ms

    # --- worker ----------------------------------------------------------------------------------
    def _pick(self, now: float) -> Task | None:
        for t in [t for t in self._queue if t.deadline is not None and now > t.deadline]:
            self._queue.remove(t)
            t.state = "expired"
            self.counts[t.klass]["expired"] += 1
            t.future.set_exception(ServiceError("TTS_EXPIRED", "Deadline passed before the GPU was free", 410))
        ready = [t for t in self._queue if not (self.hold and self.hold(t))]
        if not ready:
            return None
        best = min(ready, key=lambda t: (CLASSES[t.klass], t.seq))
        starving = [t for t in ready if CLASSES[t.klass] > CLASSES[best.klass] and now - t.enqueued >= self.starvation_s]
        if not starving:
            self._streak = 0
            return best
        if self._streak >= self.fair_every:
            self._streak = 0
            self.fair_turns += 1
            return min(starving, key=lambda t: t.seq)
        self._streak += 1
        return best

    def _loop(self) -> None:
        while True:
            with self._cond:
                task = self._pick(time.monotonic())
                while task is None:
                    if self._closed and not self._queue:
                        return
                    self._cond.wait(timeout=0.25 if self._queue else 1.0)
                    task = self._pick(time.monotonic())
                self._queue.remove(task)
                task.state, task.started = "running", time.monotonic()
                self._running = task
                self._active += 1
                self.max_active_seen = max(self.max_active_seen, self._active)
                self.counts[task.klass]["started"] += 1
                self.order.append((task.klass, task.label))
            if not task.future.set_running_or_notify_cancel():
                self._finish(task, "cancelled")
                continue
            if task.on_start:
                try:
                    task.on_start(task)
                except Exception:  # noqa: BLE001 - bookkeeping must not stop the GPU lane
                    log.exception("on_start failed")
            try:
                result = task.fn(*task.args)
            except BaseException as exc:  # noqa: BLE001 - handed to the waiter
                task.finished = time.monotonic()
                task.future.set_exception(exc)
                self._finish(task, "failed")
            else:
                task.finished = time.monotonic()
                task.future.set_result(result)
                self._finish(task, "done")

    def _finish(self, task: Task, state: str) -> None:
        with self._cond:
            task.state = state
            task.finished = task.finished or time.monotonic()
            self.counts[task.klass][state] += 1
            self._running = None
            self._active -= 1
            self._cond.notify_all()

    # --- shutdown --------------------------------------------------------------------------------
    def close(self) -> list[Task]:
        """Stop accepting work and withdraw everything queued (returned so the callers can be told)."""
        with self._cond:
            self._closed = True
            dropped = list(self._queue)
            for t in dropped:
                t.state = "cancelled"
                self.counts[t.klass]["cancelled"] += 1
                t.future.cancel()
            self._queue.clear()
            self._cond.notify_all()
            return dropped

    def join(self, timeout: float) -> bool:
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def shutdown(self) -> None:
        self.close()
