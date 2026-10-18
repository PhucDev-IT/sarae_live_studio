"""AgentRuntime: owns every service and the Agent lifecycle state machine.

    stopped --start--> starting --heartbeat ok--> running <--> disconnected
       ^                  |                          |
       |                  +-------fatal------------> error
       +------stopping <--stop----------------------+

Exactly one CloudWorker may exist at a time (guarded by _lock), so there is never a duplicate
heartbeat/poll loop inside one Core, and the Core itself is a per-session singleton (mutex).
Playback, OBS and the CAPTCHA monitor are independent of the Cloud worker: stopping the
worker never interrupts a running LIVE playlist.
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
from sarae_agent.cloud.worker import CloudWorker
from sarae_agent.config import ConfigStore, paths
from sarae_agent.diagnostics import export_bundle
from sarae_agent.errors import AgentError
from sarae_agent.logging_setup import event_handler
from sarae_agent.media.library import MediaLibrary
from sarae_agent.monitoring.captcha import CaptchaMonitor
from sarae_agent.obs.controller import ObsController
from sarae_agent.playlist.engine import PlaybackEngine
from sarae_agent.voice.tts import VoiceService
from sarae_agent.windows.win32 import hostname

log = logging.getLogger("sarae.runtime")

AGENT_STATES = ("stopped", "starting", "running", "disconnected", "error", "stopping")
# Events that should reach the Cloud quickly instead of waiting for the next heartbeat.
URGENT_EVENTS = {"monitor.captcha_detected", "monitor.captcha_cleared", "playback.changed", "obs.status_changed"}


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
        self.playback = PlaybackEngine(self.config, self.library, self.obs, self.emit)
        self.monitor = CaptchaMonitor(self.config, self.emit)
        self.voice = VoiceService(self.config, self.obs)
        self.router = CommandRouter(playback=self.playback, monitor=self.monitor, voice=self.voice,
                                    obs=self.obs, library=self.library)
        self._watchdog = threading.Thread(target=self._watch, name="runtime-watchdog", daemon=True)

    # --- events --------------------------------------------------------------------------
    def emit(self, name: str, data: dict) -> None:
        worker = self.worker
        if worker is not None and name in URGENT_EVENTS:
            worker.poke()
        self._emit_raw(name, data)

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
            self._set_state("starting")
            self.worker = CloudWorker(
                agent_id=identity.agent_id, credential=identity.credential, api_url=api_url,
                timeouts={"request": cfg.cloud.request_timeout_seconds, "heartbeat": cfg.cloud.heartbeat_seconds,
                          "poll": cfg.cloud.poll_seconds},
                router=self.router, build_report=self.build_report, library=self.library,
                emit=self.emit, on_state=self._on_worker_state)
            self.library.dirty = True
            self.worker.start()
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
            "playback": self.playback.status(),
            "monitor": self.monitor.status(),
        }

    def build_report(self) -> dict:
        """Heartbeat payload. Shape kept compatible with the Web dashboard (AgentCloudStatus)."""
        identity = self.activation.require()
        obs = self.obs.status()
        playback = self.playback.status()
        return {
            "agent_id": identity.agent_id,
            "hostname": hostname()[:160],
            "verification": self.monitor.heartbeat_view(),
            "obs": {"connected": obs.get("connected"), "state": obs.get("state"),
                    "media_state": playback.get("media_state"), "running": playback.get("active"),
                    "paused": playback.get("paused"), "error": (obs.get("error") or {}).get("message")
                    if isinstance(obs.get("error"), dict) else obs.get("error"),
                    "obs_version": obs.get("obs_version")},
            "local_playback": {k: playback[k] for k in ("active", "paused", "loop", "index", "count",
                                                         "current_media_id", "current_filename",
                                                         "media_state", "needs_manual_resume", "last_error")},
            "agent": {"version": __version__, "state": self.state, "media": self.library.summary()},
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
        self.playback.shutdown()
        self.obs.close()
        log.info("Agent Core đã giải phóng tài nguyên")
