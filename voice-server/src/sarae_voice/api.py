"""FastAPI app: health, status, voice profiles, prompt warmup, synchronous synthesis (Phase 1) and
jobs, LIVE admission, metrics and housekeeping (Phase 2).

Every model call goes through the single GPU worker (gate.py), so the event loop never blocks on the GPU.
Auth: Bearer tokens from SARAE_VOICE_ORCHESTRATOR_TOKENS / SARAE_VOICE_ADMIN_TOKENS; job audio also accepts that
job's read token. /health/* needs none. Logs carry sizes, ids and timings only: never text, transcripts, tokens or audio.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sarae_voice import __version__
from sarae_voice.admission import LiveAdmission
from sarae_voice.audio import wav_bytes
from sarae_voice.config import Settings
from sarae_voice.engine import PRESETS, Engine, build_engine
from sarae_voice.errors import ServiceError
from sarae_voice.gate import GpuGate
from sarae_voice.jobs import JobManager, _utc
from sarae_voice.metrics import Metrics
from sarae_voice.results import ResultCache
from sarae_voice.schemas import (ErrorResponse, JobCancelled, JobCreate, JobCreated, JobView, LeaseRequest, Live,
                                 Ready, SynthesizeRequest, TranscriptUpdate, VoiceList, VoiceProfile, Warmup)
from sarae_voice.tokens import ReadTokens
from sarae_voice.voices import VoiceStore

log = logging.getLogger("sarae.voice.api")
bearer = HTTPBearer(auto_error=False, description="Service token (orchestrator or admin scope)")
ERRORS = {code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 410, 413, 422, 423, 429, 500, 503, 507)}


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings()
    if not settings.orchestrator_token_set and not settings.admin_token_set:
        raise RuntimeError("Set SARAE_VOICE_ORCHESTRATOR_TOKENS (the API never runs without authentication)")
    engine = engine or build_engine(settings)
    admission = LiveAdmission(settings.live_lease_max_ttl_s)
    gate = GpuGate(settings.queue_limits(), settings.queue_timeout_s, settings.starvation_s, settings.fair_every,
                   hold=lambda task: admission.holds(task.klass))
    store = VoiceStore(settings, engine, gate)
    results = ResultCache(Path(settings.data_dir) / "results", settings.result_ttl_s, settings.result_max_bytes)
    metrics = Metrics()
    jobs = JobManager(settings, engine, gate, store, results, ReadTokens(settings.read_token_secret), admission, metrics)
    state = {"started_at": time.time(), "exit_requested": False}

    def gauges():
        out = [("tts_engine_ready", {}, 1.0 if engine.state == "ready" else 0.0, "1 when the model is loaded"),
               ("tts_gpu_busy", {}, 1.0 if gate.busy else 0.0, "1 while a GPU call runs"),
               ("tts_live_leases", {}, float(len(admission.view())), "Active LIVE leases")]
        out += [("tts_queue_depth", {"priority": k}, float(v), "Queued GPU calls") for k, v in gate.queued_by_class().items()]
        for prio, states in jobs.counts().items():
            out += [("tts_jobs", {"priority": prio, "state": s}, float(n), "Jobs in memory") for s, n in states.items()]
        out += [("tts_prompt_cache_total", {"result": k}, float(v), "VoiceClonePrompt cache") for k, v in store.stats.items()]
        out += [("tts_result_cache_events", {"event": k}, float(v), "Result cache") for k, v in results.stats.items()]
        out += [(f"tts_vram_{k}", {}, float(v), "CUDA memory (GiB)") for k, v in engine.memory().items()]
        return out
    metrics.gauges(gauges)

    async def housekeeping():
        stop_file = Path(settings.data_dir) / "server.stop"
        last_cleanup = time.monotonic()
        while True:
            await asyncio.sleep(1.0)
            if stop_file.exists():                     # graceful stop requested by the supervisor / service script
                stop_file.unlink(missing_ok=True)
                request_exit("stop file")
            if time.monotonic() - last_cleanup >= settings.cleanup_interval_s:
                last_cleanup = time.monotonic()
                try:
                    purged = jobs.purge()
                    evicted = await asyncio.to_thread(results.cleanup)
                    outputs = await asyncio.to_thread(_cleanup_outputs, Path(settings.data_dir), settings.result_ttl_s)
                    if purged or evicted or outputs:
                        log.info("cleanup: jobs=%s results=%s outputs=%s", purged, evicted, outputs)
                except Exception:  # noqa: BLE001 - housekeeping must keep running
                    log.exception("cleanup failed")

    def request_exit(reason: str) -> None:
        if state["exit_requested"]:
            return
        state["exit_requested"] = True
        log.warning("Exit requested: %s", reason)
        hook = getattr(app.state, "request_exit", None)
        if hook:
            hook()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # The model loads on the GPU thread in the background: /health/live answers at once, /health/ready
        # reports "loading" until it is done.
        load = asyncio.create_task(gate.run(engine.load, klass="system", bounded=False, timeout=0))
        keeper = asyncio.create_task(housekeeping())
        yield
        keeper.cancel()
        dropped = gate.close()              # no new GPU work; queued calls withdrawn (their jobs end "cancelled")
        jobs.shutdown()
        if dropped:
            log.info("shutdown: %s queued GPU calls withdrawn", len(dropped))
        finished = await asyncio.to_thread(gate.join, settings.shutdown_grace_s)
        if not finished:
            log.warning("shutdown: a GPU call was still running after %ss", settings.shutdown_grace_s)
        load.cancel()

    app = FastAPI(title="Sarae Voice Server", version=__version__, lifespan=lifespan,
                  description="G-OmniVoice TTS backend for Sarae Live Studio. Design: docs/TTS_SERVER.md.")
    app.state.settings, app.state.engine, app.state.gate, app.state.store = settings, engine, gate, store
    app.state.jobs, app.state.results, app.state.admission, app.state.metrics = jobs, results, admission, metrics

    # --- middleware and errors ------------------------------------------------------------------
    @app.middleware("http")
    async def timing(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        metrics.observe("tts_http_request_seconds", time.perf_counter() - started, "HTTP handling time",
                        route=route, method=request.method, status=f"{response.status_code // 100}xx")
        if engine.state == "error" and (engine.error or {}).get("code") == "TTS_GPU_FAILED" and settings.exit_on_gpu_failure:
            request_exit("CUDA failure")         # the supervisor starts a clean process
        return response

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, exc: ServiceError):
        headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else None
        metrics.inc("tts_errors_total", "Errors returned by code", code=exc.code)
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, exc: RequestValidationError):
        fields = sorted({".".join(str(p) for p in e.get("loc", ())[1:]) or "body" for e in exc.errors()})
        return JSONResponse({"error": {"code": "INVALID_PARAMS", "message": "Invalid: " + ", ".join(fields)}}, 422)

    # --- auth -----------------------------------------------------------------------------------
    def service_token(token: str, name: str = "orchestrator") -> bool:
        allowed = settings.admin_token_set | (settings.orchestrator_token_set if name == "orchestrator" else set())
        return bool(token) and any(hmac.compare_digest(token, t) for t in allowed)

    def scope(name: str):
        def check(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
            if service_token(creds.credentials if creds else "", name):
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
                "uptime_s": round(time.time() - state["started_at"], 1), "boot_id": jobs.boot, "engine": engine.info(),
                "memory": engine.memory(), "limits": {k: getattr(settings, k) for k in (
                    "max_text_chars", "job_max_chars", "max_wav_bytes", "min_ref_seconds", "max_ref_seconds",
                    "queue_timeout_s", "prompt_ram_cache", "max_output_bytes", "result_max_bytes", "result_ttl_s")},
                "gate": {"pending": gate.pending, "busy": gate.busy, "running": gate.running(),
                         "queued": gate.queued_by_class(), "queue_limits": gate.limits, "rejected": gate.rejected,
                         "timed_out": gate.timed_out, "max_active_seen": gate.max_active_seen, "fair_turns": gate.fair_turns,
                         "by_class": gate.counts},
                "admission": {"live_active": admission.active(), "leases": admission.view(),
                              "expired_total": admission.expired_total},
                "jobs": jobs.counts(),
                "prompt_cache": {**store.stats, "ram_entries": len(store._ram)},
                "result_cache": {**results.stats, **results.usage(), "pinned": len(results.pinned)},
                "synthesis": {"ok": metrics.counter("tts_sync_total", result="ok"),
                              "failed": metrics.counter("tts_sync_total", result="failed"),
                              "inference": metrics.quantiles("tts_inference_seconds", kind="sync")},
                "latency": {"job_inference": metrics.quantiles("tts_inference_seconds", kind="job"),
                            "job_queue_wait": {p: metrics.quantiles("tts_queue_wait_seconds", priority=p)
                                               for p in ("live", "preview", "prepare", "long", "benchmark")},
                            "http_synthesize": metrics.quantiles("tts_http_request_seconds", route="/v1/tts/synthesize"),
                            "http_job_audio": metrics.quantiles("tts_http_request_seconds",
                                                                route="/v1/tts/jobs/{job_id}/audio")},
                "generated_not_consumed": metrics.counter("tts_generated_not_consumed_total"),
                "discarded_after_inference": metrics.counter("tts_discarded_after_inference_total")}

    @app.get("/metrics", tags=["status"], dependencies=[orchestrator], response_class=PlainTextResponse)
    async def prometheus():
        return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")

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

    # --- synchronous synthesis (Phase 1, preview class) ----------------------------------------------
    @app.post("/v1/tts/synthesize", tags=["tts"], dependencies=[orchestrator],
              response_class=Response, responses={200: {"content": {"audio/wav": {}},
                                                         "description": "WAV, mono PCM 16-bit, model sample rate"},
                                                  **ERRORS})
    async def synthesize(body: SynthesizeRequest):
        """Short text -> WAV with the voice's cached prompt (preview priority). Timings and configuration in headers."""
        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:16]
        require_ready()
        text = body.text.strip()                 # exactly what omnivoice-demo does with its text box
        if not text:
            raise ServiceError("INVALID_PARAMS", "Empty text", 422)
        if len(text) > settings.max_text_chars:
            raise ServiceError("TEXT_TOO_LONG", f"Text longer than {settings.max_text_chars} characters", 422)
        prompt, cache, prompt_ms, profile = await store.prompt(body.voice_id)
        try:
            audio, queue_ms, run_ms = await gate.run(engine.synthesize, prompt, text, body.preset, body.seed,
                                                     klass="preview", label=f"sync:{request_id}")
        except ServiceError:
            metrics.inc("tts_sync_total", "Synchronous synthesis calls", result="failed")
            raise
        data = wav_bytes(audio, engine.sampling_rate)
        audio_ms = round(len(audio) / engine.sampling_rate * 1000, 1)
        server_ms = round((time.perf_counter() - started) * 1000, 1)
        metrics.inc("tts_sync_total", "Synchronous synthesis calls", result="ok")
        metrics.observe("tts_inference_seconds", run_ms / 1000, "GPU time per call", kind="sync", priority="preview")
        metrics.observe("tts_queue_wait_seconds", queue_ms / 1000, "Wait before the GPU started", priority="preview")
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

    # --- jobs (Phase 2) ---------------------------------------------------------------------------
    @app.post("/v1/tts/jobs", response_model=JobCreated, status_code=202, tags=["jobs"], dependencies=[orchestrator],
              responses={200: {"model": JobCreated, "description": "Idempotent replay of an existing job"}, **ERRORS})
    async def create_job(body: JobCreate, idempotency_key: str | None = Header(default=None, max_length=128)):
        """Queue a TTS job. 202 new (or done at once from the result cache); 200 replay of the same Idempotency-Key."""
        job, how = await jobs.create(body, idempotency_key)
        token = jobs.read_token(job)
        payload = {**job.view(), "created": how, "read_token": token[0] if token else None,
                   "read_token_expires_at": _utc(token[1]) if token else None}
        return JSONResponse(payload, 200 if how == "replay" else 202, headers={"Cache-Control": "no-store"})

    @app.get("/v1/tts/jobs/{job_id}", response_model=JobView, tags=["jobs"], dependencies=[orchestrator],
             responses=ERRORS)
    async def get_job(job_id: str, wait: float = Query(default=0, ge=0, le=20)):
        """Job state (never the read token). `wait` holds the request up to that many seconds for a final state."""
        job = jobs.get(job_id)
        await jobs.wait(job, wait)
        return job.view()

    @app.get("/v1/tts/jobs/{job_id}/audio", tags=["jobs"], response_class=Response,
             responses={200: {"content": {"audio/wav": {}}}, 202: {"model": JobView}, **ERRORS})
    async def job_audio(job_id: str, wait: float = Query(default=0, ge=0, le=20),
                        creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                        x_sarae_agent_id: str | None = Header(default=None, max_length=80)):
        """WAV of a finished job. Auth: this job's read token (with X-Sarae-Agent-Id) or the orchestrator token.
        202 while queued/running (after `wait`), 410 when cancelled/expired, 409 when it failed."""
        token = creds.credentials if creds else ""
        if service_token(token):
            via = "orchestrator"
        else:
            if not token or "." not in token:
                raise ServiceError("TTS_AUTH", "Missing or invalid token", 401)
            if not x_sarae_agent_id:
                raise ServiceError("TOKEN_SCOPE", "X-Sarae-Agent-Id is required with a read token", 403)
            jobs.tokens.verify(token, job_id, x_sarae_agent_id)
            via = "read_token"
        job = jobs.get(job_id)
        await jobs.wait(job, wait)
        if job.state in ("cancelled", "expired"):
            raise ServiceError("JOB_" + job.state.upper(), f"Job {job.state}; audio not available", 410)
        if job.state == "failed":
            raise ServiceError("JOB_FAILED", (job.error or {}).get("code", "failed"), 409)
        if job.state != "done":
            return JSONResponse(job.view(), 202, headers={"Retry-After": "1"})
        path = jobs.audio_path(job)
        job.reads += 1
        metrics.inc("tts_job_reads_total", "Audio downloads", via=via)
        return FileResponse(path, media_type="audio/wav", headers={
            "Cache-Control": "no-store", "X-Job-Id": job.job_id, "X-Voice-Rev": job.voice_rev,
            "X-Source": job.source or "", "X-Audio-Ms": str((job.audio or {}).get("audio_ms")),
            "X-Queue-Ms": str(job.timings.get("queue_ms", 0)), "X-Inference-Ms": str(job.timings.get("inference_ms", 0))})

    @app.delete("/v1/tts/jobs/{job_id}", response_model=JobView, tags=["jobs"], dependencies=[orchestrator],
                responses=ERRORS)
    async def cancel_job(job_id: str):
        """Cancel: queued -> never runs; running -> result discarded; done -> audio no longer readable."""
        job = jobs.cancel(jobs.get(job_id))
        await asyncio.sleep(0)            # let a queued job's runner record the cancellation
        return job.view()

    @app.delete("/v1/tts/jobs", response_model=JobCancelled, tags=["jobs"], dependencies=[orchestrator],
                responses=ERRORS)
    async def cancel_jobs(tag: str = Query(pattern=r"^[A-Za-z0-9:_.-]{1,80}$")):
        """Cancel every unfinished job carrying `tag` (e.g. session:123 when a LIVE ends)."""
        return JobCancelled(cancelled=jobs.cancel_tag(tag))

    # --- LIVE admission -----------------------------------------------------------------------------
    def admission_view() -> dict:
        running = gate.running()
        held = running is not None and running["class"] in ("long", "benchmark")
        reason = None
        if engine.state != "ready":
            reason = "ENGINE_NOT_READY"
        elif held:
            reason = "LONG_JOB_RUNNING"
        elif running is not None:
            reason = "GPU_BUSY"
        return {"live_active": admission.active(), "leases": admission.view(),
                "gpu": {"state": engine.state, "busy": running is not None, "running": running,
                        "queued": gate.queued_by_class()},
                "ready_for_live": reason is None or reason == "GPU_BUSY" and running["class"] in ("live", "preview", "prepare"),
                "reason": reason,
                "note": "estimated_remaining_s is a rough estimate from text length; a running call cannot be stopped"}

    @app.put("/v1/admission/live", tags=["admission"], dependencies=[orchestrator], responses=ERRORS)
    async def put_lease(body: LeaseRequest):
        """Create or renew a LIVE lease (renew about every ttl/2). Reports whether the GPU is free for LIVE now."""
        admission.put(body.owner_ref, body.session_id, body.ttl_s)
        return admission_view()

    @app.get("/v1/admission/live", tags=["admission"], dependencies=[orchestrator], responses=ERRORS)
    async def get_leases():
        return admission_view()

    @app.delete("/v1/admission/live", tags=["admission"], dependencies=[orchestrator], responses=ERRORS)
    async def delete_lease(owner_ref: str = Query(max_length=100),
                           session_id: str = Query(pattern=r"^[A-Za-z0-9:_.-]{1,80}$")):
        removed = admission.delete(owner_ref, session_id)
        return {**admission_view(), "removed": removed}

    return app


def _save_output(root: Path, request_id: str, data: bytes, meta: dict) -> None:
    folder = root / "outputs" / datetime.now(timezone.utc).strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{request_id}.wav").write_bytes(data)
    (folder / f"{request_id}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _cleanup_outputs(root: Path, ttl_s: float) -> int:
    """Debug outputs (SARAE_VOICE_SAVE_OUTPUTS) older than the result TTL."""
    folder = root / "outputs"
    if not folder.is_dir():
        return 0
    now, removed = time.time(), 0
    for f in folder.rglob("*"):
        if f.is_file() and now - f.stat().st_mtime > ttl_s:
            f.unlink(missing_ok=True)
            removed += 1
    for d in sorted((p for p in folder.iterdir() if p.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    return removed
