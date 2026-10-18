"""Cloud worker: heartbeat, command polling, ACK delivery and media inventory sync.

Delivery semantics (matches backend/app/cloud_agents.py):
  pending --poll--> delivered --ACK accepted--> accepted --ACK running--> running --ACK--> completed|failed
* A command id is executed at most once per process (dedupe set), even if the Cloud
  re-delivers it because the first `accepted` ACK was lost.
* Commands run on one executor thread, in order, so heartbeats never stall behind OBS/TTS.
* Final ACKs are queued and retried with backoff until the Cloud confirms or rejects them.
* Transient failures (offline, timeout, 5xx) -> `disconnected` + exponential backoff.
  Fatal failures (revoked, license expired, bad credential) -> worker stops with `error`.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from sarae_agent.cloud.client import CloudClient, CloudError
from sarae_agent.cloud.commands import CommandRouter
from sarae_agent.errors import as_agent_error
from sarae_agent.logging_setup import correlation_id

log = logging.getLogger("sarae.cloud.worker")


class Backoff:
    def __init__(self, base: float = 2.0, cap: float = 60.0):
        self.base, self.cap, self.attempt = base, cap, 0

    def next(self) -> float:
        delay = min(self.cap, self.base * (2 ** self.attempt))
        self.attempt += 1
        return delay * random.uniform(0.8, 1.2)

    def reset(self) -> None:
        self.attempt = 0


def _result_text(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:1900]
    except (TypeError, ValueError):
        return str(value)[:1900]


class CloudWorker:
    MAX_QUEUED_COMMANDS = 5
    PROGRESS_ACK_SECONDS = 30.0

    def __init__(self, *, agent_id: str, credential: str, api_url: str, timeouts: dict,
                 router: CommandRouter, build_report: Callable[[], dict], library,
                 emit: Callable[[str, dict], None], on_state: Callable[[str, dict | None], None],
                 client_factory: Callable[..., CloudClient] = CloudClient):
        self.agent_id = agent_id
        self._credential = credential
        self.api_url = api_url
        self._timeouts = timeouts
        self._router = router
        self._build_report = build_report
        self._library = library
        self._emit = emit
        self._on_state = on_state
        self._factory = client_factory
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cloud-cmd")
        self._ack_lock = threading.Lock()
        self._acks: deque[dict] = deque()
        self._seen: OrderedDict[str, str] = OrderedDict()
        self._in_flight = 0
        self._poke_heartbeat = True
        self._current: dict | None = None
        self.recent: deque[dict] = deque(maxlen=50)
        self.connection = {"connected": False, "state": "idle", "last_ok": None, "last_error": None,
                           "next_retry_seconds": None, "last_media_sync": None}

    # --- lifecycle -----------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cloud-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> bool:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)
        self._executor.shutdown(wait=False, cancel_futures=True)
        return not (self._thread and self._thread.is_alive())

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def poke(self) -> None:
        """Send a heartbeat as soon as possible (e.g. CAPTCHA detected)."""
        self._poke_heartbeat = True
        self._wake.set()

    def _track(self, delta: int) -> None:
        with self._ack_lock:
            self._in_flight += delta

    # --- connection state ----------------------------------------------------------------
    def _set_connection(self, connected: bool, state: str, error: dict | None = None,
                        retry: float | None = None) -> None:
        changed = (self.connection["connected"], self.connection["state"]) != (connected, state)
        self.connection.update(connected=connected, state=state, next_retry_seconds=retry)
        if connected:
            self.connection["last_ok"] = datetime.now(timezone.utc).isoformat()
            self.connection["last_error"] = None
        elif error:
            self.connection["last_error"] = error
        if changed or error:
            self._emit("cloud.connection_changed", dict(self.connection))

    # --- main loop -----------------------------------------------------------------------
    def _run(self) -> None:
        backoff = Backoff()
        client = None
        next_heartbeat = 0.0
        last_progress = 0.0
        try:
            client = self._factory(self.api_url, timeout=self._timeouts["request"])
            self._set_connection(False, "connecting")
            while not self._stop.is_set():
                try:
                    now = time.monotonic()
                    if self._poke_heartbeat or now >= next_heartbeat:
                        self._poke_heartbeat = False
                        reply = client.heartbeat(self._credential, self._build_report())
                        next_heartbeat = now + self._timeouts["heartbeat"]
                        if not self.connection["connected"]:
                            log.info("Đã kết nối Cloud %s", self.api_url)
                            self._on_state("running", None)
                        self._set_connection(True, "connected")
                        self.connection["server_time"] = reply.get("server_time")
                        backoff.reset()
                    if self._library.dirty:
                        self._sync_media(client)
                    current = self._current
                    if current and time.monotonic() - max(current["started"], last_progress) > self.PROGRESS_ACK_SECONDS:
                        last_progress = time.monotonic()
                        self._queue_ack(current["command_id"], "running", "Đang thực thi", final=False)
                    self._flush_acks(client)
                    if self._in_flight < self.MAX_QUEUED_COMMANDS:
                        command = client.next_command(self._credential, self.agent_id)
                        if command:
                            self._accept(client, command)
                            continue  # poll again immediately: more commands may be queued
                except CloudError as exc:
                    if exc.fatal:
                        log.error("Cloud từ chối Agent: %s", exc.message, extra={"code": exc.code})
                        self._set_connection(False, "rejected", exc.to_dict())
                        self._on_state("error", exc.to_dict())
                        return
                    delay = backoff.next()
                    if self.connection["connected"] or backoff.attempt == 1:
                        log.warning("Mất kết nối Cloud (%s), thử lại sau %.0fs", exc.message, delay,
                                    extra={"code": exc.code})
                    self._set_connection(False, "disconnected", exc.to_dict(), retry=round(delay, 1))
                    self._on_state("disconnected", exc.to_dict())
                    next_heartbeat = 0.0
                    self._sleep(delay)
                    continue
                self._sleep(self._timeouts["poll"])
        except Exception as exc:  # noqa: BLE001
            log.exception("Cloud worker dừng bất thường")
            self._on_state("error", as_agent_error(exc).to_dict())
        finally:
            # Best effort: deliver queued final ACKs before closing.
            if client is not None:
                try:
                    self._flush_acks(client, deadline=time.monotonic() + 3)
                except Exception:  # noqa: BLE001
                    pass
                client.close()
            self._set_connection(False, "stopped")

    def _sleep(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    # --- media inventory -----------------------------------------------------------------
    def _sync_media(self, client: CloudClient) -> None:
        videos = self._library.cloud_inventory()
        self._library.dirty = False
        try:
            reply = client.sync_library(self._credential, self.agent_id, videos)
        except CloudError:
            self._library.dirty = True
            raise
        self.connection["last_media_sync"] = datetime.now(timezone.utc).isoformat()
        log.info("Đã đồng bộ metadata %s video lên Cloud", reply.get("synced", len(videos)))
        self._emit("media.sync_finished", {"synced": reply.get("synced", len(videos)),
                                           "at": self.connection["last_media_sync"]})

    # --- commands ------------------------------------------------------------------------
    def _accept(self, client: CloudClient, command: dict) -> None:
        command_id = str(command.get("command_id") or "")
        kind = str(command.get("kind") or "")
        if not command_id:
            return
        if command_id in self._seen:
            # Re-delivery after a lost ACK: confirm again, never execute twice.
            log.info("Bỏ qua lệnh lặp %s (%s)", command_id, kind, extra={"cid": command_id})
            self._queue_ack(command_id, "accepted", "Đã nhận (lặp)", final=False)
            return
        self._remember(command_id, "accepted")
        try:
            client.ack(self._credential, self.agent_id, command_id, "accepted", "Agent đã nhận lệnh")
        except CloudError as exc:
            if exc.code == "COMMAND_NOT_RUNNING":
                log.warning("Lệnh %s đã hết hạn trên Cloud, không thực thi", command_id,
                            extra={"code": exc.code, "cid": command_id})
                return
            if exc.code != "CLOUD_VALIDATION_ERROR":  # older Cloud: no intermediate states
                if exc.fatal:
                    raise
                log.info("ACK accepted chưa gửi được (%s), vẫn thực thi", exc.code, extra={"cid": command_id})
        entry = {"command_id": command_id, "kind": kind, "status": "accepted",
                 "received_at": datetime.now(timezone.utc).isoformat()}
        self.recent.appendleft(entry)
        self._emit("command.received", dict(entry))
        self._track(1)
        self._executor.submit(self._execute, command_id, kind, command.get("payload") or {}, entry)

    def _execute(self, command_id: str, kind: str, payload: dict, entry: dict) -> None:
        token = correlation_id.set(command_id)
        started = time.monotonic()
        self._current = {"command_id": command_id, "started": started}
        entry["status"] = "running"
        try:
            log.info("Thực thi lệnh Cloud %s", kind)
            result = self._router.execute(kind, payload)
            status, text, error = "succeeded", _result_text(result), None
        except Exception as exc:  # noqa: BLE001
            failure = as_agent_error(exc)
            status, text, error = "failed", _result_text(failure.to_dict()), failure.to_dict()
            log.warning("Lệnh %s thất bại: %s", kind, failure.message, extra={"code": failure.code})
        finally:
            self._current = None
            self._track(-1)
            correlation_id.reset(token)
        duration = int((time.monotonic() - started) * 1000)
        self._remember(command_id, status)
        entry.update(status=status, duration_ms=duration, error=error,
                     finished_at=datetime.now(timezone.utc).isoformat())
        self._queue_ack(command_id, status, text, final=True)
        self._emit("command.finished", dict(entry))
        self.poke()  # report the new playback/monitor state and deliver the ACK promptly

    def _remember(self, command_id: str, status: str) -> None:
        self._seen[command_id] = status
        self._seen.move_to_end(command_id)
        while len(self._seen) > 500:
            self._seen.popitem(last=False)

    def _queue_ack(self, command_id: str, status: str, result: str, *, final: bool) -> None:
        with self._ack_lock:
            self._acks.append({"id": uuid.uuid4().hex, "command_id": command_id, "status": status,
                               "result": result, "final": final, "attempts": 0, "not_before": 0.0})
        self._wake.set()

    def _flush_acks(self, client: CloudClient, deadline: float | None = None) -> None:
        while True:
            with self._ack_lock:
                ready = next((a for a in self._acks if a["not_before"] <= time.monotonic()), None)
            if ready is None or (deadline and time.monotonic() > deadline):
                return
            try:
                client.ack(self._credential, self.agent_id, ready["command_id"], ready["status"], ready["result"])
                drop = True
            except CloudError as exc:
                if exc.fatal:
                    raise
                # Cloud no longer tracks it (expired/finished) or old Cloud rejects intermediate states.
                drop = exc.code in ("COMMAND_NOT_RUNNING", "CLOUD_VALIDATION_ERROR") or not ready["final"]
                if not drop:
                    ready["attempts"] += 1
                    if ready["attempts"] >= 20:
                        log.error("Bỏ ACK %s sau 20 lần thử", ready["command_id"], extra={"code": exc.code})
                        drop = True
                    else:
                        ready["not_before"] = time.monotonic() + min(60, 2 ** ready["attempts"])
                        if exc.code in ("CLOUD_UNREACHABLE", "CLOUD_TIMEOUT"):
                            raise
            if drop:
                with self._ack_lock:
                    try:
                        self._acks.remove(ready)
                    except ValueError:
                        pass

    def status(self) -> dict:
        with self._ack_lock:
            pending_acks = len(self._acks)
        return {**self.connection, "agent_id": self.agent_id, "api_url": self.api_url,
                "pending_acks": pending_acks, "commands_in_flight": self._in_flight,
                "recent_commands": list(self.recent)[:20]}
