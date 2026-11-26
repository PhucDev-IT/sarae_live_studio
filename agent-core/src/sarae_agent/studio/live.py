"""LIVE sessions for the local studio: an ordered product lineup played through OBS.

Reuses the Windows Agent's playback runtime as is: ObsController (one shared obs-websocket,
DPAPI password), PlaybackEngine (A/B sources, product queue, "after current" switch,
rotation policy, watchdog) and ObsSettingsStore. Their files live under
``<studio data>/engine`` (SARAE_AGENT_HOME), never in an Agent's own data directory.

Studio does not start a TikTok stream or OBS output: it only drives the Media Source(s)
the operator placed in the OBS scene. A session left "live" by a crash or restart is
marked ended and never resumes on its own.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from sarae_agent.errors import AgentError

from .models import StudioError, identifier
from .service import StudioService

log = logging.getLogger(__name__)
SWITCH_MODES = {"immediate", "after_current"}
OBS_FIELDS = {"host", "port", "password", "clear_password", "media_input", "dual_mode", "source_a", "source_b",
              "auto_connect"}


def engine_home(data_dir: Path) -> Path:
    home = data_dir / "engine"
    home.mkdir(exist_ok=True)
    if home.is_symlink() or home.resolve() != home:
        raise StudioError("UNSAFE_PATH", "The playback engine directory points outside the studio data directory.")
    return home


class StudioLive:
    def __init__(self, service: StudioService, *, emit: Callable[[str, dict], None] = lambda *_: None,
                 obs_client_factory=None):
        self.service = service
        self.emit = emit
        # Agent modules resolve paths from SARAE_AGENT_HOME on every access.
        os.environ["SARAE_AGENT_HOME"] = str(engine_home(service.data_dir))
        from sarae_agent.config import ConfigStore
        from sarae_agent.obs.controller import ObsController, ObsSettingsStore
        from sarae_agent.playback.engine import PlaybackEngine
        self.config = ConfigStore()
        self.obs = ObsController(self.config, self._obs_event, client_factory=obs_client_factory)
        self.engine = PlaybackEngine(resolve=self._resolve, on_event=self._on_event,
                                     client_factory=self.obs.requests_for,
                                     store=ObsSettingsStore(self.config, self.obs))
        self._lock = threading.RLock()
        service.interrupt_live_sessions("core_restart")

    # --- helpers ---------------------------------------------------------------------------
    def _resolve(self, media_id: str) -> Path | None:
        try:
            return self.service.resolve_media(media_id)
        except StudioError:
            return None

    @staticmethod
    def _agent(operation):
        try:
            return operation()
        except StudioError:
            raise
        except AgentError as exc:
            raise StudioError(exc.code, exc.message, getattr(exc, "details", None)) from exc

    def _obs_event(self, name: str, data: dict) -> None:
        self.emit("studio.live.changed", {"reason": name})

    def _live_session(self) -> dict | None:
        sessions = [s for s in self.service.list_live_sessions() if s["status"] == "live"]
        return sessions[0] if sessions else None

    def _require_live(self) -> dict:
        session = self._live_session()
        if session is None or not self.engine.active:
            raise StudioError("SESSION_NOT_LIVE", "Chưa có phiên LIVE nào đang phát.")
        return session

    def _on_event(self, event: dict) -> None:
        """Engine watchdog events: product switched, playback ended, skipped video."""
        session_id = event.get("session_id")
        kind = event.get("type") or "EVENT"
        try:
            if isinstance(session_id, str):
                self.service.record_live_event(session_id, kind, event.get("product_id"), event.get("detail"))
                if kind == "PLAYBACK_ENDED":
                    self.service.set_live_status(session_id, "ended", str((event.get("detail") or {}).get("reason")
                                                                          or "playback_ended")[:60])
        except StudioError:
            log.warning("Could not record a LIVE event: %s", kind)
        self.emit("studio.live.changed", {"reason": kind})

    def _policy(self, raw: object) -> dict:
        from sarae_agent.playback.engine import PlaybackPolicy
        try:
            return PlaybackPolicy.model_validate(raw if raw is not None else {}).model_dump()
        except ValidationError as exc:
            first = exc.errors()[0]
            raise StudioError("VALIDATION_ERROR", f"Invalid playback policy: {first.get('msg')}",
                              {"field": "policy", "path": [str(x) for x in first.get("loc", ())]}) from exc

    def _push_lineup(self, session: dict) -> list[dict]:
        lineup = self.service.product_lineup(session["product_ids"])
        policy = self._policy(session["policy"] or None)
        self._agent(lambda: self.engine.set_policy(session["id"], policy, lineup))
        return lineup

    # --- OBS -------------------------------------------------------------------------------
    def obs_status(self, params: dict | None = None) -> dict:
        status = self.obs.inspect() if self.obs.status().get("connected") else self.obs.status()
        cfg = self.config.get().obs
        return {**status, "dual_mode": cfg.dual_mode, "source_a": cfg.source_a, "source_b": cfg.source_b,
                "dual_sources": self.engine._sources()}

    def obs_configure(self, params: dict) -> dict:
        unknown = set(params) - OBS_FIELDS
        if unknown:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, unknown))})
        if self.engine.active:
            raise StudioError("SESSION_LIVE", "Dừng phiên LIVE trước khi đổi cài đặt OBS.")
        update = {key: params[key] for key in ("host", "port", "media_input", "dual_mode", "source_a", "source_b",
                                                "auto_connect") if key in params}
        password = params.get("password")
        if password is not None and (not isinstance(password, str) or len(password) > 200):
            raise StudioError("VALIDATION_ERROR", "The OBS password is invalid.", {"field": "password"})
        try:
            before = self.config.get().obs
            self.config.update({"obs": update})
        except Exception as exc:  # noqa: BLE001 - pydantic/ConfigError carry the offending field
            raise StudioError("VALIDATION_ERROR", f"Invalid OBS settings: {str(exc)[:160]}",
                              {"field": "obs"}) from exc
        if params.get("clear_password"):
            self.obs.set_password(None)
        elif password:
            self.obs.set_password(password)
        after = self.config.get().obs
        if password or params.get("clear_password") or (before.host, before.port) != (after.host, after.port):
            self.obs.reconnect_later()
        self.engine.config = self.engine.store.load()
        self.engine.dual_mode = self.engine.config.dual_mode
        return self.obs_status()

    def obs_connect(self, params: dict | None = None) -> dict:
        self._agent(self.obs.connect)
        return self.obs_status()

    def obs_disconnect(self, params: dict | None = None) -> dict:
        if self.engine.active:
            raise StudioError("SESSION_LIVE", "Dừng phiên LIVE trước khi ngắt OBS.")
        self.obs.disconnect()
        return self.obs_status()

    # --- sessions --------------------------------------------------------------------------
    def sessions_list(self, params: dict | None = None) -> dict:
        return {"sessions": self.service.list_live_sessions()}

    def sessions_create(self, params: dict) -> dict:
        unknown = set(params) - {"title", "product_ids", "policy"}
        if unknown:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, unknown))})
        return {"session": self.service.save_live_session(None, title=params.get("title"),
                                                          product_ids=params.get("product_ids"),
                                                          policy=self._policy(params.get("policy")))}

    def sessions_update(self, params: dict) -> dict:
        unknown = set(params) - {"id", "title", "product_ids", "policy"}
        if unknown:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, unknown))})
        if not set(params) - {"id"}:
            raise StudioError("VALIDATION_ERROR", "Provide a title, products or policy to update.")
        with self._lock:
            session = self.service.save_live_session(
                params.get("id"), title=params.get("title"), product_ids=params.get("product_ids"),
                policy=self._policy(params["policy"]) if "policy" in params else None)
            if session["status"] == "live" and self.engine.active:
                self._push_lineup(session)
            return {"session": session}

    def sessions_delete(self, params: dict) -> dict:
        if set(params) != {"id"}:
            raise StudioError("VALIDATION_ERROR", "Provide the session id.")
        self.service.delete_live_session(params["id"])
        return {"deleted": True}

    # --- playback control ------------------------------------------------------------------
    def start(self, params: dict) -> dict:
        unknown = set(params) - {"session_id", "product_id"}
        if unknown:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, unknown))})
        with self._lock:
            session = self.service.live_session(params.get("session_id"))
            current = self._live_session()
            if self.engine.active or (current and current["id"] != session["id"]):
                raise StudioError("SESSION_CONFLICT", "Một phiên LIVE đang phát. Hãy dừng trước khi bắt đầu phiên khác.")
            lineup = {item["product_id"]: item["media_ids"] for item in self.service.product_lineup(session["product_ids"])}
            product_id = params.get("product_id")
            if product_id is not None:
                product_id = identifier(product_id, "product_id")
                if product_id not in lineup:
                    raise StudioError("VALIDATION_ERROR", "This product is not in the session.", {"field": "product_id"})
            else:
                product_id = next((pid for pid in session["product_ids"] if lineup.get(pid)), None)
            if product_id is None or not lineup.get(product_id):
                raise StudioError("NO_PLAYABLE_VIDEO", "Chưa có video nào sẵn sàng cho sản phẩm trong phiên. "
                                  "Hãy gắn video vào sản phẩm trong Thư viện video.")
            self._push_lineup(session)
            self._agent(lambda: self.engine.product_play(session["id"], product_id, lineup[product_id], "immediate"))
            self.service.set_live_status(session["id"], "live")
            self.service.record_live_event(session["id"], "STARTED", product_id)
            self.emit("studio.live.changed", {"reason": "started"})
            return self.status()

    def switch(self, params: dict) -> dict:
        unknown = set(params) - {"product_id", "mode"}
        if unknown:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, unknown))})
        mode = params.get("mode", "after_current")
        if mode not in SWITCH_MODES:
            raise StudioError("VALIDATION_ERROR", "mode must be immediate or after_current.", {"field": "mode"})
        with self._lock:
            session = self._require_live()
            product_id = identifier(params.get("product_id"), "product_id")
            if product_id not in session["product_ids"]:
                raise StudioError("VALIDATION_ERROR", "This product is not in the session.", {"field": "product_id"})
            media_ids = self.service.product_lineup([product_id])[0]["media_ids"]
            if not media_ids:
                raise StudioError("NO_PLAYABLE_VIDEO", "Sản phẩm này chưa có video sẵn sàng.")
            self._agent(lambda: self.engine.product_play(session["id"], product_id, media_ids, mode))
            self.service.record_live_event(session["id"], "SWITCH_REQUESTED", product_id, {"mode": mode})
            self.emit("studio.live.changed", {"reason": "switch"})
            return self.status()

    def _control(self, action: str) -> dict:
        with self._lock:
            session = self._require_live()
            self._agent(getattr(self.engine, action))
            if action in {"pause", "resume"}:
                self.service.record_live_event(session["id"], action.upper())
            self.emit("studio.live.changed", {"reason": action})
            return self.status()

    def pause(self, params: dict | None = None) -> dict:
        return self._control("pause")

    def resume(self, params: dict | None = None) -> dict:
        return self._control("resume")

    def next(self, params: dict | None = None) -> dict:
        return self._control("next")

    def previous(self, params: dict | None = None) -> dict:
        return self._control("previous")

    def cancel_pending(self, params: dict | None = None) -> dict:
        return self._control("cancel_pending")

    def stop(self, params: dict | None = None, *, reason: str = "operator") -> dict:
        with self._lock:
            session = self._live_session()
            error = None
            if self.engine.active:
                try:
                    self.engine.stop()
                except AgentError as exc:
                    error = exc  # the engine is stopped either way; OBS may still show the last frame
            if session is not None:
                self.service.set_live_status(session["id"], "ended", reason)
                self.service.record_live_event(session["id"], "STOPPED", None, {"reason": reason})
            self.emit("studio.live.changed", {"reason": "stopped"})
            if error is not None:
                raise StudioError(error.code, "Đã dừng phiên nhưng OBS chưa nhận lệnh dừng: " + error.message)
            return self.status()

    def refresh(self) -> None:
        """After library/product edits: give the running engine the new video lists."""
        with self._lock:
            session = self._live_session()
            if session is not None and self.engine.active:
                try:
                    self._push_lineup(session)
                except StudioError as exc:
                    log.warning("Could not refresh the LIVE lineup: %s", exc.code)

    # --- status ----------------------------------------------------------------------------
    def status(self, params: dict | None = None) -> dict:
        engine = self.engine
        session = self._live_session()
        # Rendering an OBS copy can hold the engine lock; report the last known state meanwhile.
        if engine.lock.acquire(timeout=0.5):
            try:
                snapshot = engine.status() if engine.active else {"connected": self.obs.status().get("connected")}
                metrics = engine.metrics()
                ids, index = list(engine.ids), engine.index
            finally:
                engine.lock.release()
            busy = False
        else:
            snapshot, metrics, ids, index, busy = {}, engine.metrics(), [], 0, True
        current_media = ids[index] if engine.active and 0 <= index < len(ids) else None
        return {
            "active": engine.active, "busy": busy, "paused": bool(metrics.get("paused")),
            "session": session, "product_id": metrics.get("product_id") if engine.active else None,
            "pending_product_id": metrics.get("pending_product_id") if engine.active else None,
            "media_id": current_media, "queue": ids if engine.active else [], "index": index,
            "media_state": snapshot.get("media_state"), "cursor_ms": snapshot.get("media_cursor"),
            "duration_ms": snapshot.get("media_duration"), "obs_connected": bool(snapshot.get("connected")),
            "obs_error": snapshot.get("error"), "last_status": engine.last_status,
            "rotation": metrics.get("rotation"), "session_seconds": metrics.get("session_seconds", 0),
            "errors": metrics.get("error_count", 0), "recoveries": metrics.get("recovery_count", 0),
            "events": self.service.live_events(session["id"], 15) if session else [],
        }

    # --- lifecycle -------------------------------------------------------------------------
    def start_watchdog(self) -> None:
        self.engine.start_monitor()

    def close(self) -> None:
        try:
            self.engine.stop_monitor()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._live_session() is not None or self.engine.active:
                self.stop(reason="core_stopped")
        except Exception:  # noqa: BLE001 - shutdown must continue
            log.warning("Could not stop the LIVE session cleanly on shutdown")
        self.obs.close()
