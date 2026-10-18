"""Playlist engine: one owner of the OBS media input.

* Plays media IDs only (resolved through MediaLibrary, never arbitrary paths).
* Advances only when OBS reports the current media ended.
* Missing files are skipped; a playlist with no playable file fails instead of faking success.
* After a restart the previous playlist is reported (needs_manual_resume) but NOT resumed:
  a LIVE must always be restarted by an operator.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from sarae_agent.config import ConfigStore, paths, read_json, write_json
from sarae_agent.errors import AgentError
from sarae_agent.media.library import MediaLibrary
from sarae_agent.obs.controller import ENDED_STATES, ObsController

log = logging.getLogger("sarae.playback")

MAX_ITEMS = 300
SKIPPABLE = {"MEDIA_FILE_MISSING", "MEDIA_NOT_INDEXED", "MEDIA_OUTSIDE_ALLOWED", "MEDIA_NOT_ALLOWED"}


class PlaybackEngine:
    TICK_SECONDS = 1.0
    SETTLE_SECONDS = 2.5  # ignore "ended/stopped" right after loading a new file

    def __init__(self, config: ConfigStore, library: MediaLibrary, obs: ObsController,
                 emit: Callable[[str, dict], None]):
        self._config = config
        self._library = library
        self._obs = obs
        self._emit = emit
        self._lock = threading.RLock()
        self.media_ids: list[str] = []
        self.index = 0
        self.loop = True
        self.active = False
        self.paused = False
        self.last_error: dict | None = None
        self.skipped: list[str] = []
        self.media_state: str | None = None
        self.started_at: str | None = None
        self._last_transition = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        previous = read_json(paths.playback_state, None)
        self.recovery = previous if isinstance(previous, dict) and previous.get("was_active") else None

    # --- helpers -------------------------------------------------------------------------
    def _input(self) -> str:
        return self._config.get().obs.media_input

    def _persist(self) -> None:
        write_json(paths.playback_state, {"media_ids": self.media_ids, "index": self.index, "loop": self.loop,
                                          "was_active": self.active,
                                          "saved_at": datetime.now(timezone.utc).isoformat()})

    def _changed(self) -> None:
        self._emit("playback.changed", self.status())

    def _load(self, start_index: int, direction: int = 1) -> None:
        """Load the first playable item from start_index (wrapping). Raises if none plays."""
        count = len(self.media_ids)
        errors: list[AgentError] = []
        for step in range(count):
            idx = (start_index + direction * step) % count
            media_id = self.media_ids[idx]
            try:
                path = self._library.resolve(media_id)
            except AgentError as exc:
                if exc.code in SKIPPABLE:
                    errors.append(exc)
                    if media_id not in self.skipped:
                        self.skipped.append(media_id)
                    log.warning("Bỏ qua video %s: %s", media_id[:12], exc.message, extra={"code": exc.code})
                    continue
                raise
            status = self._obs.play_file(self._input(), str(path))
            self.index = idx
            self.media_state = status.get("state")
            self._last_transition = time.monotonic()
            self.last_error = None
            return
        first = errors[0] if errors else AgentError("PLAYLIST_EMPTY", "Playlist trống")
        raise AgentError("NO_PLAYABLE_MEDIA", "Không có video nào trong playlist phát được",
                         {"first_error": first.to_dict(), "skipped": len(errors)})

    def _ensure_ticker(self) -> None:
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        # A fresh Event per ticker: an old ticker that is still winding down cannot be revived.
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="playback-tick", daemon=True)
        self._thread.start()

    # --- commands ------------------------------------------------------------------------
    def play_playlist(self, media_ids: list[str], loop: bool = True) -> dict:
        if not media_ids:
            raise AgentError("PLAYLIST_EMPTY", "Playlist trống")
        if len(media_ids) > MAX_ITEMS:
            raise AgentError("PLAYLIST_TOO_LONG", f"Playlist tối đa {MAX_ITEMS} video")
        if len(set(media_ids)) != len(media_ids):
            raise AgentError("PLAYLIST_DUPLICATE", "Playlist có video trùng")
        for media_id in media_ids:
            if not (isinstance(media_id, str) and len(media_id) == 64
                    and all(c in "0123456789abcdef" for c in media_id)):
                raise AgentError("INVALID_MEDIA_ID", "media_id không hợp lệ")
        with self._lock:
            previous = (list(self.media_ids), self.index, self.loop, self.active)
            self.media_ids = list(media_ids)
            self.loop = bool(loop)
            self.skipped = []
            try:
                self._load(0)
            except AgentError as exc:
                self.media_ids, self.index, self.loop, self.active = previous
                self.last_error = exc.to_dict()
                self._changed()
                raise
            self.active = True
            self.paused = False
            self.recovery = None
            self.started_at = datetime.now(timezone.utc).isoformat()
            self._persist()
            self._ensure_ticker()
            log.info("Bắt đầu playlist %s video (loop=%s)", len(media_ids), self.loop)
            self._changed()
            return self.status()

    def play(self, media_id: str) -> dict:
        return self.play_playlist([media_id], loop=False)

    def next(self) -> dict:
        with self._lock:
            self._require_active()
            following = self.index + 1
            if following >= len(self.media_ids) and not self.loop:
                return self.stop(reason="Đã phát hết playlist")
            self._load(following % len(self.media_ids))
            self.paused = False
            self._persist()
            self._changed()
            return self.status()

    def previous(self) -> dict:
        with self._lock:
            self._require_active()
            self._load((self.index - 1) % len(self.media_ids), direction=-1)
            self.paused = False
            self._persist()
            self._changed()
            return self.status()

    def pause(self) -> dict:
        with self._lock:
            self._require_active()
            if not self.paused:
                self.media_state = self._obs.media_action(self._input(), "pause").get("state")
                self.paused = True
                self._changed()
            return self.status()

    def resume(self) -> dict:
        with self._lock:
            self._require_active()
            if self.paused:
                self.media_state = self._obs.media_action(self._input(), "play").get("state")
                self.paused = False
                self._last_transition = time.monotonic()
                self._changed()
            return self.status()

    def set_loop(self, loop: bool) -> dict:
        with self._lock:
            self.loop = bool(loop)
            if self.active:
                self._persist()
            self._changed()
            return self.status()

    def stop(self, reason: str | None = None) -> dict:
        with self._lock:
            was_active = self.active
            error = None
            if was_active:
                try:
                    self._obs.media_action(self._input(), "stop")
                except AgentError as exc:
                    error = exc
            self.active = False
            self.paused = False
            self.recovery = None
            self._persist()
            self._stop.set()
            if reason:
                log.info("Dừng playlist: %s", reason)
            self._changed()
            if error is not None:
                # Local state is stopped, but OBS did not confirm: report the failure honestly.
                raise AgentError(error.code, f"Đã dừng playlist nhưng OBS không nhận lệnh STOP: {error.message}")
            return self.status()

    def _require_active(self) -> None:
        if not self.active or not self.media_ids:
            raise AgentError("PLAYBACK_NOT_ACTIVE", "Chưa phát playlist nào")

    # --- auto-advance --------------------------------------------------------------------
    def _run(self, stop: threading.Event) -> None:
        while not stop.wait(self.TICK_SECONDS):
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("Lỗi vòng phát playlist")

    def tick(self) -> None:
        with self._lock:
            if not self.active or self.paused:
                return
            if time.monotonic() - self._last_transition < self.SETTLE_SECONDS:
                return
            try:
                state = self._obs.media_status(self._input()).get("state")
            except AgentError as exc:
                if self.last_error is None or self.last_error.get("code") != exc.code:
                    self.last_error = exc.to_dict()
                    self._changed()
                return
            recovered = bool(self.last_error and str(self.last_error.get("code", "")).startswith("OBS_"))
            if recovered:
                self.last_error = None  # OBS answered again: the connection problem is over
            if state != self.media_state or recovered:
                self.media_state = state
                self._changed()
            if state in ENDED_STATES:
                try:
                    self.next()
                except AgentError as exc:
                    self.last_error = exc.to_dict()
                    log.error("Không chuyển được video tiếp theo: %s", exc.message, extra={"code": exc.code})
                    if exc.code == "NO_PLAYABLE_MEDIA":
                        self.active = False
                        self._persist()
                    self._changed()

    # --- status --------------------------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            current = self.media_ids[self.index] if self.media_ids and self.index < len(self.media_ids) else None
            return {
                "active": self.active, "paused": self.paused, "loop": self.loop,
                "media_ids": list(self.media_ids), "index": self.index, "count": len(self.media_ids),
                "current_media_id": current,
                "current_filename": self._library.filename(current) if current else None,
                "media_state": self.media_state, "skipped": list(self.skipped),
                "last_error": self.last_error, "started_at": self.started_at,
                "needs_manual_resume": bool(self.recovery and not self.active),
                "source": self._input(),
            }

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
