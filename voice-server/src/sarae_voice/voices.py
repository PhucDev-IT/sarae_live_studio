"""Voice profiles and the VoiceClonePrompt cache (docs/TTS_SERVER.md 4.x, 8.1).

On disk, per voice (data/voices/<voice_id>/):
  ref.wav            the uploaded bytes, never modified
  profile.json       metadata, transcript mode and texts, QC report, current revision
  prompts/<compat>.pt + .json   the encoded prompt and what it was built from
`rev`    changes when the WAV or the transcript changes (what Sarae Backend compares).
`compat` = rev + checkpoint revision + omnivoice version + prompt format + preprocessing + ASR model: a prompt file is
used only when its compat matches the running engine, otherwise it is rebuilt from ref.wav.
Default transcript = Whisper (the demo's empty Reference Text). Its result is stored, so a rebuild for a new compat
uses the stored text instead of running Whisper again (bit-identical to the Whisper path for clips <= 20 s, Phase 0).
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import shutil
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import soundfile as sf

from sarae_voice.audio import reference_qc
from sarae_voice.config import Settings
from sarae_voice.engine import Engine
from sarae_voice.errors import ServiceError
from sarae_voice.gate import GpuGate

log = logging.getLogger("sarae.voice.voices")
VOICE_ID = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
MAX_TRANSCRIPT = 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def _write_atomic(path: Path, data: bytes | str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    if isinstance(data, str):
        tmp.write_text(data, encoding="utf-8")
    else:
        tmp.write_bytes(data)
    os.replace(tmp, path)


def clean_transcript(text: str | None) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    if not text:
        return None
    if len(text) > MAX_TRANSCRIPT:
        raise ServiceError("INVALID_PARAMS", f"Transcript longer than {MAX_TRANSCRIPT} characters", 422)
    return text


class VoiceStore:
    def __init__(self, settings: Settings, engine: Engine, gate: GpuGate):
        self.settings = settings
        self.engine = engine
        self.gate = gate
        self.root = Path(settings.data_dir) / "voices"
        self.root.mkdir(parents=True, exist_ok=True)
        self._ram: OrderedDict[str, tuple[str, Any]] = OrderedDict()   # compat -> (voice_id, prompt)
        self._locks: dict[str, asyncio.Lock] = {}
        self.stats = {"ram_hit": 0, "disk_hit": 0, "encoded": 0, "disk_rejected": 0}

    # --- reading ---------------------------------------------------------------------------------
    def _dir(self, voice_id: str) -> Path:
        if not VOICE_ID.match(voice_id or ""):
            raise ServiceError("INVALID_PARAMS", "voice_id: 3-64 characters A-Z a-z 0-9 _ -", 422)
        return self.root / voice_id

    def get(self, voice_id: str) -> dict:
        path = self._dir(voice_id) / "profile.json"
        if not path.is_file():
            raise ServiceError("VOICE_NOT_FOUND", "Voice not found", 404)
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        out = []
        for path in sorted(self.root.glob("*/profile.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                log.warning("Unreadable profile %s", path.parent.name)
        return out

    def compat(self, profile: dict) -> str:
        basis = {**self.engine.compat_basis(), "rev": profile["rev"], "wav_sha256": profile["wav"]["sha256"],
                 "transcript_mode": profile["transcript"]["mode"], "manual_text": profile["transcript"]["manual_text"]}
        return _sha(json.dumps(basis, sort_keys=True, ensure_ascii=False))[:24]

    # --- writing ---------------------------------------------------------------------------------
    def _lock(self, voice_id: str) -> asyncio.Lock:
        return self._locks.setdefault(voice_id, asyncio.Lock())

    def inspect_wav(self, data: bytes) -> tuple[dict, dict]:
        s = self.settings
        if not data:
            raise ServiceError("INVALID_AUDIO", "Empty file", 422)
        if len(data) > s.max_wav_bytes:
            raise ServiceError("PAYLOAD_TOO_LARGE", f"WAV larger than {s.max_wav_bytes} bytes", 413)
        try:
            info = sf.info(io.BytesIO(data))
            audio, sr = sf.read(io.BytesIO(data), dtype="int16", always_2d=False)
        except Exception as exc:  # noqa: BLE001 - libsndfile raises several types
            raise ServiceError("INVALID_AUDIO", "Not a readable WAV file", 422) from exc
        if info.format != "WAV":
            raise ServiceError("INVALID_AUDIO", f"Only WAV is accepted (got {info.format})", 422)
        if info.channels not in (1, 2) or not 8000 <= sr <= 96000:
            raise ServiceError("INVALID_AUDIO", "WAV must be mono or stereo, 8-96 kHz", 422)
        qc = reference_qc(audio, sr, s.min_ref_seconds, s.max_ref_seconds)
        if "duration_out_of_range" in qc["warnings"]:
            raise ServiceError("INVALID_AUDIO", f"Reference must be {s.min_ref_seconds:g}-{s.max_ref_seconds:g} s "
                               f"(got {qc['duration_s']} s)", 422)
        wav = {"sha256": _sha(data), "bytes": len(data), "sample_rate": sr, "channels": info.channels,
               "subtype": info.subtype, "duration_s": qc["duration_s"]}
        return wav, qc

    async def put(self, voice_id: str, data: bytes, transcript: str | None, name: str | None,
                  owner_ref: str | None) -> tuple[dict, bool]:
        """Create or replace a voice. Same WAV and same transcript -> unchanged (idempotent). Returns (profile, changed)."""
        folder = self._dir(voice_id)
        wav, qc = self.inspect_wav(data)
        manual = clean_transcript(transcript)
        async with self._lock(voice_id):
            old = self.get(voice_id) if (folder / "profile.json").is_file() else None
            mode = "manual" if manual else "asr"
            if old and old["wav"]["sha256"] == wav["sha256"] and old["transcript"]["mode"] == mode \
                    and old["transcript"]["manual_text"] == manual:
                if name is not None or owner_ref is not None:
                    old.update({k: v for k, v in (("name", name), ("owner_ref", owner_ref)) if v is not None})
                    _write_atomic(folder / "profile.json", json.dumps(old, ensure_ascii=False, indent=2))
                await self._ensure_prompt(old)
                return self.get(voice_id), False
            folder.mkdir(parents=True, exist_ok=True)
            keep_asr = old["transcript"]["asr_text"] if old and old["wav"]["sha256"] == wav["sha256"] else None
            profile = {
                "voice_id": voice_id, "name": name if name is not None else (old or {}).get("name"),
                "owner_ref": owner_ref if owner_ref is not None else (old or {}).get("owner_ref"),
                "created_at": (old or {}).get("created_at") or _now(), "updated_at": _now(),
                "rev": _sha(f"{wav['sha256']}\n{mode}\n{manual or ''}")[:16],
                "wav": wav, "qc": qc,
                "transcript": {"mode": mode, "manual_text": manual, "asr_text": keep_asr, "ref_text_used": None},
                "prompt": None,
            }
            if not old or old["wav"]["sha256"] != wav["sha256"]:
                _write_atomic(folder / "ref.wav", data)
            self._invalidate(voice_id)
            _write_atomic(folder / "profile.json", json.dumps(profile, ensure_ascii=False, indent=2))
            try:
                await self._ensure_prompt(profile)
            except ServiceError:
                if old is None:          # a new voice that cannot be prepared is not kept half-made
                    shutil.rmtree(folder, ignore_errors=True)
                raise
            return self.get(voice_id), True

    async def set_transcript(self, voice_id: str, transcript: str | None) -> tuple[dict, bool]:
        self.get(voice_id)
        data = (self._dir(voice_id) / "ref.wav").read_bytes()
        return await self.put(voice_id, data, transcript, None, None)

    def delete(self, voice_id: str) -> None:
        folder = self._dir(voice_id)
        if not (folder / "profile.json").is_file():
            raise ServiceError("VOICE_NOT_FOUND", "Voice not found", 404)
        self._invalidate(voice_id)
        shutil.rmtree(folder)

    def _invalidate(self, voice_id: str) -> None:
        for key in [k for k, (vid, _) in self._ram.items() if vid == voice_id]:
            del self._ram[key]
        prompts = self._dir(voice_id) / "prompts"
        if prompts.is_dir():
            shutil.rmtree(prompts, ignore_errors=True)

    # --- prompt cache ----------------------------------------------------------------------------
    async def prompt(self, voice_id: str) -> tuple[Any, str, float, dict]:
        """(prompt, cache, ms, profile) with cache in ram | disk | encoded."""
        profile = self.get(voice_id)
        key = self.compat(profile)
        if key in self._ram:
            self._ram.move_to_end(key)
            self.stats["ram_hit"] += 1
            return self._ram[key][1], "ram", 0.0, profile
        async with self._lock(voice_id):
            profile = self.get(voice_id)
            return await self._ensure_prompt(profile)

    async def _ensure_prompt(self, profile: dict) -> tuple[Any, str, float, dict]:
        voice_id, key = profile["voice_id"], self.compat(profile)
        if key in self._ram:
            self.stats["ram_hit"] += 1
            return self._ram[key][1], "ram", 0.0, profile
        folder = self._dir(voice_id) / "prompts"
        pt, meta_path = folder / f"{key}.pt", folder / f"{key}.json"
        started = time.perf_counter()
        if pt.is_file() and meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("compat") != key or meta.get("wav_sha256") != profile["wav"]["sha256"]:
                    raise ValueError("metadata does not match")
                prompt = self.engine.load_prompt(pt)
                self._remember(key, voice_id, prompt)
                self.stats["disk_hit"] += 1
                return prompt, "disk", round((time.perf_counter() - started) * 1000, 2), profile
            except Exception as exc:  # noqa: BLE001 - a damaged cache file is rebuilt, never trusted
                self.stats["disk_rejected"] += 1
                log.warning("Prompt cache for %s rejected (%s); rebuilding", voice_id, type(exc).__name__)
        t = profile["transcript"]
        ref_text = t["manual_text"] if t["mode"] == "manual" else t["asr_text"]   # None -> Whisper, like the demo
        result, _, run_ms = await self.gate.run(self.engine.create_prompt, self._dir(voice_id) / "ref.wav", ref_text)
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.iterdir():                 # one compat per voice on disk
            old.unlink(missing_ok=True)
        self.engine.save_prompt(result.prompt, pt.with_suffix(".tmp"))
        os.replace(pt.with_suffix(".tmp"), pt)
        meta = {"compat": key, "basis": self.engine.compat_basis(), "voice_id": voice_id, "rev": profile["rev"],
                "wav_sha256": profile["wav"]["sha256"], "transcript_mode": t["mode"], "ref_text_given": ref_text,
                "ref_text_used": result.ref_text, "asr_used": result.asr_used, "tokens": result.tokens,
                "prompt_ms": run_ms, "created_at": _now()}
        _write_atomic(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
        if t["mode"] == "asr" and t["asr_text"] is None:
            t["asr_text"] = result.ref_text
        t["ref_text_used"] = result.ref_text
        profile["prompt"] = {"compat": key, "created_at": meta["created_at"], "prompt_ms": run_ms,
                             "tokens": result.tokens, "asr_used": result.asr_used}
        _write_atomic(self._dir(voice_id) / "profile.json", json.dumps(profile, ensure_ascii=False, indent=2))
        self._remember(key, voice_id, result.prompt)
        self.stats["encoded"] += 1
        log.info("Prompt encoded voice=%s rev=%s asr=%s ms=%.0f", voice_id, profile["rev"], result.asr_used, run_ms)
        return result.prompt, "encoded", run_ms, profile

    def _remember(self, key: str, voice_id: str, prompt: Any) -> None:
        self._ram[key] = (voice_id, prompt)
        self._ram.move_to_end(key)
        while len(self._ram) > self.settings.prompt_ram_cache:
            self._ram.popitem(last=False)
