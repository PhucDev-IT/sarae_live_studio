"""LIVE sessions for the local studio: Single Video Timeline, like the dev branch (docs/SINGLE_VIDEO_TIMELINE.md).

A session is a list of library videos played one after another (sequential / random / shuffle, looped or once).
Products are pinned on TikTok at the time markers of each video: the playback position reported by OBS is the clock,
so a pin always matches what the video is showing. The Windows Agent's runtime is reused as is:
- PlaybackEngine ``video_play`` (A/B sources, watchdog, loop iterations) drives OBS;
- TimelineScheduler computes the wanted pin at each position and pins through the TikTok Console
  (ConsolePinExecutor + ConsoleLiveGate on the Studio's Chrome) or, in rehearsal mode, a dry-run executor;
- ObsController / ObsSettingsStore keep the OBS connection and its DPAPI password.
Agent files live under ``<studio data>/engine`` (SARAE_AGENT_HOME), never in an Agent's own data directory.

Studio never starts a TikTok stream or the OBS output. A session left "live" by a crash or restart is marked ended
and never resumes on its own.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable

from sarae_agent.errors import AgentError

from .models import StudioError, identifier
from .service import StudioService

log = logging.getLogger(__name__)
OBS_FIELDS = {"host", "port", "password", "clear_password", "media_input", "dual_mode", "source_a", "source_b",
              "auto_connect"}
PIN_SETTING = "live_pins"
STUDIO_OVERLAY_PORT = 17893
STUDIO_OVERLAY_SOURCE = "Sarae Studio Overlay"
TIMELINE_EVENTS = {"PIN_APPLIED", "PIN_FAILED", "PIN_DRIFT", "LIVE_GATE_CHANGED"}


def engine_home(data_dir: Path) -> Path:
    home = data_dir / "engine"
    home.mkdir(exist_ok=True)
    if home.is_symlink() or home.resolve() != home:
        raise StudioError("UNSAFE_PATH", "The playback engine directory points outside the studio data directory.")
    return home


class _PinExecutor:
    """Rehearsal (dry-run) or the TikTok Console, chosen at each action by the operator's setting."""

    def __init__(self, live: "StudioLive"):
        from sarae_agent.timeline.executors import DryRunPinExecutor
        self.live = live
        self.rehearsal = DryRunPinExecutor()

    @property
    def dry_run(self) -> bool:
        return not self.live.real_pins()

    def _console(self):
        from sarae_agent.timeline.executors import ConsolePinExecutor
        browser = self.live.browser()
        return ConsolePinExecutor(browser, guard=self.live.engine.lock) if browser is not None else None

    def submit(self, action, target, current, done):
        if self.dry_run:
            return self.rehearsal.submit(action, target, current, done)
        console = self._console()
        if console is None:
            done(False, "Chưa kết nối Chrome TikTok LIVE Console")
            return
        console.submit(action, target, current, done)

    def read(self, done):
        if self.dry_run:
            return self.rehearsal.read(done)
        console = self._console()
        if console is None:
            done(False, None, "Chưa kết nối Chrome TikTok LIVE Console")
            return
        console.read(done)


class _LiveGate:
    """Pins only while TikTok LIVE is confirmed on the Console (fail closed); always open in rehearsal."""

    def __init__(self, live: "StudioLive"):
        self.live = live
        self._gate = None
        self._browser = None

    def is_live(self):
        if not self.live.real_pins():
            return True
        return {"LIVE": True, "OFFLINE": False}.get(self.console_status()["state"])

    def console_status(self) -> dict:
        """Observe the real Console even during rehearsal; rehearsal never proves TikTok is live."""
        browser = self.live.browser()
        if browser is None:
            return {"state": "UNKNOWN", "age_seconds": None, "connected": False}
        if browser is not self._browser:
            from sarae_agent.timeline.executors import ConsoleLiveGate
            self._browser, self._gate = browser, ConsoleLiveGate(
                browser, on_change=lambda state, evidence: self.live._timeline_event(
                    {"type": "LIVE_GATE_CHANGED", "session_id": self.live.scheduler.session_id, "product_id": None,
                     "detail": {"state": state}}))
        confirmed = self._gate.is_live()
        return {**self._gate.status(), "state": "LIVE" if confirmed is True else "OFFLINE" if confirmed is False else "UNKNOWN", "connected": True}

    def status(self) -> dict | None:
        return self._gate.status() if self._gate is not None and self.live.real_pins() else None


class StudioLive:
    def __init__(self, service: StudioService, *, emit: Callable[[str, dict], None] = lambda *_: None,
                 obs_client_factory=None, broadcast_checker=None):
        self.service = service
        self.emit = emit
        # Agent modules resolve paths from SARAE_AGENT_HOME on every access.
        os.environ["SARAE_AGENT_HOME"] = str(engine_home(service.data_dir))
        from sarae_agent.config import ConfigStore
        from sarae_agent.obs.controller import ObsController, ObsSettingsStore
        from sarae_agent.playback.engine import PlaybackEngine
        from sarae_agent.timeline.scheduler import TimelineScheduler
        self.config = ConfigStore()
        self.obs = ObsController(self.config, self._obs_event, client_factory=obs_client_factory)
        self.engine = PlaybackEngine(resolve=self._resolve, on_event=self._on_event,
                                     client_factory=self.obs.requests_for,
                                     store=ObsSettingsStore(self.config, self.obs))
        self.gate = _LiveGate(self)
        self.scheduler = TimelineScheduler(_PinExecutor(self), self.gate, emit=self._timeline_event)
        self.engine.on_position = self._on_position
        # Overlays (dev LIVE_OVERLAYS.md §9-10): the Agent's OverlayService and renderer page, fed from local data.
        # Its own port and Browser Source name, so an Agent on the same PC keeps its "Sarae Overlay" untouched.
        from sarae_agent.config import paths
        from sarae_agent.overlay.cache import OverlayCache
        from sarae_agent.overlay.service import OverlayService
        overlay = self.config.get().overlay
        if overlay.port == 17891 or overlay.source_name == "Sarae Overlay":
            self.config.update({"overlay": {"port": STUDIO_OVERLAY_PORT, "source_name": STUDIO_OVERLAY_SOURCE}})
        self.overlay = OverlayService(self.config, self.obs.request, lambda: self.obs.status().get("connected", False),
                                      self._video_sources, self._overlay_snapshot, service.overlay_image,
                                      OverlayCache(paths.overlay_cache),
                                      playback_state=lambda: {"active": self.engine.active, "paused": self.engine.paused,
                                                              "session_id": self.engine.session_id})
        self._lock = threading.RLock()
        from .broadcast import BroadcastWatch, check_room
        self.broadcast = BroadcastWatch(broadcast_checker or check_room)
        # The TikTok Console browser is owned by the TikTok connector (attached by the runtime).
        self.tiktok = None
        # Called with (session, started: bool) when a session starts or ends (the reply desk follows the chat).
        self.session_listeners: list[Callable[[dict, bool], None]] = []
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

    def browser(self):
        browser = getattr(self.tiktok, "_browser", None) if self.tiktok is not None else None
        try:
            return browser if browser is not None and browser.status().get("browser_open") else None
        except Exception:  # noqa: BLE001 - a broken browser counts as not connected
            return None

    def real_pins(self) -> bool:
        return bool(self.service.get_setting(PIN_SETTING, {"real": False}).get("real"))

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

    def _timeline(self, session: dict) -> dict:
        account = self.service.tiktok_account() or {}
        return self.service.timeline_for(session["video_ids"], account.get("user_id") or "")

    def _video_sources(self) -> list[str]:
        """OBS Media Sources the engine plays the video in (the overlay goes above them)."""
        config = self.engine.config
        names = [config.input_name]
        if config.dual_mode:
            names += [config.source_a.strip() or config.input_name + " A", config.source_b.strip() or config.input_name + " B"]
        return names

    def _overlay_snapshot(self, session_id) -> dict | None:
        try:
            session = self.service.live_session(session_id)
        except StudioError:
            return None
        return self.service.overlay_snapshot(session)

    def _session_changed(self, session: dict, started: bool) -> None:
        for listener in list(self.session_listeners):
            try:
                listener(session, started)
            except Exception:  # noqa: BLE001 - a listener never breaks playback
                log.warning("A LIVE session listener failed")

    # --- engine and scheduler callbacks ------------------------------------------------------
    def _on_position(self, report: dict) -> None:
        """Engine watchdog (~1 s): the video position drives the pins."""
        self.scheduler.observe(report["session_id"], report["position_ms"], report["iteration"],
                               media_id=report.get("media_id"))
        try:  # an overlay error never reaches the pins
            self.overlay.observe(report["session_id"], report.get("media_id"), report["position_ms"],
                                 report.get("media_state"), report["iteration"])
        except Exception:  # noqa: BLE001
            log.exception("Overlay did not get the playback position")

    def _on_event(self, event: dict) -> None:
        session_id = event.get("session_id")
        kind = event.get("type") or "EVENT"
        try:
            if isinstance(session_id, str):
                self.service.record_live_event(session_id, kind, event.get("product_id"), event.get("detail"))
                if kind == "PLAYBACK_ENDED":
                    self.scheduler.clear("playback_ended")
                    self.overlay.clear("playback_ended")
                    session = self.service.set_live_status(session_id, "ended", str(
                        (event.get("detail") or {}).get("reason") or "playback_ended")[:60])
                    self._session_changed(session, False)
        except StudioError:
            log.warning("Could not record a LIVE event: %s", kind)
        self.emit("studio.live.changed", {"reason": kind})

    def _timeline_event(self, event: dict) -> None:
        session_id = event.get("session_id")
        kind = event.get("type") or ""
        if kind in TIMELINE_EVENTS and isinstance(session_id, str):
            try:
                self.service.record_live_event(session_id, kind, event.get("product_id"), event.get("detail"))
            except StudioError:
                pass
        self.emit("studio.live.changed", {"reason": kind})

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
        return {"session": self.service.save_live_session(None, params)}

    def sessions_update(self, params: dict) -> dict:
        session_id = params.get("id")
        changes = {key: value for key, value in params.items() if key != "id"}
        with self._lock:
            current = self.service.live_session(session_id)
            playing = current["status"] == "live" and self.engine.active
            if playing and {"video_ids", "video_order"} & changes.keys():
                raise StudioError("SESSION_LIVE", "Dừng phiên trước khi đổi danh sách hoặc thứ tự video.")
            session = self.service.save_live_session(current["id"], changes)
            if playing and "video_loop" in changes:
                with self.engine.lock:
                    self.engine.loop = session["video_loop"]
            if playing and {"overlay_mode", "overlay_every_ms", "overlay_min_gap_ms"} & changes.keys():
                self.overlay.request_sync()  # the new schedule reaches OBS now, not at the next periodic sync
            return {"session": session}

    def sessions_delete(self, params: dict) -> dict:
        if set(params) != {"id"}:
            raise StudioError("VALIDATION_ERROR", "Provide the session id.")
        self.service.delete_live_session(params["id"])
        return {"deleted": True}

    # --- playback control ------------------------------------------------------------------
    def start(self, params: dict) -> dict:
        unknown = set(params) - {"session_id", "media_id"}
        if unknown:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, unknown))})
        with self._lock:
            session = self.service.live_session(params.get("session_id"))
            current = self._live_session()
            if self.engine.active or (current and current["id"] != session["id"]):
                raise StudioError("SESSION_CONFLICT", "Một phiên LIVE đang phát. Hãy dừng trước khi bắt đầu phiên khác.")
            ids = session["video_ids"]
            if not ids:
                raise StudioError("NO_PLAYABLE_VIDEO", "Phiên chưa có video nào. Hãy thêm video vào phiên.")
            missing = [media_id for media_id in ids if self._resolve(media_id) is None]
            if missing:
                raise StudioError("NO_PLAYABLE_VIDEO", f"{len(missing)}/{len(ids)} video của phiên đang thiếu file. "
                                  "Hãy nhập lại hoặc bỏ chúng khỏi phiên.")
            first = params.get("media_id")
            if first is not None:
                first = identifier(first, "media_id")
                if first not in ids:
                    raise StudioError("VALIDATION_ERROR", "This video is not in the session.", {"field": "media_id"})
            self._agent(lambda: self.engine.video_play(session["id"], media_id=first, loop=session["video_loop"],
                                                       media_ids=ids, video_order=session["video_order"]))
            self.scheduler.load(session["id"], self._timeline(session))
            self.overlay.request_sync()
            session = self.service.set_live_status(session["id"], "live")
            self.service.record_live_event(session["id"], "STARTED", None,
                                           {"pins": "real" if self.real_pins() else "rehearsal"})
            self._session_changed(session, True)
            self.emit("studio.live.changed", {"reason": "started"})
            return self.status()

    def _control(self, action: str, *args) -> dict:
        with self._lock:
            session = self._require_live()
            self._agent(lambda: getattr(self.engine, action)(*args))
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

    def seek(self, params: dict) -> dict:
        position = (params or {}).get("position_ms")
        if set(params or {}) != {"position_ms"} or isinstance(position, bool) or not isinstance(position, int) \
                or position < 0:
            raise StudioError("VALIDATION_ERROR", "position_ms must be a position in the video.", {"field": "position_ms"})
        return self._control("seek", position)

    def stop(self, params: dict | None = None, *, reason: str = "operator") -> dict:
        with self._lock:
            session = self._live_session()
            error = None
            # A pin on its way becomes stale: nothing is pinned after the operator stopped.
            self.scheduler.clear(reason)
            self.overlay.clear(reason)
            if self.engine.active:
                try:
                    self.engine.stop()
                except AgentError as exc:
                    error = exc  # the engine is stopped either way; OBS may still show the last frame
            if session is not None:
                session = self.service.set_live_status(session["id"], "ended", reason)
                self.service.record_live_event(session["id"], "STOPPED", None, {"reason": reason})
                self._session_changed(session, False)
            self.emit("studio.live.changed", {"reason": "stopped"})
            if error is not None:
                raise StudioError(error.code, "Đã dừng phiên nhưng OBS chưa nhận lệnh dừng: " + error.message)
            return self.status()

    def configure_pins(self, params: dict) -> dict:
        """Rehearsal (nothing clicked on TikTok) or real pins on the Console."""
        if set(params) != {"real"} or not isinstance(params.get("real"), bool):
            raise StudioError("VALIDATION_ERROR", "real must be true or false.", {"field": "real"})
        changed = params["real"] != self.real_pins()
        self.service.set_setting(PIN_SETTING, {"real": params["real"]})
        session = self._live_session()
        if changed and session is not None and self.engine.active:
            # What is pinned is unknown again in the new mode: read it back before acting.
            self.scheduler.clear("pin_mode_changed")
            self.scheduler.load(session["id"], self._timeline(session))
            self.service.record_live_event(session["id"], "PIN_MODE", None,
                                           {"pins": "real" if params["real"] else "rehearsal"})
        return self.status()

    def refresh(self) -> None:
        """Markers, products or the library changed: the running scheduler gets the new timeline, same position."""
        with self._lock:
            session = self._live_session()
            if session is not None and self.engine.active:
                try:
                    self.scheduler.load(session["id"], self._timeline(session))
                except StudioError as exc:
                    log.warning("Could not refresh the LIVE timeline: %s", exc.code)
                self.overlay.request_sync()

    def current_product_id(self) -> str | None:
        """The product the timeline wants pinned now (AI reply context)."""
        if not self.engine.active:
            return None
        desired = self.scheduler.status().get("desired_product_id")
        return desired if isinstance(desired, str) else None

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
        pins = self.scheduler.status()
        self.broadcast.follow((self.service.tiktok_account() or {}).get("user_name", "").strip().lstrip("@"))
        console = self.gate.console_status()
        names = {}
        markers = []
        if current_media:
            markers = [{"at_ms": m["at_ms"], "action": m["action"], "product_id": m.get("product_id"),
                        "product_name": m.get("product_name")} for m in self.scheduler.markers_of(current_media)]
            names = {m["product_id"]: m["product_name"] for m in markers if m.get("product_id")}
        for key in ("desired_product_id", "actual_product_id"):
            if pins.get(key) and pins[key] not in names:
                try:
                    names[pins[key]] = self.service.product_context(pins[key])["name"]
                except StudioError:
                    pass
        cursor = snapshot.get("media_cursor")
        next_marker = None
        if current_media and isinstance(cursor, (int, float)):
            upcoming = next((m for m in markers if m["at_ms"] > cursor), None)
            if upcoming:
                next_marker = {**upcoming, "media_id": current_media, "next_video": False,
                               "remaining_ms": max(0, upcoming["at_ms"] - cursor)}
            else:
                # The next video's first action is useful even when its exact arrival time is unknown.
                for next_id in ids[index + 1:]:
                    try:
                        upcoming_markers = self.service.dispatch("studio.media.markers.list", {"media_id": next_id})["markers"]
                    except StudioError:
                        continue
                    if upcoming_markers:
                        next_marker = {**upcoming_markers[0], "media_id": next_id, "next_video": True, "remaining_ms": None}
                        break
        return {
            "active": engine.active, "busy": busy, "paused": bool(metrics.get("paused")),
            "session": session, "media_id": current_media, "queue": ids if engine.active else [], "index": index,
            "loop_iteration": metrics.get("loop_iteration", 0),
            "media_state": snapshot.get("media_state"), "cursor_ms": snapshot.get("media_cursor"),
            "duration_ms": snapshot.get("media_duration"), "obs_connected": bool(snapshot.get("connected")),
            "obs_error": snapshot.get("error"), "last_status": engine.last_status,
            "session_seconds": metrics.get("session_seconds", 0),
            "errors": metrics.get("error_count", 0), "recoveries": metrics.get("recovery_count", 0),
            "markers": markers,
            "next_marker": next_marker, "broadcast": self.broadcast.view(), "console_live": console,
            "pins": {"real": self.real_pins(), "chrome_connected": self.browser() is not None,
                     "live": pins.get("live"), "desired_product_id": pins.get("desired_product_id"),
                     "actual_product_id": pins.get("actual_product_id"), "actual_known": pins.get("actual_known"),
                     "actual_tiktok_product_id": pins.get("actual_tiktok_product_id"), "last_read": pins.get("last_read"),
                     "in_flight": pins.get("in_flight"), "last_error": pins.get("last_error"),
                     "last_applied": pins.get("last_applied"), "product_names": names},
            "events": self.service.live_events(session["id"], 15) if session else [],
            "overlay": self._overlay_status(),
        }

    def _overlay_status(self) -> dict:
        try:
            state = self.overlay.status()
        except Exception:  # noqa: BLE001
            return {"state": "error", "error": {"message": "Không đọc được trạng thái overlay"}}
        return {"state": state.get("state"), "visible": state.get("visible"), "overlay_count": state.get("overlay_count"),
                "active_overlays": state.get("active_overlays"), "error": state.get("error"),
                "source_name": self.config.get().overlay.source_name}

    # --- lifecycle -------------------------------------------------------------------------
    def start_watchdog(self) -> None:
        self.broadcast.follow((self.service.tiktok_account() or {}).get("user_name", "").strip().lstrip("@"))
        self.broadcast.start()
        self.engine.start_monitor()
        self.overlay.start()

    def close(self) -> None:
        self.broadcast.close()
        try:
            self.engine.stop_monitor()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._live_session() is not None or self.engine.active:
                self.stop(reason="core_stopped")
        except Exception:  # noqa: BLE001 - shutdown must continue
            log.warning("Could not stop the LIVE session cleanly on shutdown")
        try:
            self.overlay.close()  # hides our overlay; never touches the video
        except Exception:  # noqa: BLE001
            pass
        self.obs.close()
