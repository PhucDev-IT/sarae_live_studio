"""AgentRuntime: owns every service and the Agent lifecycle state machine.

    stopped --start--> starting --heartbeat ok--> running <--> disconnected
       ^                  |                          |
       |                  +-------fatal------------> error
       +------stopping <--stop----------------------+

Exactly one CloudWorker may exist at a time (guarded by _lock), so there is never a duplicate
heartbeat/poll loop inside one Core, and the Core itself is a per-session singleton (mutex).
Playback, OBS and the CAPTCHA monitor are independent of the Cloud worker: stopping the
worker never interrupts a running LIVE playlist (the engine watchdog keeps advancing videos), and
playback events wait in the outbox until the worker delivers them.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from sarae_agent import IPC_PROTOCOL_VERSION, __version__
from sarae_agent.activation.service import ActivationService
from sarae_agent.cloud.commands import CommandRouter
from sarae_agent.cloud.journal import CommandJournal, CommandLock, EventOutbox
from sarae_agent.cloud.worker import CloudWorker
from sarae_agent.config import ConfigStore, paths
from sarae_agent.diagnostics import export_bundle
from sarae_agent.errors import AgentError
from sarae_agent.logging_setup import event_handler
from sarae_agent.media.library import MediaLibrary
from sarae_agent.monitoring.captcha import CaptchaMonitor
from sarae_agent.obs.controller import ObsController, ObsSettingsStore
from sarae_agent.playback.engine import PlaybackEngine
from sarae_agent.playback.service import PlaybackService
from sarae_agent.voice.tts import VoiceService
from sarae_agent.windows.win32 import hostname

log = logging.getLogger("sarae.runtime")

AGENT_STATES = ("stopped", "starting", "running", "disconnected", "error", "stopping")
# Events that should reach the Cloud quickly instead of waiting for the next heartbeat.
URGENT_EVENTS = {"monitor.captcha_detected", "monitor.captcha_cleared", "playback.changed", "obs.status_changed"}
# Heartbeat `obs` subset still read by the web Agent page (AgentCloudStatus).
OBS_SNAPSHOT_KEYS = ("connected", "running", "paused", "media_state", "product_id", "index", "count",
                     "last_status", "error")


def _without_paths(verification: dict) -> dict:
    """Keep the watched program's file name only: no absolute path of this PC goes to the Cloud."""
    state = dict(verification.get("state") or {})
    info = state.get("window_info")
    if isinstance(info, dict) and info.get("exe_path"):
        state["window_info"] = {k: v for k, v in info.items() if k != "exe_path"}
    return {**verification, "state": state}


class AgentRuntime:
    def __init__(self, emit: Callable[[str, dict], None]):
        self._emit_raw = emit
        self._lock = threading.RLock()
        self._state_lock = threading.Lock()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.state = "stopped"
        self.state_error: dict | None = None
        self.worker: CloudWorker | None = None
        self._closing = threading.Event()

        self.config = ConfigStore()
        self.activation = ActivationService()
        self.library = MediaLibrary(self.config, self.emit)
        self.obs = ObsController(self.config, self.emit)
        self.outbox = EventOutbox()
        self.journal = CommandJournal(paths.command_journal)
        self.command_lock = CommandLock(paths.command_lock)
        self.engine = PlaybackEngine(resolve=self.library.resolve, on_event=self._on_playback_event,
                                     client_factory=self.obs.requests_for,
                                     store=ObsSettingsStore(self.config, self.obs))
        self.playback = PlaybackService(self.engine, self.library, self.emit)
        self.monitor = CaptchaMonitor(self.config, self.emit)
        self.voice = VoiceService(self.config, self.obs)
        self.router = CommandRouter(playback=self.engine, monitor=self.monitor, voice=self.voice)
        self._watchdog = threading.Thread(target=self._watch, name="runtime-watchdog", daemon=True)

    # --- events --------------------------------------------------------------------------
    def emit(self, name: str, data: dict) -> None:
        worker = self.worker
        if worker is not None and name in URGENT_EVENTS:
            worker.poke()
        self._emit_raw(name, data)
        if name == "command.finished":
            try:
                self.playback.changed()  # a Cloud command may have changed playback: refresh the GUI
            except Exception:  # noqa: BLE001
                log.exception("Không cập nhật được trạng thái phát")

    def _on_playback_event(self, event: dict) -> None:
        """PRODUCT_SWITCHED / PLAYBACK_ENDED / ERROR from the engine: queue for the Cloud, refresh the GUI."""
        self.outbox.push(event)
        worker = self.worker
        if worker is not None:
            worker.poke()
        try:
            self.playback.changed()
        except Exception:  # noqa: BLE001 - never break the engine watchdog
            log.exception("Không cập nhật được trạng thái phát")

    def reload_obs_settings(self) -> None:
        """OBS settings changed from the GUI: the engine uses them from its next request."""
        config = self.engine.store.load()
        with self.engine.lock:
            self.engine.config = config
            self.engine.dual_mode = config.dual_mode
            self.engine.slots = []

    def _set_state(self, state: str, error: dict | None = None) -> None:
        # Separate from the lifecycle lock: the worker reports state while stop_agent() joins it.
        with self._state_lock:
            if self.state == state and self.state_error == error:
                return
            self.state, self.state_error = state, error
        log.info("Trạng thái Agent: %s", state, extra={"code": (error or {}).get("code", "-")})
        self.emit("agent.status_changed", self.agent_status())

    # --- startup -------------------------------------------------------------------------
    def boot(self) -> None:
        """Called once after the IPC channel is ready. Never blocks the IPC reader."""
        self._watchdog.start()
        self.engine.start_monitor()
        cfg = self.config.get()
        if cfg.obs.auto_connect:
            threading.Thread(target=self.obs.ensure_connected, name="obs-initial", daemon=True).start()
        if cfg.monitor.autostart:
            try:
                self.monitor.start()
            except AgentError as exc:
                log.warning("Không tự bật được CAPTCHA Monitor: %s", exc.message, extra={"code": exc.code})
        if cfg.agent.autostart and self.activation.identity is not None:
            try:
                self.start_agent()
            except AgentError as exc:
                log.warning("Không tự khởi động Agent: %s", exc.message, extra={"code": exc.code})

    def _watch(self) -> None:
        last_rescan = time.monotonic()
        while not self._closing.wait(5):
            try:
                if self.obs.wanted:
                    self.obs.ensure_connected()
                minutes = self.config.get().media.rescan_minutes
                if self.worker is not None and time.monotonic() - last_rescan >= minutes * 60:
                    last_rescan = time.monotonic()
                    if self.config.get().media.folders:
                        try:
                            self.library.start_scan()
                        except AgentError:
                            pass
            except Exception:  # noqa: BLE001
                log.exception("Watchdog lỗi")

    # --- agent lifecycle -----------------------------------------------------------------
    def start_agent(self) -> dict:
        with self._lock:
            if self.worker is not None and self.worker.is_alive():
                return self.agent_status()
            if self.worker is not None:
                self.worker.stop(timeout=5)  # stopped on a fatal error: release its threads and command lock
                self.worker = None
            identity = self.activation.require()
            cfg = self.config.get()
            api_url = cfg.cloud.api_url
            if not api_url:
                raise AgentError("CLOUD_URL_MISSING", "Chưa cấu hình Cloud API URL")
            if identity.cloud_url and identity.cloud_url != api_url:
                # The credential belongs to the Cloud that issued it; never send it elsewhere.
                raise AgentError("CLOUD_URL_MISMATCH",
                                 "Cloud URL khác với Cloud đã kích hoạt Agent; khôi phục URL cũ hoặc kích hoạt lại",
                                 {"activated_cloud_url": identity.cloud_url})
            worker = CloudWorker(
                agent_id=identity.agent_id, credential=identity.credential, api_url=api_url,
                timeouts={"request": cfg.cloud.request_timeout_seconds, "heartbeat": cfg.cloud.heartbeat_seconds,
                          "poll": cfg.cloud.poll_seconds},
                router=self.router, build_report=self.build_report, library=self.library, outbox=self.outbox,
                journal=self.journal, lock=self.command_lock, emit=self.emit, on_state=self._on_worker_state)
            self.library.dirty = True
            self.router.rescan = worker.rescan
            self._set_state("starting")
            try:
                worker.start()  # AGENT_ALREADY_RUNNING when another process holds the command lock
            except AgentError as exc:
                self.router.rescan = None
                self._set_state("error", exc.to_dict())
                raise
            self.worker = worker
            return self.agent_status()

    def _on_worker_state(self, state: str, error: dict | None) -> None:
        if self.state == "stopping":
            return
        self._set_state(state, error)

    def stop_agent(self) -> dict:
        with self._lock:
            worker = self.worker
            if worker is None:
                self._set_state("stopped")
                return self.agent_status()
            self._set_state("stopping")
            self.router.rescan = None
            stopped = worker.stop(timeout=10)
            self.worker = None
            if not stopped:
                log.error("Cloud worker không dừng trong 10 giây", extra={"code": "WORKER_STOP_TIMEOUT"})
            self._set_state("stopped")
            return self.agent_status()

    def restart_agent(self) -> dict:
        self.stop_agent()
        return self.start_agent()

    # --- views ---------------------------------------------------------------------------
    def agent_status(self) -> dict:
        return {"state": self.state, "error": self.state_error, **self.activation.status()}

    def cloud_status(self) -> dict:
        worker = self.worker
        if worker is None:
            return {"connected": False, "state": "idle", "api_url": self.config.get().cloud.api_url}
        return worker.status()

    def config_view(self) -> dict:
        data = self.config.get().model_dump()
        data["obs"]["password_set"] = self.obs.password_set()
        data["paths"] = {"data_root": str(paths.root), "logs": str(paths.logs),
                         "templates": str(paths.monitor_templates)}
        return data

    def system_status(self) -> dict:
        return {
            "core": {"version": __version__, "ipc_protocol": IPC_PROTOCOL_VERSION, "pid": os.getpid(),
                     "started_at": self.started_at, "data_root": str(paths.root), "hostname": hostname()},
            "agent": self.agent_status(),
            "cloud": self.cloud_status(),
            "obs": self.obs.status(),
            "media": self.library.summary(),
            "playback": self.playback.status(live=False),
            "monitor": self.monitor.status(),
        }

    def build_report(self) -> dict:
        """POST /agents/heartbeat body (backend/app/cloud_agents.py Heartbeat).

        ``playback`` is PlaybackEngine.status() (same shape as GET /api/live/status; the Cloud reconciles its
        LIVE sessions with it). ``obs`` and ``local_playback`` are the older subsets the web Agent page reads.
        """
        identity = self.activation.require()
        play = self.playback.engine_status()
        return {
            "agent_id": identity.agent_id,
            "hostname": hostname()[:160],
            "verification": _without_paths(self.monitor.heartbeat_view()),
            "obs": {k: play.get(k) for k in OBS_SNAPSHOT_KEYS},
            "local_playback": {"active": play.get("running", False), "media_ids": play.get("queue_ids", []),
                               "index": play.get("index", 0), "count": play.get("count", 0),
                               "loop": play.get("loop", False), "paused": play.get("paused", False),
                               "needs_manual_resume": False},
            "playback": play,
        }

    def diagnostics(self) -> dict:
        return export_bundle(self.system_status(), self.config_view(), list(event_handler.recent))

    # --- shutdown ------------------------------------------------------------------------
    def shutdown(self) -> None:
        if self._closing.is_set():
            return
        self._closing.set()
        log.info("Agent Core đang tắt")
        try:
            self.stop_agent()
        except Exception:  # noqa: BLE001
            log.exception("Lỗi khi dừng Cloud worker")
        self.library.cancel_scan()
        try:
            self.monitor.stop(wait=5)
        except Exception:  # noqa: BLE001
            log.exception("Lỗi khi dừng CAPTCHA Monitor")
        self.engine.stop_monitor()
        self.obs.close()
        log.info("Agent Core đã giải phóng tài nguyên")
