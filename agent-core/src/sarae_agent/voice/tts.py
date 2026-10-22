"""Vietnamese TTS for the LIVE (Microsoft Edge neural voices via edge-tts), spoken one sentence at a time.

Sentences come from the Cloud (`tts_speak`, AI replies the Cloud already checked) or the local GUI (`voice.speak`).
They wait in a short queue and a single worker speaks them in order:
  * a sentence older than its `ttl_seconds` is dropped, not spoken late (a viewer's question goes stale fast);
  * the MP3 is cached by (voice, rate, text): a sentence said before plays at once, without the TTS service;
  * it plays on the OBS input named in config.obs.voice_input (a Media Source with audio in the mixer), and the next
    sentence starts only when OBS reports the previous one ended, so voices never overlap;
  * while the voice speaks, the video inputs are lowered by DUCK_DB and restored when the queue is empty. The levels
    to restore are kept on disk too, so a crash mid-sentence is repaired the next time the voice starts.
`clear()` (Cloud `tts_clear`) empties the queue and stops the sentence playing. The video media input is never
played or replaced here, only its volume changed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from typing import Callable

from sarae_agent.config import ConfigStore, paths
from sarae_agent.errors import AgentError
from sarae_agent.obs.controller import ENDED_STATES, ObsController

log = logging.getLogger("sarae.voice")

VOICES = ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural")
MAX_CHARS = 450


class VoiceService:
    GENERATE_TIMEOUT = 25.0
    MAX_QUEUE = 4
    DEFAULT_TTL = 120.0
    DUCK_DB = 12.0
    POLL_SECONDS = 0.15
    MAX_SPEECH_SECONDS = 45.0
    CACHE_KEEP = 300
    SYLLABLES_PER_SECOND = 4.2

    def __init__(self, config: ConfigStore, obs: ObsController,
                 synthesize: Callable[[str, str, str, str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._config = config
        self._obs = obs
        self._synthesize = synthesize or self._edge_tts
        self._clock = clock
        self._cond = threading.Condition()
        self._queue: deque[dict] = deque()
        self._worker: threading.Thread | None = None
        self._closed = False
        self._interrupt = threading.Event()
        self._ducked: dict[str, float] = {}
        self.current: dict | None = None
        self.last: dict | None = None
        self.stats = {"queued": 0, "spoken": 0, "expired": 0, "failed": 0, "cache_hits": 0, "cleared": 0}

    # --- synthesis -----------------------------------------------------------------------
    def _edge_tts(self, text: str, voice: str, output: str, rate: str = "+0%") -> None:
        try:
            import edge_tts
        except ImportError as exc:
            raise AgentError("TTS_UNAVAILABLE", "Thiếu thư viện edge-tts trong Agent Core") from exc

        async def run() -> None:
            await asyncio.wait_for(edge_tts.Communicate(text, voice, rate=rate).save(output),
                                   timeout=self.GENERATE_TIMEOUT)

        try:
            asyncio.run(run())
        except asyncio.TimeoutError as exc:
            raise AgentError("TTS_TIMEOUT", f"Tạo giọng nói quá {int(self.GENERATE_TIMEOUT)} giây",
                             retryable=True) from exc
        except AgentError:
            raise
        except Exception as exc:  # noqa: BLE001 - aiohttp/network errors from the TTS service
            raise AgentError("TTS_FAILED", f"Dịch vụ TTS lỗi: {type(exc).__name__}: {str(exc)[:200]}",
                             retryable=True) from exc

    @property
    def _cache_dir(self):
        path = paths.voice / "cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _audio(self, item: dict):
        """MP3 for the sentence: from the cache, else generated once and kept."""
        key = hashlib.sha256(f"{item['voice']}|{item['rate']}|{item['text']}".encode()).hexdigest()[:32]
        target = self._cache_dir / f"{key}.mp3"
        if target.is_file() and target.stat().st_size > 0:
            os.utime(target)
            self.stats["cache_hits"] += 1
            return target
        temp = self._cache_dir / f"tmp_{uuid.uuid4().hex}.mp3"
        try:
            self._synthesize(item["text"], item["voice"], str(temp), f"{item['rate']:+d}%")
            if not temp.is_file() or temp.stat().st_size == 0:
                raise AgentError("TTS_FAILED", "Dịch vụ TTS không trả về âm thanh", retryable=True)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        self._prune_cache()
        return target

    def _prune_cache(self) -> None:
        files = sorted(self._cache_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        for old in files[:-self.CACHE_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass  # still opened by OBS
        for legacy in paths.voice.glob("voice_*.mp3"):  # one file per sentence, before the cache
            try:
                legacy.unlink()
            except OSError:
                pass

    # --- public API ----------------------------------------------------------------------
    def speak(self, text: str, voice: str = VOICES[0], obs_source: str | None = None, *, rate: int = 0,
              ttl_seconds: float | None = None, duck: bool = True) -> dict:
        """Queue one sentence; returns at once with its place in the queue."""
        text = " ".join((text or "").split())
        if not 1 <= len(text) <= MAX_CHARS:
            raise AgentError("INVALID_PARAMS", f"Nội dung TTS từ 1 đến {MAX_CHARS} ký tự")
        if voice not in VOICES:
            raise AgentError("INVALID_PARAMS", "Giọng đọc không được hỗ trợ")
        if not -30 <= int(rate) <= 50:
            raise AgentError("INVALID_PARAMS", "Tốc độ đọc từ -30% đến +50%")
        source = obs_source or self._config.get().obs.voice_input
        now = self._clock()
        item = {"id": uuid.uuid4().hex[:12], "text": text, "voice": voice, "rate": int(rate), "source": source,
                "duck": bool(duck), "queued_at": now, "deadline": now + (ttl_seconds or self.DEFAULT_TTL)}
        with self._cond:
            if self._closed:
                raise AgentError("TTS_UNAVAILABLE", "Dịch vụ giọng nói đã dừng")
            if len(self._queue) >= self.MAX_QUEUE:
                raise AgentError("TTS_QUEUE_FULL", f"Đang có {len(self._queue)} câu chờ đọc", retryable=True)
            self._queue.append(item)
            self.stats["queued"] += 1
            position = len(self._queue) + (1 if self.current else 0)
            self._ensure_worker()
            self._cond.notify_all()
        return {"status": "queued", "id": item["id"], "position": position, "source": source, "voice": voice,
                "chars": len(text)}

    def clear(self) -> dict:
        """Empty the queue and stop the sentence being spoken (emergency stop)."""
        with self._cond:
            cleared = len(self._queue)
            self._queue.clear()
            self.stats["cleared"] += cleared
            playing = self.current
            self._interrupt.set()
            self._cond.notify_all()
        stopped = False
        if playing is not None:
            try:
                self._obs.media_action(playing["source"], "stop")
                stopped = True
            except AgentError as exc:
                log.warning("Không dừng được nguồn giọng nói: %s", exc.message)
        return {"cleared": cleared, "stopped": stopped}

    def status(self) -> dict:
        with self._cond:
            return {"queue": len(self._queue), "speaking": self.current["text"][:80] if self.current else None,
                    "ducked": bool(self._ducked), "stats": dict(self.stats), "last": self.last}

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._queue.clear()
            self._interrupt.set()
            self._cond.notify_all()
            worker = self._worker
        if worker is not None:
            worker.join(timeout=3)

    # --- worker --------------------------------------------------------------------------
    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, name="voice-worker", daemon=True)
            self._worker.start()

    def _run(self) -> None:
        self._restore_saved()
        while True:
            with self._cond:
                while not self._queue and not self._closed:
                    self._cond.wait(timeout=5.0)
                if self._closed:
                    break
                item = self._queue.popleft()
                self.current = item
                self._interrupt.clear()
            try:
                self._play(item)
            finally:
                with self._cond:
                    idle = not self._queue
                if idle:  # the video comes back before the voice reports itself idle
                    self._restore()
                with self._cond:
                    self.current = None
        self._restore()

    def _play(self, item: dict) -> None:
        base = {"id": item["id"], "chars": len(item["text"]), "voice": item["voice"], "source": item["source"]}
        if self._clock() > item["deadline"]:
            self.stats["expired"] += 1
            self.last = {**base, "status": "expired"}
            log.info("Bỏ câu TTS đã quá hạn (%s ký tự)", len(item["text"]))
            return
        try:
            path = self._audio(item)
            if self._interrupt.is_set():
                return
            if self._clock() > item["deadline"]:
                self.stats["expired"] += 1
                self.last = {**base, "status": "expired"}
                return
            if item["duck"]:
                self._duck()
            started = self._clock()
            media = self._obs.play_file(item["source"], str(path.resolve()), unmute=True)
            self._wait_end(item, started)
            if self._interrupt.is_set():
                self.last = {**base, "status": "stopped"}
                return
            self.stats["spoken"] += 1
            self.last = {**base, "status": "spoken", "media_state": media.get("state"),
                         "duration_ms": int((self._clock() - item["queued_at"]) * 1000)}
            log.info("Đã đọc TTS (%s ký tự) qua nguồn OBS '%s'", len(item["text"]), item["source"])
        except AgentError as exc:
            self.stats["failed"] += 1
            self.last = {**base, "status": "failed", "code": exc.code, "message": exc.message}
            log.warning("TTS lỗi: %s", exc.message, extra={"code": exc.code})
        except Exception as exc:  # noqa: BLE001 - the worker must survive any single sentence
            self.stats["failed"] += 1
            self.last = {**base, "status": "failed", "code": "TTS_FAILED", "message": f"{type(exc).__name__}: {exc}"[:200]}
            log.exception("TTS lỗi không mong đợi")

    def _wait_end(self, item: dict, started: float) -> None:
        """Until OBS reports the sentence ended (or a time limit from its length), so the next one never overlaps."""
        words = len(item["text"].split())
        estimate = words / (self.SYLLABLES_PER_SECOND * (1 + item["rate"] / 100)) + 1.5
        limit = started + min(self.MAX_SPEECH_SECONDS, max(3.0, estimate * 2))
        seen_playing = False
        while self._clock() < limit:
            if self._interrupt.wait(self.POLL_SECONDS):
                return
            try:
                status = self._obs.media_status(item["source"])
            except AgentError:
                return
            state = status.get("state")
            if state == "OBS_MEDIA_STATE_PLAYING":
                seen_playing = True
                duration = status.get("duration_ms")
                if isinstance(duration, (int, float)) and duration > 0:
                    limit = min(limit, started + duration / 1000 + 1.5)
            elif state in ENDED_STATES and (seen_playing or self._clock() - started > 1.0):
                return

    # --- ducking -------------------------------------------------------------------------
    def _video_inputs(self) -> list[str]:
        cfg = self._config.get().obs
        names = [cfg.media_input]
        if cfg.dual_mode:
            names += [cfg.source_a.strip() or cfg.media_input + " A", cfg.source_b.strip() or cfg.media_input + " B"]
        return [n for n in dict.fromkeys(names) if n and n != cfg.voice_input]

    @property
    def _duck_file(self):
        return paths.voice / "ducked.json"

    def _duck(self) -> None:
        if self._ducked:
            return
        for name in self._video_inputs():
            try:
                level = self._obs.input_volume_db(name)
                self._obs.set_input_volume_db(name, level - self.DUCK_DB)
                self._ducked[name] = level
            except AgentError:
                continue  # input missing in this scene collection: nothing to lower
        if self._ducked:
            try:
                self._duck_file.write_text(json.dumps(self._ducked), encoding="utf-8")
            except OSError:
                pass

    def _restore(self) -> None:
        failed = {}
        for name, level in list(self._ducked.items()):
            try:
                self._obs.set_input_volume_db(name, level)
            except AgentError as exc:
                failed[name] = level
                log.warning("Không trả lại âm lượng video '%s': %s", name, exc.message)
        self._ducked.clear()
        try:
            if failed:  # OBS unreachable now: tried again the next time the voice starts
                self._duck_file.write_text(json.dumps(failed), encoding="utf-8")
            else:
                self._duck_file.unlink(missing_ok=True)
        except OSError:
            pass

    def _restore_saved(self) -> None:
        """Levels saved by a voice that stopped mid-sentence (crash, restart): put them back first."""
        try:
            saved = json.loads(self._duck_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(saved, dict):
            self._ducked = {str(k): float(v) for k, v in saved.items() if isinstance(v, (int, float))}
            self._restore()
