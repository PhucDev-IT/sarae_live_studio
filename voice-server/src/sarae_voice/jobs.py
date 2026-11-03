"""TTS jobs (docs/TTS_SERVER.md 4): queued -> running -> done | failed | cancelled | expired.

* At creation the job snapshots voice_rev, the prompt compat, the preset's generation config and, when available
  without the GPU, the VoiceClonePrompt itself: a voice changed while the job waits does not change its audio
  (a prompt that still has to be encoded is checked against the snapshot and the job fails VOICE_CHANGED otherwise).
* Idempotency-Key + request fingerprint: the same key and the same request return the same job (no second inference);
  the same key with another request is 409. Deadline and tags are not part of the fingerprint, so a retry that
  recomputed its deadline is still the same job.
* Result cache (results.py) first: an identical earlier result is reused and the job is `done` at once.
* A queued job past its deadline never starts (expired). Cancelling a queued job removes it from the GPU queue;
  cancelling a running job flags it and its audio is discarded when the GPU call returns.
* Jobs live in memory. job_id starts with this process's boot id: an id from an earlier run answers 410 JOB_LOST
  and is never replayed. Finished jobs are kept `job_retention_s`, then answer 410 JOB_GONE.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sarae_voice.admission import LiveAdmission
from sarae_voice.audio import wav_bytes
from sarae_voice.config import Settings
from sarae_voice.engine import PRESETS, Engine
from sarae_voice.errors import ServiceError
from sarae_voice.gate import GpuGate
from sarae_voice.metrics import Metrics
from sarae_voice.results import ResultCache
from sarae_voice.tokens import ReadTokens
from sarae_voice.voices import VoiceStore

log = logging.getLogger("sarae.voice.jobs")
JOB_ID = re.compile(r"^[a-f0-9]{6}[A-Za-z0-9_-]{16,40}$")
DEFAULT_TTL_S = {"live": 60, "preview": 60, "prepare": 900, "long": 3600, "benchmark": 3600}
SHORT_CLASSES = ("live", "preview", "prepare")
TERMINAL = ("done", "failed", "cancelled", "expired")


def _utc(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds") if ts else None


@dataclass
class Job:
    job_id: str
    voice_id: str
    voice_rev: str
    prompt_compat: str
    preset: str
    generation_config: dict
    priority: str
    text: str
    seed: int | None
    deadline_at: float                 # unix seconds
    deadline_mono: float
    reader_agent_id: str | None
    owner_ref: str | None
    tags: list[str]
    idempotency_key: str | None
    fingerprint: str
    result_key: str
    created_at: float = field(default_factory=time.time)
    state: str = "queued"
    cancel_requested: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    source: str | None = None          # generated | result_cache
    error: dict | None = None
    timings: dict = field(default_factory=dict)
    audio: dict | None = None
    reads: int = 0
    prompt: Any = None
    gpu_task: Any = None
    event: asyncio.Event = field(default_factory=asyncio.Event)
    pinned: bool = False
    runner: Any = None                 # the asyncio task driving this job

    def view(self) -> dict:
        return {"job_id": self.job_id, "state": self.state, "cancel_requested": self.cancel_requested,
                "voice_id": self.voice_id, "voice_rev": self.voice_rev, "prompt_compat": self.prompt_compat,
                "preset": self.preset, "generation_config": self.generation_config, "priority": self.priority,
                "chars": len(self.text), "seed": self.seed, "deadline_at": _utc(self.deadline_at),
                "reader_agent_id": self.reader_agent_id, "owner_ref": self.owner_ref, "tags": self.tags,
                "created_at": _utc(self.created_at), "started_at": _utc(self.started_at),
                "finished_at": _utc(self.finished_at), "source": self.source, "error": self.error,
                "timings": self.timings, "audio": self.audio, "reads": self.reads}


class JobManager:
    def __init__(self, settings: Settings, engine: Engine, gate: GpuGate, store: VoiceStore, results: ResultCache,
                 tokens: ReadTokens, admission: LiveAdmission, metrics: Metrics):
        self.s, self.engine, self.gate, self.store = settings, engine, gate, store
        self.results, self.tokens, self.admission, self.metrics = results, tokens, admission, metrics
        self.boot = secrets.token_hex(3)
        self.jobs: dict[str, Job] = {}
        self.idem: dict[str, tuple[str, str, float]] = {}        # key -> (fingerprint, job_id, expires)
        self.gone: dict[str, float] = {}                         # purged job ids -> when

    # --- create -----------------------------------------------------------------------------------
    def estimate(self, text: str) -> tuple[float, float]:
        """(audio seconds, GPU seconds) from text length: a rough planning figure, never a promise."""
        audio = len(text) / self.s.chars_per_audio_s
        return audio, 1.8 + 0.27 * audio

    async def create(self, body, idem_key: str | None) -> tuple[Job, str]:
        """Returns (job, how) with how in new | replay | result_cache."""
        if self.engine.state != "ready":
            raise ServiceError(self.engine.error["code"] if self.engine.error else "TTS_NOT_READY",
                               f"Engine is {self.engine.state}", 503, retry_after=5)
        text = body.text.strip()                  # exactly what omnivoice-demo does with its text box
        if not text:
            raise ServiceError("INVALID_PARAMS", "Empty text", 422)
        limit = self.s.max_text_chars if body.priority in SHORT_CLASSES else self.s.job_max_chars
        if len(text) > limit:
            raise ServiceError("TEXT_TOO_LONG", f"{body.priority} jobs take at most {limit} characters", 422)
        now = time.time()
        deadline = body.deadline_at.timestamp() if body.deadline_at else now + DEFAULT_TTL_S[body.priority]
        if deadline <= now:
            raise ServiceError("DEADLINE_PASSED", "deadline_at is already in the past", 422)
        if deadline - now > self.s.max_deadline_s:
            raise ServiceError("INVALID_PARAMS", f"deadline_at at most {self.s.max_deadline_s:g} s ahead", 422)
        est_audio, est_gpu = self.estimate(text)
        if self.admission.active():
            if body.priority in ("long", "benchmark"):
                raise ServiceError("LIVE_ACTIVE", f"{body.priority} jobs are not accepted during LIVE", 423, retry_after=60)
            if body.priority == "prepare" and est_audio > self.s.prepare_live_max_audio_s:
                raise ServiceError("LIVE_ACTIVE", "prepare text too long during LIVE", 423, retry_after=60)

        profile = self.store.get(body.voice_id)
        if profile.get("owner_ref") and body.owner_ref != profile["owner_ref"]:
            raise ServiceError("VOICE_NOT_FOUND", "Voice not found", 404)      # never reveal another owner's voice
        compat = self.store.compat(profile)
        config = PRESETS[body.preset]
        fingerprint = hashlib.sha256(json.dumps(
            {"voice_id": body.voice_id, "rev": profile["rev"], "compat": compat, "text": text, "preset": body.preset,
             "config": config, "seed": body.seed, "owner_ref": body.owner_ref, "reader": body.reader_agent_id,
             "priority": body.priority}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

        self._expire_idem(now)
        if idem_key:
            known = self.idem.get(idem_key)
            if known:
                if known[0] != fingerprint:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "Idempotency-Key already used for another request", 409)
                job = self.get(known[1])
                self.metrics.inc("tts_jobs_created_total", "Job create calls by outcome", priority=job.priority,
                                 how="replay")
                return job, "replay"

        basis = self.engine.compat_basis()
        result_key = ResultCache.key(owner_ref=body.owner_ref, voice_id=body.voice_id, rev=profile["rev"],
                                     compat=compat, basis=basis, config=config, preset=body.preset, text=text,
                                     seed=body.seed)
        job = Job(job_id=self.boot + secrets.token_urlsafe(15).replace("-", "_"), voice_id=body.voice_id,
                  voice_rev=profile["rev"], prompt_compat=compat, preset=body.preset, generation_config=config,
                  priority=body.priority, text=text, seed=body.seed, deadline_at=deadline,
                  deadline_mono=time.monotonic() + (deadline - now), reader_agent_id=body.reader_agent_id,
                  owner_ref=body.owner_ref, tags=list(body.tags), idempotency_key=idem_key, fingerprint=fingerprint,
                  result_key=result_key)
        job.timings["estimated_audio_s"] = round(est_audio, 1)
        self.jobs[job.job_id] = job
        if idem_key:
            self.idem[idem_key] = (fingerprint, job.job_id, now + self.s.job_retention_s + (deadline - now))

        cached = self.results.lookup(result_key, basis)
        self.metrics.inc("tts_result_cache_total", "Result cache lookups", result="hit" if cached else "miss")
        if cached:
            self.results.pin(result_key)
            job.pinned = True
            job.audio = {k: cached[k] for k in ("bytes", "audio_ms", "sample_rate", "sha256")}
            job.source = "result_cache"
            self._finish(job, "done")
            self.metrics.inc("tts_jobs_created_total", "Job create calls by outcome", priority=job.priority,
                             how="result_cache")
            return job, "result_cache"

        snap = self.store.peek(body.voice_id, compat)
        try:
            if snap:
                # Queued on the GPU right now, so a full class answers 429 to this request (not a failed job later).
                job.prompt, job.timings["prompt_cache"] = snap
                self._submit(job)
            elif self.gate.queued_by_class().get(job.priority, 0) >= self.gate.limits[job.priority]:
                raise ServiceError("TTS_BUSY", f"The {job.priority} queue is full", 429, retry_after=2)
        except ServiceError:
            del self.jobs[job.job_id]
            if idem_key:
                self.idem.pop(idem_key, None)
            raise
        job.runner = asyncio.create_task(self._run(job))
        self.metrics.inc("tts_jobs_created_total", "Job create calls by outcome", priority=job.priority, how="new")
        log.info("job created id=%s voice=%s priority=%s chars=%s", job.job_id, job.voice_id, job.priority, len(text))
        return job, "new"

    def read_token(self, job: Job) -> tuple[str, float] | None:
        if not job.reader_agent_id:
            return None
        exp = min(job.deadline_at + 60, time.time() + self.s.read_token_max_ttl_s)
        return self.tokens.issue(job.job_id, job.reader_agent_id, exp), exp

    # --- run ----------------------------------------------------------------------------------------
    async def _run(self, job: Job) -> None:
        try:
            if job.prompt is None:
                prompt, cache, ms, profile = await self.store.prompt(job.voice_id, klass=job.priority)
                if profile["rev"] != job.voice_rev or self.store.compat(profile) != job.prompt_compat:
                    raise ServiceError("VOICE_CHANGED", "The voice changed after the job was created", 409)
                job.prompt = prompt
                job.timings.update(prompt_cache=cache, prompt_ms=ms)
            if job.cancel_requested:
                raise asyncio.CancelledError
            if job.gpu_task is None:
                self._submit(job)
            audio = await asyncio.wrap_future(job.gpu_task.future)
            job.timings.update(queue_ms=job.gpu_task.queue_ms, inference_ms=job.gpu_task.run_ms)
            self.metrics.observe("tts_queue_wait_seconds", job.gpu_task.queue_ms / 1000, "Wait before the GPU started",
                                 priority=job.priority)
            self.metrics.observe("tts_inference_seconds", job.gpu_task.run_ms / 1000, "GPU time per call", kind="job",
                                 priority=job.priority)
            if job.cancel_requested:
                self.metrics.inc("tts_discarded_after_inference_total", "Cancelled while running: audio dropped",
                                 priority=job.priority)
                self._finish(job, "cancelled", {"code": "CANCELLED", "message": "Cancelled while running; audio discarded"})
                return
            data = wav_bytes(audio, self.engine.sampling_rate)
            if len(data) > self.s.max_output_bytes:
                raise ServiceError("OUTPUT_TOO_LARGE", f"Audio larger than {self.s.max_output_bytes} bytes", 507)
            info = {"audio_ms": round(len(audio) / self.engine.sampling_rate * 1000, 1),
                    "sample_rate": self.engine.sampling_rate, "job_id": job.job_id}
            meta = await asyncio.to_thread(self.results.store, job.result_key, data, self.engine.compat_basis(), info)
            self.results.pin(job.result_key)
            job.pinned = True
            job.audio = {k: meta[k] for k in ("bytes", "audio_ms", "sample_rate", "sha256")}
            job.source = "generated"
            self._finish(job, "done")
        except asyncio.CancelledError:
            reason = "Cancelled before the GPU started" if job.started_at is None else "Cancelled"
            if self.gate._closed:
                reason = "Server shutting down"
            self._finish(job, "cancelled", {"code": "CANCELLED", "message": reason})
        except ServiceError as exc:
            self._finish(job, "expired" if exc.code == "TTS_EXPIRED" else "failed",
                         {"code": exc.code, "message": exc.message})
        except Exception as exc:  # noqa: BLE001 - a job never takes the server down
            log.exception("job %s failed", job.job_id)
            self._finish(job, "failed", {"code": "TTS_INFERENCE_FAILED", "message": type(exc).__name__})

    def _submit(self, job: Job) -> None:
        _, est_gpu = self.estimate(job.text)

        def started(_task):
            job.state, job.started_at = "running", time.time()
        job.gpu_task = self.gate.submit(self.engine.synthesize, job.prompt, job.text, job.preset, job.seed,
                                        klass=job.priority, deadline=job.deadline_mono, label=f"job:{job.job_id}",
                                        est_s=est_gpu, on_start=started)

    def _finish(self, job: Job, state: str, error: dict | None = None) -> None:
        if job.state in TERMINAL:
            return
        job.state, job.error, job.finished_at = state, error, time.time()
        if state != "done" and job.pinned:
            self.results.unpin(job.result_key)
            job.pinned = False
        job.prompt = None
        job.event.set()
        self.metrics.inc("tts_jobs_finished_total", "Jobs by final state", priority=job.priority, state=state)

    # --- read / cancel ------------------------------------------------------------------------------
    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job:
            return job
        if job_id in self.gone:
            raise ServiceError("JOB_GONE", "Job finished and was removed after its retention time", 410)
        if JOB_ID.match(job_id or "") and not job_id.startswith(self.boot):
            raise ServiceError("JOB_LOST", "Job belonged to an earlier run of the voice server; it is not replayed", 410)
        raise ServiceError("JOB_NOT_FOUND", "Job not found", 404)

    def cancel(self, job: Job) -> Job:
        if job.state in ("failed", "expired", "cancelled"):
            return job
        job.cancel_requested = True
        if job.state == "done":                       # revoke access: its token can no longer read the audio
            if job.reads == 0:
                self.metrics.inc("tts_generated_not_consumed_total", "Audio made but never read", priority=job.priority)
            self._finish_done_as_cancelled(job)
            return job
        if job.gpu_task is not None:
            if self.gate.cancel(job.gpu_task):
                return job                              # removed from the queue; _run records the cancellation
            return job                                  # running: flagged, result dropped when it returns
        runner = getattr(job, "runner", None)
        if runner is not None:
            runner.cancel()                             # still preparing the prompt
        return job

    def _finish_done_as_cancelled(self, job: Job) -> None:
        job.state, job.error = "cancelled", {"code": "CANCELLED", "message": "Cancelled after completion"}
        if job.pinned:
            self.results.unpin(job.result_key)
            job.pinned = False
        self.metrics.inc("tts_jobs_finished_total", "Jobs by final state", priority=job.priority, state="cancelled")

    def cancel_tag(self, tag: str) -> int:
        n = 0
        for job in list(self.jobs.values()):
            if tag in job.tags and job.state not in ("failed", "expired", "cancelled"):
                self.cancel(job)
                n += 1
        return n

    async def wait(self, job: Job, seconds: float) -> None:
        if job.state in TERMINAL or seconds <= 0:
            return
        try:
            await asyncio.wait_for(job.event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def audio_path(self, job: Job):
        path = self.results.path(job.result_key)
        if not path.is_file():
            raise ServiceError("RESULT_GONE", "Audio is no longer stored", 410)
        return path

    # --- housekeeping -------------------------------------------------------------------------------
    def _expire_idem(self, now: float) -> None:
        for key in [k for k, v in self.idem.items() if v[2] < now]:
            del self.idem[key]

    def purge(self) -> int:
        now = time.time()
        removed = 0
        for job in list(self.jobs.values()):
            if job.state in TERMINAL and job.finished_at and now - job.finished_at > self.s.job_retention_s \
                    and now > job.deadline_at:
                if job.state == "done" and job.reads == 0:
                    self.metrics.inc("tts_generated_not_consumed_total", "Audio made but never read",
                                     priority=job.priority)
                if job.pinned:
                    self.results.unpin(job.result_key)
                del self.jobs[job.job_id]
                self.gone[job.job_id] = now
                removed += 1
        for jid in [j for j, t in self.gone.items() if now - t > 86400]:
            del self.gone[jid]
        self._expire_idem(now)
        return removed

    def counts(self) -> dict:
        out: dict[str, dict[str, int]] = {}
        for job in self.jobs.values():
            out.setdefault(job.priority, {}).setdefault(job.state, 0)
            out[job.priority][job.state] += 1
        return out

    def shutdown(self) -> None:
        for job in self.jobs.values():
            if job.state == "queued" and job.gpu_task is None and getattr(job, "runner", None):
                job.runner.cancel()
