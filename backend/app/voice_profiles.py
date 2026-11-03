"""G-OmniVoice voice profiles: Sarae Backend keeps the original (docs/TTS_SERVER.md 4.1, D3, 8.1).

- The reference WAV is stored through the existing Media Library storage provider (R2 / Google Drive), never in
  SQLite; the row keeps its provider + object id, SHA-256 and basic audio facts.
- Transcript: Whisper on the voice server by default (`transcript_source = asr`, like the official demo with an empty
  Reference Text); the operator may set a manual transcript and later clear it again. The Whisper text reported by
  the voice server is kept here too.
- `revision` changes whenever the WAV or the transcript changes. The voice server only holds a copy: `ensure_synced`
  uploads the WAV + transcript when the copy is missing (new GPU machine, its data lost) or older than `revision`;
  the voice server then rebuilds and invalidates its prompt cache by itself.
- A profile belongs to one OWNER: every query filters by owner_id and another owner's id answers 404. On the voice
  server the voice id is the profile id (globally unique) and owner_ref is the owner id, which it checks again.
Metadata stays readable when the voice server is offline; syncing resumes from the background loop.
"""
from __future__ import annotations

from app.voice_policy import owner_voice_lock, require_voice_mutable

import hashlib
import io
import json
import logging
import threading
import time
import wave
from datetime import datetime, timezone


from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import tts_server
from app.database import Base, SessionLocal
from app.ownership import Owner
from app.storage_models import aware, new_id

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai/voice/profiles", tags=["AI LIVE Voice"])

MAX_WAV_BYTES = 5 * 1024 * 1024
MIN_SECONDS, MAX_SECONDS = 1.0, 20.0       # the voice server rejects longer clips (docs 8.1)
MAX_TRANSCRIPT = 1000
VERIFY_SECONDS = 60                        # how long a successful remote check is trusted


def now() -> datetime:
    return datetime.now(timezone.utc)


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(100))
    storage_provider: Mapped[str] = mapped_column(String(20))
    storage_object_id: Mapped[str] = mapped_column(String(200))
    wav_sha256: Mapped[str] = mapped_column(String(64))
    wav_bytes: Mapped[int] = mapped_column(Integer)
    sample_rate: Mapped[int] = mapped_column(Integer)
    channels: Mapped[int] = mapped_column(Integer)
    duration_s: Mapped[float] = mapped_column(Float)
    transcript_source: Mapped[str] = mapped_column(String(10), default="asr")      # asr | manual
    transcript_manual: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_asr: Mapped[str | None] = mapped_column(Text, nullable=True)         # reported by the voice server
    revision: Mapped[int] = mapped_column(Integer, default=1)
    qc_json: Mapped[str] = mapped_column(Text, default="{}")
    tts_sync_state: Mapped[str] = mapped_column(String(20), default="PENDING")      # PENDING | SYNCED | FAILED
    tts_synced_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tts_rev: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tts_compat: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tts_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tts_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def ensure_schema(bind=None):
    from app.database import engine
    Base.metadata.create_all(bind=bind or engine, tables=[VoiceProfile.__table__], checkfirst=True)


# --- reading / views -------------------------------------------------------------------------------------
def owned(db: Session, owner_id: str, profile_id: str) -> VoiceProfile:
    row = db.get(VoiceProfile, profile_id) if isinstance(profile_id, str) and len(profile_id) <= 32 else None
    if row is None or row.owner_id != owner_id:
        raise HTTPException(404, "Không tìm thấy giọng nói")
    return row


def view(row: VoiceProfile) -> dict:
    qc = json.loads(row.qc_json or "{}")
    effective = row.transcript_manual if row.transcript_source == "manual" else row.transcript_asr
    return {"id": row.id, "name": row.name, "revision": row.revision,
            "wav": {"sha256": row.wav_sha256, "bytes": row.wav_bytes, "sample_rate": row.sample_rate,
                    "channels": row.channels, "duration_s": row.duration_s},
            "transcript": {"source": row.transcript_source, "manual": row.transcript_manual,
                           "asr": row.transcript_asr, "effective": effective},
            "qc": qc,
            "sync": {"state": row.tts_sync_state, "synced_revision": row.tts_synced_revision,
                     "up_to_date": row.tts_sync_state == "SYNCED" and row.tts_synced_revision == row.revision,
                     "error": row.tts_error, "synced_at": aware(row.tts_synced_at).isoformat() if row.tts_synced_at else None},
            "created_at": aware(row.created_at).isoformat(), "updated_at": aware(row.updated_at).isoformat()}


# --- validation and storage ----------------------------------------------------------------------------
def inspect_wav(data: bytes) -> dict:
    """PCM WAV facts with the standard library; the voice server runs the full QC (reported back on sync)."""
    if not data:
        raise HTTPException(422, "File WAV trống")
    if len(data) > MAX_WAV_BYTES:
        raise HTTPException(413, f"File WAV lớn hơn {MAX_WAV_BYTES // (1024 * 1024)} MB")
    try:
        with wave.open(io.BytesIO(data)) as w:
            channels, rate, frames, width = w.getnchannels(), w.getframerate(), w.getnframes(), w.getsampwidth()
    except (wave.Error, EOFError) as exc:
        raise HTTPException(422, "Chỉ nhận file WAV PCM (16/24/32-bit)") from exc
    duration = frames / rate if rate else 0
    if channels not in (1, 2) or not 8000 <= rate <= 96000 or width not in (1, 2, 3, 4):
        raise HTTPException(422, "WAV phải mono hoặc stereo, 8-96 kHz")
    if not MIN_SECONDS <= duration <= MAX_SECONDS:
        raise HTTPException(422, f"Giọng mẫu dài {MIN_SECONDS:g}-{MAX_SECONDS:g} giây (file này {duration:.1f} giây)")
    warnings = ["duration_not_recommended"] if not 3 <= duration <= 10 else []
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "sample_rate": rate, "channels": channels,
            "duration_s": round(duration, 3), "sample_width": width, "warnings": warnings}


def clean_transcript(text: str | None) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    if len(text) > MAX_TRANSCRIPT:
        raise HTTPException(422, f"Transcript tối đa {MAX_TRANSCRIPT} ký tự")
    return text or None


def _store(db: Session, owner_id: str, profile_id: str, revision: int, data: bytes) -> tuple[str, str]:
    from app.storage_providers import upload_provider
    provider = upload_provider(db)
    stored = provider.put(owner_id, f"voice-{profile_id}-r{revision}", "voice_ref.wav", "audio/wav", len(data), [data])
    return provider.name, stored.object_id


def _delete_object(db: Session, provider_name: str, object_id: str) -> None:
    from app.storage_providers import provider
    try:
        provider(provider_name, db).delete(object_id)
    except Exception as exc:  # noqa: BLE001 - an orphan object is reported, never blocks the user
        log.warning("voice WAV %s not deleted from %s: %s", object_id[:12], provider_name, type(exc).__name__)


def read_wav(db: Session, row: VoiceProfile) -> bytes:
    from app.storage_providers import provider
    stream = provider(row.storage_provider, db).open_read(row.storage_object_id)
    try:
        data = b"".join(stream.iter())
    finally:
        stream.close()
    if hashlib.sha256(data).hexdigest() != row.wav_sha256:
        raise HTTPException(502, "File giọng mẫu trong kho không khớp SHA-256")
    return data


# --- sync with the voice server -------------------------------------------------------------------------
_locks: dict[str, threading.Lock] = {}
_verified: dict[str, tuple[int, float]] = {}


def _lock(profile_id: str) -> threading.Lock:
    return _locks.setdefault(profile_id, threading.Lock())


def ensure_synced(db: Session, row: VoiceProfile, force: bool = False) -> VoiceProfile:
    """Make sure the voice server holds this profile at its current revision. Raises TtsUnavailable / TtsError."""
    with _lock(row.id):
        db.refresh(row)
        current = row.tts_sync_state == "SYNCED" and row.tts_synced_revision == row.revision
        seen = _verified.get(row.id)
        if current and not force and seen and seen[0] == row.revision and time.monotonic() - seen[1] < VERIFY_SECONDS:
            return row
        if current and not force:
            remote = tts_server.get_voice(row.id)          # the GPU machine may have been replaced or wiped
            if remote and remote.get("rev") == row.tts_rev:
                _verified[row.id] = (row.revision, time.monotonic())
                return row
        revision = row.revision
        data = read_wav(db, row)
        manual = row.transcript_manual if row.transcript_source == "manual" else None
        try:
            remote = tts_server.put_voice(row.id, data, manual, row.name, row.owner_id)
        except tts_server.TtsError as exc:
            row.tts_sync_state, row.tts_error = "FAILED", f"{exc.code}: {exc.message}"[:200]
            row.updated_at = now()
            db.commit()
            raise
        except tts_server.TtsUnavailable as exc:
            row.tts_sync_state, row.tts_error = "PENDING", f"{exc.code}"[:200]
            db.commit()
            raise
        db.refresh(row)
        if row.revision != revision:          # changed while uploading: the next sync sends the newer one
            return row
        transcript = remote.get("transcript") or {}
        if transcript.get("asr_text"):
            row.transcript_asr = transcript["asr_text"]
        qc = json.loads(row.qc_json or "{}")
        qc["server"] = remote.get("qc")
        row.qc_json = json.dumps(qc, ensure_ascii=False)
        row.tts_rev = remote.get("rev")
        row.tts_compat = (remote.get("prompt") or {}).get("compat")
        row.tts_sync_state, row.tts_synced_revision, row.tts_error = "SYNCED", revision, None
        row.tts_synced_at = now()
        db.commit()
        _verified[row.id] = (revision, time.monotonic())
        log.info("voice profile %s synced (revision %s)", row.id, revision)
        return row


def try_sync(db: Session, row: VoiceProfile) -> None:
    """Best effort in a user request: the profile is saved either way; the background loop retries."""
    try:
        ensure_synced(db, row)
    except (tts_server.TtsUnavailable, tts_server.TtsError) as exc:
        log.info("voice profile %s not synced now: %s", row.id, getattr(exc, "code", type(exc).__name__))


def sync_pending(limit: int = 5) -> int:
    """Background: push profiles that are not on the voice server at their current revision."""
    if not tts_server.configured() or not tts_server.ready().get("online"):
        return 0
    done = 0
    with SessionLocal() as db:
        rows = db.scalars(select(VoiceProfile).where(VoiceProfile.tts_sync_state != "SYNCED").limit(limit)).all()
        rows += [r for r in db.scalars(select(VoiceProfile).where(VoiceProfile.tts_sync_state == "SYNCED")).all()
                 if r.tts_synced_revision != r.revision][:limit]
        for row in rows:
            if row.tts_sync_state == "FAILED" and row.tts_synced_revision == row.revision:
                continue
            try:
                ensure_synced(db, row)
                done += 1
            except (tts_server.TtsUnavailable, tts_server.TtsError):
                continue
    return done


def forget(profile_id: str) -> None:
    _verified.pop(profile_id, None)


# --- API --------------------------------------------------------------------------------------------------
class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    transcript: str | None = Field(default=None, max_length=MAX_TRANSCRIPT,
                                   description="Manual transcript; null or empty returns to Whisper")


@router.get("")
def list_profiles(owner: Owner):
    with SessionLocal() as db:
        rows = db.scalars(select(VoiceProfile).where(VoiceProfile.owner_id == owner["id"])
                          .order_by(VoiceProfile.created_at)).all()
        return {"profiles": [view(r) for r in rows], "tts_configured": tts_server.configured()}


@router.post("", status_code=201)
async def create_profile(owner: Owner, wav: UploadFile = File(...), name: str = Form(..., min_length=1, max_length=100),
                         transcript: str | None = Form(default=None)):
    """Upload a reference WAV (1-20 s). Without a transcript the voice server uses Whisper, like the official demo."""
    data = await wav.read(MAX_WAV_BYTES + 1)
    info = inspect_wav(data)
    manual = clean_transcript(transcript)
    profile_id = new_id()
    from fastapi.concurrency import run_in_threadpool
    return await run_in_threadpool(_create, owner["id"], profile_id, name.strip(), data, info, manual)


def _create(owner_id: str, profile_id: str, name: str, data: bytes, info: dict, manual: str | None) -> dict:
    with SessionLocal() as db:
        provider_name, object_id = _store(db, owner_id, profile_id, 1, data)
        row = VoiceProfile(id=profile_id, owner_id=owner_id, name=name, storage_provider=provider_name,
                           storage_object_id=object_id, wav_sha256=info["sha256"], wav_bytes=info["bytes"],
                           sample_rate=info["sample_rate"], channels=info["channels"], duration_s=info["duration_s"],
                           transcript_source="manual" if manual else "asr", transcript_manual=manual, revision=1,
                           qc_json=json.dumps({"local": info}, ensure_ascii=False))
        db.add(row)
        db.commit()
        try_sync(db, row)
        db.refresh(row)
        return view(row)


@router.get("/{profile_id}")
def get_profile(profile_id: str, owner: Owner):
    with SessionLocal() as db:
        return view(owned(db, owner["id"], profile_id))


@router.patch("/{profile_id}")
def patch_profile(profile_id: str, body: ProfilePatch, owner: Owner):
    """Rename, or change the transcript (new revision: the voice server rebuilds the prompt)."""
    with owner_voice_lock(owner["id"]):
        with SessionLocal() as db:
            row = owned(db, owner["id"], profile_id)
            if "name" in body.model_fields_set and body.name:
                row.name = body.name.strip()
            if "transcript" in body.model_fields_set:
                manual = clean_transcript(body.transcript)
                source = "manual" if manual else "asr"
                if (source, manual) != (row.transcript_source, row.transcript_manual):
                    require_voice_mutable(db, owner["id"], row.id)
                    row.transcript_source, row.transcript_manual = source, manual
                    row.revision += 1
                    row.tts_sync_state = "PENDING"
                    forget(row.id)
            row.updated_at = now()
            db.commit()
            if row.tts_synced_revision != row.revision:
                try_sync(db, row)
                db.refresh(row)
            return view(row)


@router.put("/{profile_id}/wav")
async def replace_wav(profile_id: str, owner: Owner, wav: UploadFile = File(...)):
    """New reference WAV: new revision, transcript back to Whisper (a manual text belonged to the old clip)."""
    data = await wav.read(MAX_WAV_BYTES + 1)
    info = inspect_wav(data)
    from fastapi.concurrency import run_in_threadpool
    return await run_in_threadpool(_replace, owner["id"], profile_id, data, info)


def _replace(owner_id: str, profile_id: str, data: bytes, info: dict) -> dict:
    with owner_voice_lock(owner_id):
        with SessionLocal() as db:
            row = owned(db, owner_id, profile_id)
            if info["sha256"] == row.wav_sha256:
                return view(row)
            require_voice_mutable(db, owner_id, row.id)
            old = (row.storage_provider, row.storage_object_id)
            provider_name, object_id = _store(db, owner_id, row.id, row.revision + 1, data)
            row.storage_provider, row.storage_object_id = provider_name, object_id
            row.wav_sha256, row.wav_bytes = info["sha256"], info["bytes"]
            row.sample_rate, row.channels, row.duration_s = info["sample_rate"], info["channels"], info["duration_s"]
            row.transcript_source, row.transcript_manual, row.transcript_asr = "asr", None, None
            row.qc_json = json.dumps({"local": info}, ensure_ascii=False)
            row.revision += 1
            row.tts_sync_state = "PENDING"
            row.updated_at = now()
            db.commit()
            forget(row.id)
            _delete_object(db, *old)
            try_sync(db, row)
            db.refresh(row)
            return view(row)


@router.post("/{profile_id}/sync")
def sync_profile(profile_id: str, owner: Owner):
    """Push this profile to the voice server now (e.g. after replacing the GPU machine)."""
    with SessionLocal() as db:
        row = owned(db, owner["id"], profile_id)
        try:
            ensure_synced(db, row, force=True)
        except tts_server.TtsUnavailable as exc:
            raise HTTPException(503, f"{exc.code}: {exc.message}", headers={"X-Sarae-Error": exc.code}) from exc
        except tts_server.TtsError as exc:
            raise HTTPException(422 if exc.status < 500 else 502, f"{exc.code}: {exc.message}",
                                headers={"X-Sarae-Error": exc.code}) from exc
        return view(row)


@router.get("/{profile_id}/wav")
def download_wav(profile_id: str, owner: Owner):
    """The original reference WAV, byte for byte (for listening in the web)."""
    from app.storage_providers import provider
    with SessionLocal() as db:
        row = owned(db, owner["id"], profile_id)
        stream = provider(row.storage_provider, db).open_read(row.storage_object_id)

    def chunks():
        try:
            yield from stream.iter()
        finally:
            stream.close()
    return StreamingResponse(chunks(), media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.delete("/{profile_id}", status_code=204)
def delete_profile(profile_id: str, owner: Owner):
    with owner_voice_lock(owner["id"]):
        from app.ai_voice import voice_config
        with SessionLocal() as db:
            row = owned(db, owner["id"], profile_id)
            config = voice_config(owner["id"])
            require_voice_mutable(db, owner["id"], row.id)
            if config.provider == "omnivoice" and config.voice == row.id:
                raise HTTPException(409, "Giọng này đang được chọn trong Voice Settings; chọn giọng khác trước khi xoá")
            old = (row.storage_provider, row.storage_object_id)
            db.delete(row)
            db.commit()
            forget(profile_id)
            _delete_object(db, *old)
        try:
            tts_server.delete_voice(profile_id)
        except (tts_server.TtsUnavailable, tts_server.TtsError) as exc:
            log.info("voice %s not removed from the voice server now: %s", profile_id, getattr(exc, "code", ""))
        return None


