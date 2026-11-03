"""FastAPI app: health, status, voice profiles, prompt warmup and synchronous synthesis (Phase 1).

Every model call goes through the single GpuGate thread, so the event loop never blocks on the GPU.
Auth: Bearer tokens from SARAE_VOICE_ORCHESTRATOR_TOKENS / SARAE_VOICE_ADMIN_TOKENS; /health/* needs none.
Logs carry sizes, ids and timings only: never text, transcripts, tokens or audio.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sarae_voice import __version__
from sarae_voice.audio import wav_bytes
from sarae_voice.config import Settings
from sarae_voice.engine import PRESETS, Engine, build_engine
from sarae_voice.errors import ServiceError
from sarae_voice.gate import GpuGate
from sarae_voice.schemas import (ErrorResponse, Live, Ready, SynthesizeRequest, TranscriptUpdate, VoiceList,
                                 VoiceProfile, Warmup)
from sarae_voice.voices import VoiceStore

log = logging.getLogger("sarae.voice.api")
bearer = HTTPBearer(auto_error=False, description="Service token (orchestrator or admin scope)")
ERRORS = {code: {"model": ErrorResponse} for code in (401, 404, 413, 422, 429, 500, 503, 507)}


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings()
    if not settings.orchestrator_token_set and not settings.admin_token_set:
        raise RuntimeError("Set SARAE_VOICE_ORCHESTRATOR_TOKENS (the API never runs without authentication)")
    engine = engine or build_engine(settings)
    gate = GpuGate(settings.max_pending, settings.queue_timeout_s)
    store = VoiceStore(settings, engine, gate)
    metrics = {"started_at": time.time(), "synth_ok": 0, "synth_failed": 0, "codes": {},
               "inference_ms": deque(maxlen=500), "server_ms": deque(maxlen=500), "queue_ms": deque(maxlen=500)}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # The model loads on the GPU thread in the background: /health/live answers at once, /health/ready
        # reports "loading" until it is done.
        task = asyncio.create_task(gate.run(engine.load, bounded=False, timeout=0))
        yield
        task.cancel()
        gate.shutdown()

    app = FastAPI(title="Sarae Voice Server", version=__version__, lifespan=lifespan,
                  description="G-OmniVoice TTS backend for Sarae Live Studio. Design: docs/TTS_SERVER.md.")
    app.state.settings, app.state.engine, app.state.gate, app.state.store = settings, engine, gate, store

    # --- errors ---------------------------------------------------------------------------------
    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, exc: ServiceError):
        headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else None
        metrics["codes"][exc.code] = metrics["codes"].get(exc.code, 0) + 1
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, exc: RequestValidationError):
        fields = sorted({".".join(str(p) for p in e.get("loc", ())[1:]) or "body" for e in exc.errors()})
        return JSONResponse({"error": {"code": "INVALID_PARAMS", "message": "Invalid: " + ", ".join(fields)}}, 422)

    # --- auth -----------------------------------------------------------------------------------
    def scope(name: str):
        def check(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
            token = creds.credentials if creds else ""
            allowed = settings.admin_token_set | (settings.orchestrator_token_set if name == "orchestrator" else set())
            if token and any(hmac.compare_digest(token, t) for t in allowed):
                return name
            raise ServiceError("TTS_AUTH", "Missing or invalid service token", 401)
        return check

    orchestrator = Depends(scope("orchestrator"))

    def require_ready() -> None:
        if engine.state != "ready":
            raise ServiceError(engine.error["code"] if engine.error else "TTS_NOT_READY",
                               f"Engine is {engine.state}", 503, retry_after=5)

    # --- health ---------------------------------------------------------------------------------
    @app.get("/health/live", response_model=Live, tags=["health"])
    async def live():
        return Live()

    @app.get("/health/ready", response_model=Ready, tags=["health"], responses={503: {"model": Ready}})
    async def ready():
        body = Ready(state=engine.state, error=engine.error, busy=gate.busy, pending=gate.pending)
        return JSONResponse(body.model_dump(), 200 if engine.state == "ready" else 503)

    @app.get("/v1/status", tags=["status"], dependencies=[orchestrator], responses=ERRORS)
    async def status():
        return {"version": __version__, "state": engine.state, "error": engine.error,
                "uptime_s": round(time.time() - metrics["started_at"], 1), "engine": engine.info(),
                "memory": engine.memory(), "limits": {k: getattr(settings, k) for k in (
                    "max_text_chars", "max_wav_bytes", "min_ref_seconds", "max_ref_seconds", "max_pending",
                    "queue_timeout_s", "prompt_ram_cache")},
                "gate": {"pending": gate.pending, "busy": gate.busy, "rejected": gate.rejected,
                         "timed_out": gate.timed_out, "max_active_seen": gate.max_active_seen},
                "prompt_cache": {**store.stats, "ram_entries": len(store._ram)},
                "synthesis": {"ok": metrics["synth_ok"], "failed": metrics["synth_failed"], "errors": metrics["codes"],
                              **{f"{name}_p50": _pct(list(metrics[name]), 0.5) for name in ("inference_ms", "queue_ms", "server_ms")},
                              **{f"{name}_p95": _pct(list(metrics[name]), 0.95) for name in ("inference_ms", "queue_ms", "server_ms")}}}

    # --- voices ---------------------------------------------------------------------------------
    @app.put("/v1/voices/{voice_id}", response_model=VoiceProfile, tags=["voices"], dependencies=[orchestrator],
             responses={201: {"model": VoiceProfile}, **ERRORS})
    async def put_voice(voice_id: str, wav: UploadFile = File(..., description="Reference WAV (1-20 s), kept byte for byte"),
                        transcript: str | None = Form(default=None, description="Optional. Empty = Whisper, like the demo"),
                        name: str | None = Form(default=None, max_length=100),
                        owner_ref: str | None = Form(default=None, max_length=100)):
        """Create or replace a voice and prepare its VoiceClonePrompt. Same WAV + same transcript = unchanged (200)."""
        require_ready()
        data = await wav.read(settings.max_wav_bytes + 1)
        profile, changed = await store.put(voice_id, data, transcript, name, owner_ref)
        return JSONResponse(profile, 201 if changed else 200)

    @app.get("/v1/voices", response_model=VoiceList, tags=["voices"], dependencies=[orchestrator], responses=ERRORS)
    async def list_voices():
        return {"voices": store.list()}

    @app.get("/v1/voices/{voice_id}", response_model=VoiceProfile, tags=["voices"], dependencies=[orchestrator],
             responses=ERRORS)
    async def get_voice(voice_id: str):
        return store.get(voice_id)

    @app.put("/v1/voices/{voice_id}/transcript", response_model=VoiceProfile, tags=["voices"],
             dependencies=[orchestrator], responses=ERRORS)
    async def put_transcript(voice_id: str, body: TranscriptUpdate):
        """Set a manual transcript (rebuilds the prompt) or clear it to return to the stored Whisper text."""
        require_ready()
        profile, _ = await store.set_transcript(voice_id, body.transcript)
        return profile

    @app.delete("/v1/voices/{voice_id}", status_code=204, tags=["voices"], dependencies=[orchestrator],
                responses=ERRORS)
    async def delete_voice(voice_id: str):
        store.delete(voice_id)
        return Response(status_code=204)

    @app.post("/v1/voices/{voice_id}/warmup", response_model=Warmup, tags=["voices"], dependencies=[orchestrator],
              responses=ERRORS)
    async def warmup(voice_id: str):
        """Put the voice's prompt in RAM (from the .pt cache, or rebuilt if the cache does not match)."""
        require_ready()
        _, cache, ms, profile = await store.prompt(voice_id)
        return Warmup(voice_id=voice_id, rev=profile["rev"], cache=cache, ms=ms)

    # --- synthesis ------------------------------------------------------------------------------
    @app.post("/v1/tts/synthesize", tags=["tts"], dependencies=[orchestrator],
              response_class=Response, responses={200: {"content": {"audio/wav": {}},
                                                         "description": "WAV, mono PCM 16-bit, model sample rate"},
                                                  **ERRORS})
    async def synthesize(body: SynthesizeRequest):
        """Short text -> WAV with the voice's cached prompt. Timings and the exact configuration are in headers."""
        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:16]
        require_ready()
        text = " ".join(body.text.split())       # only whitespace is normalized (newlines would be glued by the library)
        if not text:
            raise ServiceError("INVALID_PARAMS", "Empty text", 422)
        if len(text) > settings.max_text_chars:
            raise ServiceError("TEXT_TOO_LONG", f"Text longer than {settings.max_text_chars} characters", 422)
        prompt, cache, prompt_ms, profile = await store.prompt(body.voice_id)
        try:
            audio, queue_ms, run_ms = await gate.run(engine.synthesize, prompt, text, body.preset, body.seed)
        except ServiceError:
            metrics["synth_failed"] += 1
            raise
        data = wav_bytes(audio, engine.sampling_rate)
        audio_ms = round(len(audio) / engine.sampling_rate * 1000, 1)
        server_ms = round((time.perf_counter() - started) * 1000, 1)
        metrics["synth_ok"] += 1
        for name, value in (("inference_ms", run_ms), ("queue_ms", queue_ms), ("server_ms", server_ms)):
            metrics[name].append(value)
        info = engine.info()
        trace = {"request_id": request_id, "voice_id": body.voice_id, "voice_rev": profile["rev"],
                 "prompt_compat": store.compat(profile), "preset": body.preset, "generation_config": PRESETS[body.preset],
                 "language": None, "seed": body.seed, "chars": len(text), "checkpoint": info.get("checkpoint"),
                 "checkpoint_revision": info.get("checkpoint_revision"), "versions": info.get("versions"),
                 "sample_rate": engine.sampling_rate, "audio_ms": audio_ms, "queue_ms": queue_ms,
                 "inference_ms": run_ms, "prompt_cache": cache, "prompt_ms": prompt_ms, "server_ms": server_ms}
        if settings.save_outputs:
            await asyncio.to_thread(_save_output, Path(settings.data_dir), request_id, data, {**trace, "text": text})
        log.info("synthesize id=%s voice=%s chars=%s audio_ms=%s queue_ms=%s inference_ms=%s cache=%s",
                 request_id, body.voice_id, len(text), audio_ms, queue_ms, run_ms, cache)
        headers = {"X-Request-Id": request_id, "X-Voice-Id": body.voice_id, "X-Voice-Rev": profile["rev"],
                   "X-Preset": body.preset, "X-Sample-Rate": str(engine.sampling_rate), "X-Audio-Ms": str(audio_ms),
                   "X-Queue-Ms": str(queue_ms), "X-Inference-Ms": str(run_ms), "X-Prompt-Cache": cache,
                   "X-Prompt-Ms": str(prompt_ms), "X-Server-Ms": str(server_ms),
                   "X-Checkpoint-Revision": str(info.get("checkpoint_revision")),
                   "X-Trace": json.dumps({k: trace[k] for k in ("prompt_compat", "generation_config", "versions")},
                                         separators=(",", ":"))}
        return Response(content=data, media_type="audio/wav", headers=headers)

    return app


def _save_output(root: Path, request_id: str, data: bytes, meta: dict) -> None:
    folder = root / "outputs" / datetime.now(timezone.utc).strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{request_id}.wav").write_bytes(data)
    (folder / f"{request_id}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
