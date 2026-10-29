"""AI Timeline Analyzer API: settings, analysis jobs (background, persisted), cache, and review drafts.

- GET/PUT /api/ai/timeline-settings: providers and models (owner settings `timeline_ai`; keys stay in app/credentials).
- POST /api/live/sessions/{id}/timeline-analysis: analyse the session's main video (202 + job; cached results return
  at once). GET .../timeline-analysis[/{job}]: jobs, progress, suggestions with evidence.
- POST .../timeline-analysis/{job}/draft: a new DRAFT timeline version with the suggestions as AI markers. Low
  confidence ones are NEEDS_REVIEW, which blocks approval until a person reviews them (app/live_timeline.py).

Cache (table timeline_ai_cache): the transcript by (video revision, transcript + alignment settings); the analysis by
(video revision, catalog version, every provider/model/setting, pipeline version). Re-running with nothing changed
reads the cache; a catalog change re-runs only the LLM part.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.catalog import Product
from app.config import settings
from app.database import Base, SessionLocal
from app.identity import current_owner, owner_settings, update_owner_settings
from app.ownership import Owner
from app.timeline_ai import pipeline
from app.timeline_ai.providers import ProviderUnavailable, build as build_providers

log = logging.getLogger("sarae.timeline_ai")
settings_router = APIRouter(prefix="/api/ai", tags=["AI Timeline Analyzer"], dependencies=[Depends(current_owner)])
router = APIRouter(prefix="/api/live/sessions", tags=["AI Timeline Analyzer"], dependencies=[Depends(current_owner)])
MODEL = r"^[A-Za-z0-9._/:-]{1,120}$"
_worker = threading.Semaphore(1)  # one analysis at a time: transcription is CPU/GPU heavy


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ----- settings -----------------------------------------------------------------------------------------------------

class TranscriptSettings(BaseModel):
    provider: Literal["faster_whisper"] = "faster_whisper"
    # Default from the Backend environment (SARAE_WHISPER_MODEL), never fixed in code.
    model: str = Field(default_factory=lambda: settings.sarae_whisper_model, pattern=MODEL)
    language: str = Field(default="vi", pattern=r"^[a-z]{2}$")
    device: str = Field(default_factory=lambda: settings.sarae_whisper_device, pattern=r"^(auto|cpu|cuda)$")
    compute_type: str = Field(default_factory=lambda: settings.sarae_whisper_compute_type, pattern=r"^[a-z0-9_]{2,20}$")


class AlignmentSettings(BaseModel):
    provider: Literal["words", "whisperx"] = "words"
    model: str | None = Field(default=None, pattern=MODEL)


class LLMSettings(BaseModel):
    provider: Literal["gemini"] = "gemini"
    model: str | None = Field(default=None, pattern=MODEL)  # None: the model of the owner's AI settings
    max_output_tokens: int = Field(default=4000, ge=512, le=16000)


class VisionSettings(BaseModel):
    enabled: bool = False
    provider: Literal["gemini"] = "gemini"
    model: str | None = Field(default=None, pattern=MODEL)


class TimelineAISettings(BaseModel):
    transcript: TranscriptSettings = Field(default_factory=TranscriptSettings)
    alignment: AlignmentSettings = Field(default_factory=AlignmentSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)
    # Below this, a suggestion is NEEDS_REVIEW (never applied without a person).
    min_confidence: float = Field(default=0.75, ge=0.3, le=0.99)
    # Suggest an UNPIN when nothing else is presented this long after a product (0: never).
    unpin_after_seconds: int = Field(default=60, ge=0, le=3600)


def load_settings(owner_id: str) -> TimelineAISettings:
    return TimelineAISettings.model_validate(owner_settings(owner_id).get("timeline_ai") or {})


def resolved(owner_id: str, config: TimelineAISettings) -> dict:
    """The settings as they will run (LLM model filled in): what the analysis cache key covers."""
    from app.ai_service import load_config
    data = config.model_dump()
    data["llm"]["model"] = config.llm.model or load_config(owner_id).model
    if config.vision.enabled:
        data["vision"]["model"] = config.vision.model or data["llm"]["model"]
    return data


@settings_router.get("/timeline-settings")
def get_settings(owner: Owner):
    from app.ai_service import api_key
    config = load_settings(owner["id"])
    _key, source = api_key(owner["id"], config.llm.provider)
    return {**config.model_dump(), "resolved": resolved(owner["id"], config), "has_api_key": source is not None,
            "installed": {"faster_whisper": _installed("faster_whisper"), "whisperx": _installed("whisperx"),
                          "ffmpeg": bool(_tool("ffmpeg", required=False))}}


@settings_router.put("/timeline-settings")
def put_settings(body: TimelineAISettings, owner: Owner):
    update_owner_settings(owner["id"], timeline_ai=body.model_dump())
    return get_settings(owner)


def _installed(module: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(module) is not None


def _tool(name: str, required: bool = True) -> str | None:
    found = getattr(settings, f"sarae_{name}_path") or shutil.which(name)
    if not found and required:
        raise pipeline.AnalysisError("Máy chủ chưa cài ffmpeg nên chưa phân tích được video")
    return found


# ----- persistence --------------------------------------------------------------------------------------------------

class TimelineAnalysis(Base):
    __tablename__ = "timeline_analyses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[int] = mapped_column(Integer, index=True)
    media_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")  # QUEUED / RUNNING / DONE / FAILED
    phase: Mapped[str] = mapped_column(String(200), default="")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    cache_key: Mapped[str] = mapped_column(String(64))
    cached: Mapped[int] = mapped_column(Integer, default=0)
    config: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeline_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AICacheEntry(Base):
    __tablename__ = "timeline_ai_cache"
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # transcript / analysis
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


def ensure_schema(bind) -> None:
    """Create the tables if missing (startup; Alembic 20261029_0017 does the same)."""
    TimelineAnalysis.__table__.create(bind=bind, checkfirst=True)
    AICacheEntry.__table__.create(bind=bind, checkfirst=True)


def key_of(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def video_revision(media) -> str:
    """Changes whenever the stored file changes (same rule as the Agent media cache)."""
    return media.sha256 or media.md5 or f"{media.provider}:{media.provider_file_id}"


def cache_get(db, key: str) -> dict | None:
    row = db.get(AICacheEntry, key)
    return json.loads(row.data) if row else None


def cache_put(db, key: str, kind: str, owner_id: str, data) -> None:
    db.merge(AICacheEntry(cache_key=key, kind=kind, owner_id=owner_id, data=json.dumps(data, ensure_ascii=False)))
    db.commit()


# ----- jobs ---------------------------------------------------------------------------------------------------------

class AnalyzeInput(BaseModel):
    force: bool = False  # ignore the cache (run every step again)


def start(target) -> None:
    """Background worker (tests replace it with a direct call)."""
    threading.Thread(target=target, name="timeline-ai", daemon=True).start()


def session_media(db, owner: dict, session_id: int):
    from app.live_timeline import main_video, session_of
    item = session_of(db, session_id, owner)
    if not item.main_media_id:
        raise HTTPException(409, "Phiên chưa có video chính")
    return item, main_video(db, owner["id"], item.main_media_id)


@router.post("/{session_id}/timeline-analysis", status_code=202)
def run_analysis(session_id: int, body: AnalyzeInput, owner: Owner):
    config = load_settings(owner["id"])
    with SessionLocal() as db:
        item, media = session_media(db, owner, session_id)
        running = db.scalar(select(TimelineAnalysis).where(TimelineAnalysis.session_id == session_id,
                                                           TimelineAnalysis.status.in_(("QUEUED", "RUNNING"))))
        if running is not None:
            return job_view(db, running)
        catalog, catalog_version = pipeline.catalog_snapshot(
            db.scalars(select(Product).where(Product.owner_id == owner["id"])).all())
        settled = resolved(owner["id"], config)
        key = key_of("analysis", pipeline.PIPELINE_VERSION, video_revision(media), catalog_version, settled)
        job = TimelineAnalysis(owner_id=owner["id"], session_id=session_id, media_id=media.id, cache_key=key,
                               config=json.dumps(settled, ensure_ascii=False))
        hit = None if body.force else cache_get(db, key)
        if hit is not None:
            job.status, job.phase, job.progress, job.cached = "DONE", "Kết quả đã có (cache)", 1.0, 1
            job.result = json.dumps(hit, ensure_ascii=False)
        db.add(job)
        db.commit()
        if hit is not None:
            return job_view(db, job)
        job_id = job.id
    start(lambda: _run(job_id, owner["id"], config, catalog, body.force))
    with SessionLocal() as db:
        return job_view(db, db.get(TimelineAnalysis, job_id))


def _update(job_id: int, **values) -> None:
    with SessionLocal() as db:
        job = db.get(TimelineAnalysis, job_id)
        for name, value in values.items():
            setattr(job, name, value)
        db.commit()


def _run(job_id: int, owner_id: str, config: TimelineAISettings, catalog: list[dict], force: bool) -> None:
    from app.storage_models import MediaItem
    with _worker, tempfile.TemporaryDirectory(prefix="sarae-timeline-ai-") as work:
        try:
            _update(job_id, status="RUNNING", phase="Chuẩn bị", progress=0.02)
            with SessionLocal() as db:
                job = db.get(TimelineAnalysis, job_id)
                media = db.get(MediaItem, job.media_id)
                media_revision, duration = video_revision(media), (int(media.duration_seconds * 1000) if media.duration_seconds else None)
                cache_key = job.cache_key
            transcript_key = key_of("transcript", pipeline.PIPELINE_VERSION, media_revision,
                                    config.transcript.model_dump(), config.alignment.model_dump())
            with SessionLocal() as db:
                transcript = None if force else cache_get(db, transcript_key)
            providers = build_providers(owner_id, config)
            video = None
            if transcript is None or providers.get("vision") is not None:
                _update(job_id, phase="Tải video từ thư viện", progress=0.05)
                video = f"{work}/video"
                with SessionLocal() as db:
                    from app.media_clips import _download
                    _download(db, db.get(MediaItem, media.id), video)
            result = pipeline.analyze(
                video=video, duration_ms=duration, catalog=catalog, config=config, providers=providers, transcript=transcript,
                ffmpeg=_tool("ffmpeg") if video else "ffmpeg", ffprobe=_tool("ffprobe") if video else "ffprobe", work=work,
                progress=lambda phase, value: _update(job_id, phase=phase, progress=round(value, 3)))
            with SessionLocal() as db:
                if transcript is None and result["transcript"]:
                    cache_put(db, transcript_key, "transcript", owner_id, result["transcript"])
                cache_put(db, cache_key, "analysis", owner_id, result)
            _update(job_id, status="DONE", phase="Hoàn tất", progress=1.0, result=json.dumps(result, ensure_ascii=False))
        except (pipeline.AnalysisError, ProviderUnavailable) as exc:
            _update(job_id, status="FAILED", error=str(exc)[:500])
        except Exception as exc:  # noqa: BLE001 - a background job always ends with a status
            log.exception("timeline analysis %s crashed", job_id)
            _update(job_id, status="FAILED", error=f"Phân tích thất bại: {type(exc).__name__}: {str(exc)[:200]}")


def job_view(db, job: TimelineAnalysis, detail: bool = False) -> dict:
    out = {"id": job.id, "session_id": job.session_id, "media_id": job.media_id, "status": job.status,
           "phase": job.phase, "progress": job.progress, "cached": bool(job.cached), "error": job.error,
           "timeline_id": job.timeline_id, "created_at": job.created_at, "updated_at": job.updated_at}
    if job.result:
        result = json.loads(job.result)
        out["summary"] = {"suggestions": len(result["suggestions"]),
                          "needs_review": sum(1 for s in result["suggestions"] if s["review_status"] == "NEEDS_REVIEW"),
                          "dropped_products": result.get("dropped_products", 0), "notes": result.get("notes", [])}
        if detail:
            ids = {s["product_id"] for s in result["suggestions"] if s["product_id"] is not None}
            names = {p.id: p.name for p in db.scalars(select(Product).where(Product.id.in_(ids)))} if ids else {}
            out["suggestions"] = [{**s, "product_name": names.get(s["product_id"])} for s in result["suggestions"]]
            out["config"] = json.loads(job.config or "{}")
    return out


def owned_job(db, owner: dict, session_id: int, job_id: int) -> TimelineAnalysis:
    job = db.get(TimelineAnalysis, job_id)
    if job is None or job.owner_id != owner["id"] or job.session_id != session_id:
        raise HTTPException(404, "Không tìm thấy lần phân tích")
    return job


@router.get("/{session_id}/timeline-analysis")
def list_analyses(session_id: int, owner: Owner):
    with SessionLocal() as db:
        session_media(db, owner, session_id)
        rows = db.scalars(select(TimelineAnalysis).where(TimelineAnalysis.session_id == session_id,
                                                         TimelineAnalysis.owner_id == owner["id"])
                          .order_by(TimelineAnalysis.id.desc()).limit(20)).all()
        return [job_view(db, row) for row in rows]


@router.get("/{session_id}/timeline-analysis/{job_id}")
def analysis_detail(session_id: int, job_id: int, owner: Owner):
    with SessionLocal() as db:
        return job_view(db, owned_job(db, owner, session_id, job_id), detail=True)


@router.post("/{session_id}/timeline-analysis/{job_id}/draft", status_code=201)
def draft_from_analysis(session_id: int, job_id: int, owner: Owner):
    """A new DRAFT timeline version holding the suggestions as AI markers, for review in the Timeline Editor."""
    from app.live_sessions import event
    from app.live_timeline import TimelineMarker, new_version, view
    with SessionLocal() as db:
        item, media = session_media(db, owner, session_id)
        job = owned_job(db, owner, session_id, job_id)
        if job.status != "DONE" or not job.result:
            raise HTTPException(409, "Lần phân tích chưa xong")
        if job.media_id != item.main_media_id:
            raise HTTPException(409, "Video chính đã đổi sau lần phân tích này; hãy phân tích lại")
        if item.status == "ENDED":
            raise HTTPException(409, "Phiên đã kết thúc")
        result = json.loads(job.result)
        known = set(db.scalars(select(Product.id).where(Product.owner_id == owner["id"])))
        timeline = new_version(db, item, owner["id"], note=f"Đề xuất AI (lần phân tích #{job.id})")
        seq: dict[int, int] = {}
        for s in result["suggestions"]:
            product = s["product_id"] if s["product_id"] in known else None
            action = s["action"] if product is not None or s["action"] == "UNPIN_PRODUCT" else "NO_ACTION"
            review = s["review_status"] if product is not None or action == "UNPIN_PRODUCT" else "NEEDS_REVIEW"
            evidence = {"transcript": s["evidence_transcript"], "timestamp_ms": s["evidence_timestamp_ms"],
                        "start_ms": s["start_ms"], "end_ms": s["end_ms"], "match_reason": s["match_reason"],
                        "sources": s.get("sources", []), "analysis_id": job.id}
            n = seq.get(s["start_ms"], 0)
            seq[s["start_ms"]] = n + 1
            db.add(TimelineMarker(timeline_id=timeline.id, at_ms=s["start_ms"], action=action, product_id=product, seq=n,
                                  label=("AI: chưa xác định sản phẩm" if action == "NO_ACTION" else f"AI {s['confidence']:.0%}")[:200],
                                  source="AI", review_status=review, confidence=s["confidence"],
                                  evidence=json.dumps(evidence, ensure_ascii=False)))
        job.timeline_id = timeline.id
        event(db, session_id, "TIMELINE_CREATED", detail=f"version={timeline.version};analysis={job.id}")
        db.commit()
        return view(db, owner["id"], item, timeline)
