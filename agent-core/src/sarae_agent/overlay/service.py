"""OverlayService: shows the active timeline's overlays in OBS, in step with the video the PlaybackEngine plays.

    Cloud snapshot ──fetch──> images cached on this PC ──> PENDING revision
                                                            │ renderer polls /overlay/state, sees the new revision,
                                                            │ loads the scene, preloads every image and the fonts
                                                            ▼
                                     POST /overlay/ready {revision, ok} ──ok──> ACTIVE revision ──> scene item shown
                                                            └─not ok / no answer in 20 s──> PENDING dropped (rollback:
                                                               the previous revision stays, or the overlay stays hidden)

Time: there is no overlay clock. The state is reconciled from what the PlaybackEngine reports (`observe`): session,
media id, position, media state, loop iteration (the engine reports on every watchdog tick, and at once when a video
starts, on a seek, a pause and a resume). An overlay is active when `start_ms <= position < start_ms + duration_ms` on
that media (`shown_at`, the rule of the web preview). The renderer polls this state (no queue of show/hide commands:
a reconnecting renderer simply reads the current state, nothing past is replayed); between two reports it may advance
by the elapsed time only while the media state is PLAYING, at most 1.5 s. A paused video holds its position: the
engine does not report while paused, so the pause flag of the engine keeps that position valid.

Sync: every state carries the scene revision and a clock `seq`; the renderer echoes the revision and seq it drew
(ACK). A new active timeline version (TIMELINE_LOADED of the pin scheduler) asks for the snapshot at once.

Safety: the service has its own thread. Nothing it does can stop or restart the video, the voice or the pin
schedule: every failure ends in "overlay hidden" plus a log line and `status()["error"]`.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from typing import Callable

from sarae_agent.errors import AgentError
from sarae_agent.overlay.cache import OverlayCache
from sarae_agent.overlay.obs_source import URL_MARKER, ObsOverlaySource
from sarae_agent.overlay.server import OverlayServer

log = logging.getLogger("sarae.overlay")

# Resource limits of a snapshot (the Backend enforces the same when a version is approved).
MAX_OVERLAYS = 500
MAX_ANIMATION_MS = 5000
MAX_AT_ONCE = 4


def shown_at(overlays: list[dict], media_id: str | None, position_ms: int) -> list[dict]:
    """Overlays active at `position_ms` of `media_id` (web: overlayModel.isShown), in drawing order."""
    found = [(o.get("layout", {}).get("z_index", 0), i, o) for i, o in enumerate(overlays)
             if o.get("enabled", True) is not False and o.get("media_id") == media_id
             and o["start_ms"] <= position_ms < o["start_ms"] + o["duration_ms"]]
    return [o for _, _, o in sorted(found, key=lambda x: (x[0], x[1]))]


def snapshot_problem(snapshot: dict) -> str | None:
    """Why a snapshot cannot be shown (limits), None when it can."""
    overlays = snapshot.get("overlays") or []
    if len(overlays) > MAX_OVERLAYS:
        return f"quá {MAX_OVERLAYS} overlay"
    for o in overlays:
        if any((o.get(k) or {}).get("duration_ms", 0) > MAX_ANIMATION_MS for k in ("animation_in", "animation_out")):
            return f"hiệu ứng dài hơn {MAX_ANIMATION_MS} ms"
        if not (isinstance(o.get("start_ms"), int) and isinstance(o.get("duration_ms"), int) and o["duration_ms"] > 0):
            return "thời gian overlay không hợp lệ"
    for media_id in {o.get("media_id") for o in overlays}:
        edges = sorted([(o["start_ms"], 1) for o in overlays if o.get("media_id") == media_id and o.get("enabled", True) is not False]
                       + [(o["start_ms"] + o["duration_ms"], -1) for o in overlays
                          if o.get("media_id") == media_id and o.get("enabled", True) is not False], key=lambda e: (e[0], e[1]))
        shown = 0
        for _, step in edges:
            shown += step
            if shown > MAX_AT_ONCE:
                return f"quá {MAX_AT_ONCE} overlay cùng lúc"
    return None


class OverlayService:
    TICK_SECONDS = 2.0
    SYNC_SECONDS = 15.0
    READY_TIMEOUT = 20.0
    RENDERER_LOST = 6.0
    CLOCK_STALE = 5.0
    REFRESH_EVERY = 30.0

    def __init__(self, config, obs_call: Callable[[str, dict | None], dict], obs_connected: Callable[[], bool],
                 video_sources: Callable[[], list[str]], fetch_snapshot: Callable[[int], dict | None],
                 fetch_image: Callable[[str], bytes], cache: OverlayCache, *, clock: Callable[[], float] = time.monotonic,
                 server: OverlayServer | None = None, playback_state: Callable[[], dict] | None = None):
        self._config = config
        self._obs_connected = obs_connected
        self._video_sources = video_sources
        self._fetch_snapshot = fetch_snapshot
        self._fetch_image = fetch_image
        self._cache = cache
        self._now = clock
        self._playback_state = playback_state or (lambda: {})
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self.token = self._token()
        self.source = ObsOverlaySource(obs_call, lambda: self._config.get().overlay.source_name,
                                       lambda: dict(self._config.get().overlay.input_uuids), self._remember_input)
        self.server = server or OverlayServer(self)
        self.session_id: int | None = None
        self.clock: dict | None = None
        self.observed_at: float | None = None
        self.active: dict | None = None
        self.pending: dict | None = None
        self.ready_revision: str | None = None
        self.renderer_seen: float | None = None
        self.renderer_ack: dict | None = None
        self.renderer_build: str | None = None
        self.source_ok = False
        self.source_info: dict = {}
        self.applied_visible: bool | None = None
        self.error: dict | None = None
        self.missing: list[dict] = []
        self.last_sync = 0.0
        self.last_refresh = 0.0
        self._refused: str | None = None
        self._state = "idle"

    # ----- setup -----
    def _token(self) -> str:
        token = self._config.get().overlay.token
        if not token:
            token = secrets.token_urlsafe(24)
            self._config.update({"overlay": {"token": token}})
        return token

    def _remember_input(self, collection: str, uuid: str) -> None:
        uuids = dict(self._config.get().overlay.input_uuids)
        if uuids.get(collection) != uuid:
            uuids[collection] = uuid
            self._config.update({"overlay": {"input_uuids": uuids}})

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.port}/overlay/?t={self.token}&{URL_MARKER}"

    def start(self) -> None:
        if not self._config.get().overlay.enabled or (self._thread and self._thread.is_alive()):
            return
        try:
            self.server.start(self._config.get().overlay.port)
        except OSError as exc:
            self._fail("OVERLAY_PORT_BUSY", f"Không mở được cổng renderer overlay: {exc}")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="overlay-service", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            if self.applied_visible and self._obs_connected():
                self.source.set_visible(False)
        except Exception:  # noqa: BLE001
            pass
        self.server.stop()

    # ----- inputs -----
    def observe(self, session_id: int, media_id: str | None, position_ms: int, media_state: str | None, iteration: int) -> None:
        """Position report of the PlaybackEngine (its watchdog thread): cheap, never raises."""
        with self._lock:
            if session_id != self.session_id:
                self.session_id, self.last_sync = session_id, 0.0
            seq = (self.clock or {}).get("seq", 0) + 1
            self.clock = {"session_id": session_id, "media_id": media_id, "position_ms": int(position_ms),
                          "media_state": media_state, "iteration": int(iteration or 0), "seq": seq}
            self.observed_at = self._now()

    def clear(self, reason: str) -> None:
        """Playback ended: hide at the next tick (the clock is dropped)."""
        with self._lock:
            if self.clock is not None:
                log.info("Overlay: dừng theo video (%s)", reason)
            self.clock, self.observed_at = None, None

    # ----- renderer side (overlay server threads) -----
    def _paused(self) -> bool:
        try:
            state = self._playback_state() or {}
        except Exception:  # noqa: BLE001
            return False
        return bool(state.get("active") and state.get("paused") and state.get("session_id") == self.session_id)

    def _clock_fresh(self, now: float) -> bool:
        """The reported position still describes the picture: recent, or held by a pause of the same session."""
        if self.clock is None or self.observed_at is None:
            return False
        return now - self.observed_at <= self.CLOCK_STALE or self._paused()

    def _clock_view(self, now: float) -> dict | None:
        if self.clock is None or self.observed_at is None:
            return None
        paused = self._paused()
        return {**self.clock, "age_ms": int((now - self.observed_at) * 1000),
                "playing": self.clock.get("media_state") == "OBS_MEDIA_STATE_PLAYING" and not paused}

    def active_ids(self) -> list:
        """Overlays of the active revision shown at the last reported position (status, tests, readiness)."""
        with self._lock:
            if self.active is None or self.clock is None:
                return []
            return [o.get("id") for o in shown_at(self.active["scene"].get("overlays") or [], self.clock.get("media_id"),
                                                    self.clock.get("position_ms", 0))]

    def renderer_state(self, ack: dict | None = None) -> dict:
        """What the renderer draws now (it polls). `ack`: the revision and clock seq it drew last."""
        with self._lock:
            now = self._now()
            self.renderer_seen = now
            if ack and ack.get("revision"):
                self.renderer_ack = {"revision": ack.get("revision"), "seq": ack.get("seq"), "at": now}
            if ack is not None:
                self.renderer_build = ack.get("build") or ""
                # The page outlived an Agent restart and already draws this exact content (the revision is a hash of
                # it): it will not load it again, so its ACK is its READY.
                staged = self.pending or self.active
                if staged and ack["revision"] == staged["revision"] and self.ready_revision != staged["revision"]:
                    log.info("Renderer đã vẽ sẵn revision %s (trang còn từ trước): coi là READY", ack["revision"])
                    self.renderer_ready({"revision": ack["revision"], "ok": True})
            target = self.pending or self.active
            return {"revision": target["revision"] if target else None,
                    "version": target.get("version") if target else None,
                    "show": self._wanted(now, renderer=False), "clock": self._clock_view(now)}

    def scene_for(self, revision: str) -> dict | None:
        with self._lock:
            for staged in (self.pending, self.active):
                if staged and staged["revision"] == revision:
                    return staged["scene"]
            return None

    def renderer_ready(self, body: dict) -> dict:
        revision, ok = body.get("revision"), body.get("ok") is True
        missing = [str(m)[:300] for m in (body.get("missing") or [])][:20]
        with self._lock:
            if self.pending and revision == self.pending["revision"]:
                if ok:
                    self.active, self.pending, self.ready_revision = self.pending, None, revision
                    self.error = None
                    log.info("Overlay READY: phiên bản %s (revision %s, %s overlay)", self.active["version"], revision,
                             self.active["overlay_count"])
                    return {"accepted": True}
                log.error("Renderer không sẵn sàng với revision %s: %s; giữ bản trước", revision, missing)
                self.pending = None
                self._fail("RENDERER_NOT_READY", "Renderer không tải đủ ảnh/phông của overlay mới; giữ overlay trước", {"missing": missing})
                return {"accepted": False}
            if self.active and revision == self.active["revision"]:
                self.ready_revision = revision if ok else None
                if not ok:
                    self._fail("RENDERER_NOT_READY", "Renderer không tải đủ ảnh overlay", {"missing": missing})
                return {"accepted": ok}
            return {"accepted": False}

    def file_path(self, name: str):
        return self._cache.path(name)

    # ----- the loop -----
    def _fail(self, code: str, message: str, detail: dict | None = None) -> None:
        error = {"code": code, "message": message, **({"detail": detail} if detail else {})}
        if error != self.error:
            log.warning("Overlay: %s", message, extra={"code": code})
        self.error = error

    def _wanted(self, now: float, renderer: bool = True) -> bool:
        """Should the overlay be on screen now (False whenever anything is not right)."""
        active = self.active
        if not self._config.get().overlay.enabled or active is None or not active["overlay_count"]:
            return False
        if not self._clock_fresh(now):
            return False
        if active["session_id"] != self.session_id:
            return False
        if renderer and (self.ready_revision != active["revision"] or self.renderer_seen is None
                         or now - self.renderer_seen > self.RENDERER_LOST or not self.source_ok):
            return False
        return True

    def _sync(self, now: float) -> None:
        session_id = self.session_id
        if session_id is None or now - self.last_sync < self.SYNC_SECONDS:
            return
        self.last_sync = now
        try:
            snapshot = self._fetch_snapshot(session_id)
        except Exception as exc:  # noqa: BLE001 - offline: keep what is loaded
            log.warning("Không lấy được snapshot overlay (giữ bản đang có): %s", exc)
            return
        with self._lock:
            if snapshot is None:
                if self.active or self.pending:
                    log.info("Phiên %s không còn overlay đang kích hoạt", session_id)
                self.active = self.pending = None
                return
            current = (self.pending or self.active or {}).get("revision")
            if snapshot.get("revision") in (current, self._refused):
                return
        problem = snapshot_problem(snapshot)
        if problem:
            self._refused = snapshot.get("revision")
            self._fail("SNAPSHOT_REFUSED", f"Snapshot overlay không hợp lệ ({problem}); giữ overlay trước")
            return
        scene, missing = self._cache.localize(snapshot, self._fetch_image, self.token)
        self.missing = missing
        if any("asset_id" in m for m in missing):
            self._fail("ASSET_MISSING", "Không tải được ảnh overlay; giữ overlay trước", {"missing": missing[:10]})
            self.last_sync = now - self.SYNC_SECONDS + 5  # retry soon
            return
        with self._lock:
            self.pending = {"revision": snapshot["revision"], "scene": scene, "session_id": session_id,
                            "version": snapshot.get("version"), "timeline_id": snapshot.get("timeline_id"),
                            "overlay_count": len(snapshot.get("overlays") or []), "since": now}
            log.info("Overlay: chuẩn bị phiên bản %s (revision %s), chờ renderer READY", snapshot.get("version"), snapshot["revision"])

    def _obs(self, now: float, wanted: Callable[[float], bool]) -> None:
        if not self._obs_connected():
            self.source_ok, self.applied_visible = False, None
            return
        if self.session_id is not None or self.applied_visible:
            try:
                self.source_info = self.source.ensure(self.url, self._video_sources())
                self.source_ok = True
                if "created" in self.source_info["changes"] or any(c.startswith(("added:", "item_changed:", "collection"))
                                                                   for c in self.source_info["changes"]):
                    self.applied_visible = None  # new scene items start hidden: apply again
            except AgentError as exc:
                self.source_ok = False
                self._fail(exc.code, exc.message, getattr(exc, "details", None))
        visible = self.source_ok and wanted(now)  # decided after the source check (a reconnect shows it at once)
        if visible != self.applied_visible and (self.source_ok or not visible):
            try:
                self.source.set_visible(visible)
                self.applied_visible = visible
                log.info("Overlay %s trong OBS", "HIỆN" if visible else "ẩn")
            except AgentError as exc:
                self.applied_visible = None
                self._fail(exc.code, exc.message)

    def tick(self) -> None:
        now = self._now()
        if not self._config.get().overlay.enabled:
            if self.applied_visible:
                self._obs(now, lambda _now: False)
            return
        self._sync(now)
        with self._lock:
            if self.pending and now - self.pending["since"] > self.READY_TIMEOUT:
                log.error("Renderer không báo READY sau %ss cho revision %s; giữ bản trước", int(self.READY_TIMEOUT), self.pending["revision"])
                self.pending = None
                self._fail("RENDERER_TIMEOUT", "Renderer không sẵn sàng kịp; giữ overlay trước")
            wanted = self._wanted(now, renderer=False)
        renderer_alive = self.renderer_seen is not None and now - self.renderer_seen <= self.RENDERER_LOST
        expected = getattr(self.server, "build", "")
        if (renderer_alive and expected and self.renderer_build is not None and self.renderer_build != expected
                and self.source_ok and now - self.last_refresh > self.REFRESH_EVERY):
            self.last_refresh = now
            log.info("Trang renderer cũ (%s ≠ %s): tải lại Browser Source", self.renderer_build or "không rõ", expected)
            try:
                self.source.refresh()
            except AgentError as exc:
                self._fail(exc.code, exc.message)
        self._obs(now, self._wanted)
        if wanted and self.source_ok and not renderer_alive and now - self.last_refresh > self.REFRESH_EVERY:
            self.last_refresh = now
            log.warning("Renderer overlay không phản hồi; tải lại Browser Source (video không bị ảnh hưởng)",
                        extra={"code": "RENDERER_LOST"})
            try:
                self.source.refresh()
            except AgentError as exc:
                self._fail(exc.code, exc.message)
        self._log_state(now)

    def _log_state(self, now: float) -> None:
        state = self.status_state(now)
        if state != self._state:
            log.info("Trạng thái overlay: %s → %s", self._state, state)
            self._state = state

    def status_state(self, now: float | None = None) -> str:
        now = self._now() if now is None else now
        if not self._config.get().overlay.enabled:
            return "disabled"
        if self.applied_visible:
            return "showing"
        if self.pending:
            return "loading"
        if self.error and self.session_id is not None:
            return "error"
        return "hidden" if self.active else "idle"

    def request_sync(self) -> None:
        """A new active version (or another reason to re-read the Cloud now): sync at the next tick, at once."""
        with self._lock:
            self.last_sync = 0.0
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.TICK_SECONDS)
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the overlay never takes anything else down
                log.exception("Overlay tick lỗi")

    def status(self) -> dict:
        with self._lock:
            now = self._now()
            active = self.active or {}
            return {"enabled": self._config.get().overlay.enabled, "state": self.status_state(now),
                    "url_port": self.server.port, "session_id": self.session_id,
                    "revision": active.get("revision"), "version": active.get("version"),
                    "overlay_count": active.get("overlay_count", 0),
                    "pending_revision": (self.pending or {}).get("revision"), "ready_revision": self.ready_revision,
                    "renderer_seen_ms": int((now - self.renderer_seen) * 1000) if self.renderer_seen is not None else None,
                    "renderer_ack": {k: v for k, v in (self.renderer_ack or {}).items() if k != "at"} or None,
                    "clock": self._clock_view(now), "active_overlays": self.active_ids(),
                    "visible": bool(self.applied_visible), "source": self.source_info if self.source_ok else None,
                    "missing": self.missing[:10], "error": self.error}
