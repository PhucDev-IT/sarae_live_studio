"""The single path from Sarae to G-OmniVoice (provider "omnivoice"): utterances, jobs, Agent commands, readiness,
LIVE leases and the audio relay. Edge and ElevenLabs never come here. Design: docs/TTS_SERVER.md sections 4-7, 17.

Utterance = one decision to say one clip (body, intro, or a prepared sentence). Its row links owner, Agent, LIVE
session, reply, voice + revision, TTS job and Agent command; it never stores the text or any token.

    NEW --create job (Idempotency-Key = utterance id)--> JOB_CREATED --create command (key tts:<id>)--> SENT
     |                                                     |-- command refused --> ABORTED (job cancelled)
     |-- voice server refused / unreachable --> FAILED     '-- Backend restarted before the command --> ORPHANED
    SENT --LIVE ended / Dừng giọng nói--> CANCELLED (jobs cancelled on the voice server)
Playback (PLAYING / PLAYED / expired on the Agent) arrives with Phase 4 Agent events; until then the GPU job state
is mirrored in tts_job_state and SENT only means "command queued for the Agent".
Guarantees in Phase 3: one utterance -> at most one TTS job (same key on retry) and at most one tts_speak command
(same command key); a timeout never creates a second utterance by itself. At-most-once *playback* also needs the
Agent's utterance dedup (Phase 4).

Read tokens returned by the voice server are kept in memory only (never in a table, command payload, log or Web
response). An Agent gets one through POST /api/agents/tts/audio/{utterance}/token, or downloads through the relay
(GET /api/agents/tts/audio/{utterance}), both authenticated with its existing Agent key.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from app import tts_server
from app.config import settings
from app.database import Base, SessionLocal
from app.ownership import Owner
from app.storage_models import aware, new_id

log = logging.getLogger(__name__)
agent_router = APIRouter(prefix="/api/agents/tts", tags=["Windows Agents"])
owner_router = APIRouter(prefix="/api/ai/voice", tags=["AI LIVE Voice"])

PRESET = "demo_default"
OPEN_STATES = ("NEW", "JOB_CREATED", "SENT", "PLAYING")
DEAD_STATES = ("ABORTED", "CANCELLED", "FAILED", "EXPIRED", "ORPHANED")
ORPHAN_AFTER_S = 120
RELAY_GRACE_S = 30
LOOP_SECONDS = 15


def now() -> datetime:
    return datetime.now(timezone.utc)


class TtsUtterance(Base):
    __tablename__ = "tts_utterances"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_id: Mapped[str] = mapped_column(String(80), index=True)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    reply_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(20))            # live_reply | manual | prepare
    part: Mapped[str] = mapped_column(String(10))              # body | intro | prepare
    voice_id: Mapped[str] = mapped_column(String(32))
    voice_rev: Mapped[int] = mapped_column(Integer)
    text_sha256: Mapped[str] = mapped_column(String(64))
    chars: Mapped[int] = mapped_column(Integer)
    priority: Mapped[str] = mapped_column(String(10))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(20), default="NEW", index=True)
    tts_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tts_job_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    command_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def ensure_schema(bind=None):
    from app.database import engine
    Base.metadata.create_all(bind=bind or engine, tables=[TtsUtterance.__table__], checkfirst=True)


def view(u: TtsUtterance) -> dict:
    """What the Web may see (no text, no token)."""
    return {"id": u.id, "agent_id": u.agent_id, "session_id": u.session_id, "reply_id": u.reply_id, "source": u.source,
            "part": u.part, "voice_id": u.voice_id, "voice_rev": u.voice_rev, "chars": u.chars, "priority": u.priority,
            "deadline_at": aware(u.deadline_at).isoformat(), "state": u.state, "tts_job_id": u.tts_job_id,
            "tts_job_state": u.tts_job_state, "command_id": u.command_id, "error_code": u.error_code,
            "error_message": u.error_message, "created_at": aware(u.created_at).isoformat(),
            "updated_at": aware(u.updated_at).isoformat()}


class SpeakRefused(Exception):
    """OmniVoice could not take this sentence. `fallback_ok` is False for decisions that must not be answered by
    another voice (LIVE ended, sentence expired or cancelled, invalid input)."""

    def __init__(self, code: str, message: str, fallback_ok: bool = True, status: int = 503):
        super().__init__(message)
        self.code, self.message, self.fallback_ok, self.status = code, message, fallback_ok, status


# --- read tokens: memory only ----------------------------------------------------------------------
_tokens: dict[str, tuple[str, float]] = {}
_tokens_lock = threading.Lock()


def _keep_token(job: dict) -> None:
    token, expires = job.get("read_token"), job.get("read_token_expires_at")
    if not token or not expires:
        return
    try:
        exp = datetime.fromisoformat(expires).timestamp()
    except ValueError:
        return
    with _tokens_lock:
        _tokens[job["job_id"]] = (token, exp)


def _purge_tokens() -> None:
    t = time.time()
    with _tokens_lock:
        for job_id in [j for j, (_, exp) in _tokens.items() if exp < t]:
            del _tokens[job_id]


# --- Agent capabilities ----------------------------------------------------------------------------
def agent_capabilities(agent_id: str) -> dict:
    """From the Agent's last heartbeat: voice.capabilities (Phase 4 Agents report it; older ones do not)."""
    import json
    from app.cloud_agents import _db
    with _db() as db:
        row = db.execute("SELECT status_json FROM agent_status WHERE agent_id=?", (agent_id,)).fetchone()
    try:
        status = json.loads(row["status_json"]) if row else {}
    except ValueError:
        status = {}
    caps = (status.get("voice") or {}).get("capabilities")
    return caps if isinstance(caps, dict) else {}


def supports_omnivoice(agent_id: str) -> bool:
    caps = agent_capabilities(agent_id)
    return "omnivoice" in (caps.get("providers") or []) and int(caps.get("tts_job") or 0) >= 1


# --- configuration helpers ---------------------------------------------------------------------------
def selected(owner_id: str) -> bool:
    from app.ai_voice import voice_config
    return voice_config(owner_id).provider == "omnivoice"


def fallback(owner_id: str, context: str) -> tuple[str, dict | None]:
    """(mode, provider spec) configured by the owner for live_reply | prepare | manual. Default ("none", None)."""
    from app.ai_voice import voice_config
    policy = voice_config(owner_id).fallback
    mode = getattr(policy, context)
    return mode, (policy.provider if mode == "provider" else None)


def _profile(db, owner_id: str):
    from app.ai_voice import voice_config
    from app.voice_profiles import VoiceProfile
    config = voice_config(owner_id)
    row = db.get(VoiceProfile, config.voice)
    if row is None or row.owner_id != owner_id:
        raise SpeakRefused("VOICE_NOT_FOUND", "Giọng G-OmniVoice đã chọn không còn tồn tại", fallback_ok=True, status=409)
    return row


def _synced(db, row) -> None:
    from app.voice_profiles import ensure_synced
    try:
        ensure_synced(db, row)
    except tts_server.TtsUnavailable as exc:
        raise SpeakRefused(exc.code, exc.message) from exc
    except tts_server.TtsError as exc:
        raise SpeakRefused(exc.code, f"Không đồng bộ được giọng: {exc.message}", status=502) from exc


def _session_running(owner_id: str, session_id: int | None) -> None:
    if session_id is None:
        return
    from app.live_sessions import LiveSession
    with SessionLocal() as db:
        item = db.get(LiveSession, session_id)
        if item is None or item.owner_id != owner_id or item.status != "RUNNING":
            raise SpeakRefused("SESSION_ENDED", "Phiên LIVE đã kết thúc", fallback_ok=False, status=409)


def _ready_or_refuse() -> None:
    if not tts_server.configured():
        raise SpeakRefused("TTS_NOT_CONFIGURED", "Chưa cấu hình máy chủ giọng nói G-OmniVoice")
    health = tts_server.ready()
    if not health["online"]:
        raise SpeakRefused("TTS_BACKEND_UNREACHABLE", "Máy chủ giọng nói G-OmniVoice không kết nối được")
    if health["state"] != "ready":
        raise SpeakRefused((health.get("error") or {}).get("code") or "TTS_NOT_READY",
                           f"Máy chủ giọng nói đang ở trạng thái {health['state']}")


# --- jobs ---------------------------------------------------------------------------------------------
def _job(db, u: TtsUtterance, text: str) -> dict:
    """Create (or, on retry, get back) the TTS job of utterance `u`; records job id and state."""
    tags = [f"agent:{u.agent_id}", f"owner:{u.owner_id[:60]}"] + ([f"session:{u.session_id}"] if u.session_id else [])
    resynced = False
    while True:
        try:
            job = tts_server.create_job(idempotency_key=u.id, voice_id=u.voice_id, text=text, priority=u.priority,
                                        deadline_at=aware(u.deadline_at).isoformat(), reader_agent_id=u.agent_id,
                                        owner_ref=u.owner_id, tags=tags, preset=PRESET)
            break
        except tts_server.TtsError as exc:
            if exc.code in ("VOICE_NOT_FOUND", "VOICE_STALE") and not resynced:
                from app.voice_profiles import VoiceProfile, ensure_synced
                ensure_synced(db, db.get(VoiceProfile, u.voice_id), force=True)
                resynced = True
                continue
            raise
    _keep_token(job)
    u.tts_job_id, u.tts_job_state, u.state, u.updated_at = job["job_id"], job["state"], "JOB_CREATED", now()
    db.commit()
    return job


def _fail(db, rows: list[TtsUtterance], state: str, code: str, message: str) -> None:
    for u in rows:
        if u.state in DEAD_STATES:
            continue
        if u.tts_job_id:
            try:
                tts_server.cancel_job(u.tts_job_id)
            except (tts_server.TtsUnavailable, tts_server.TtsError):
                pass                                   # the job expires on its own; reconcile retries the cancel
        u.state, u.error_code, u.error_message, u.updated_at = state, code[:40], message[:200], now()
    db.commit()


def _new(db, *, owner_id, agent_id, session_id, reply_id, source, part, profile, text, priority, deadline) -> TtsUtterance:
    u = TtsUtterance(id=new_id(), owner_id=owner_id, agent_id=agent_id, session_id=session_id, reply_id=reply_id,
                     source=source, part=part, voice_id=profile.id, voice_rev=profile.revision,
                     text_sha256=hashlib.sha256(text.encode()).hexdigest(), chars=len(text), priority=priority,
                     deadline_at=deadline)
    db.add(u)
    return u


def _audio_path() -> str:
    return "auto" if settings.sarae_tts_agent_url.strip() else "relay"


def speak(owner_id: str, agent_id: str, *, text: str, intro: str | None, ttl_seconds: float, duck: bool,
          session_id: int | None, reply_id: int | None, source: str) -> dict:
    """Body (+ intro) -> TTS jobs -> one tts_speak command. Raises SpeakRefused or HTTPException (command)."""
    from app.cloud_agents import create_command
    from fastapi import HTTPException as _HTTPException
    _session_running(owner_id, session_id)
    if not supports_omnivoice(agent_id):
        raise SpeakRefused("AGENT_NO_OMNIVOICE", "Windows Agent chưa hỗ trợ G-OmniVoice (cần cập nhật Agent)", status=409)
    _ready_or_refuse()
    deadline = now() + timedelta(seconds=max(5.0, min(300.0, ttl_seconds)))
    with SessionLocal() as db:
        profile = _profile(db, owner_id)
        _synced(db, profile)
        parts = [("body", text)] + ([("intro", intro)] if intro else [])
        rows = [_new(db, owner_id=owner_id, agent_id=agent_id, session_id=session_id, reply_id=reply_id, source=source,
                     part=part, profile=profile, text=t, priority="live", deadline=deadline) for part, t in parts]
        db.commit()
        jobs = {}
        for u, (_, t) in zip(rows, parts):
            try:
                jobs[u.part] = _job(db, u, t)
            except tts_server.TtsUnavailable as exc:
                _fail(db, rows, "FAILED", exc.code, exc.message)
                raise SpeakRefused(exc.code, exc.message) from exc
            except tts_server.TtsError as exc:
                _fail(db, rows, "FAILED", exc.code, exc.message)
                raise SpeakRefused(exc.code, exc.message or exc.code, status=502) from exc
        body = rows[0]
        tts = {"utterance_id": body.id, "job_id": body.tts_job_id, "voice_rev": profile.revision,
               "deadline_at": deadline.isoformat(), "audio_path": _audio_path()}
        if intro:
            tts["intro"] = {"utterance_id": rows[1].id, "job_id": rows[1].tts_job_id}
        payload = {"text": text, "provider": "omnivoice", "model": PRESET, "voice": profile.id, "rate": 0,
                   "settings": {}, "ttl_seconds": round(max(5.0, min(300.0, ttl_seconds)), 1), "duck": duck, "tts": tts}
        mode, spec = fallback(owner_id, source if source in ("manual", "prepare") else "live_reply")
        payload["fallback"] = {"mode": "provider", "provider": spec} if mode == "provider" and spec else {"mode": "none"}
        if intro:
            payload["intro"] = intro
        try:
            command = create_command(agent_id, owner_id, "tts_speak", payload, idempotency_key=f"tts:{body.id}",
                                     session_id=session_id,
                                     context={"module": "tts_orchestrator", "utterances": [u.id for u in rows]})
        except _HTTPException as exc:
            _fail(db, rows, "ABORTED", "COMMAND_FAILED", str(exc.detail))
            raise
        for u in rows:
            u.command_id, u.state, u.updated_at = command.get("command_id"), "SENT", now()
        db.commit()
        return {"command": command, "utterances": [view(u) for u in rows], "voice_rev": profile.revision}


def prepare(owner_id: str, agent_id: str, texts: list[str], session_id: int | None) -> dict:
    """Prepared answers: prepare-priority jobs (result cache makes repeats free) and one tts_prepare command."""
    from app.cloud_agents import create_command
    from fastapi import HTTPException as _HTTPException
    if not supports_omnivoice(agent_id):
        raise SpeakRefused("AGENT_NO_OMNIVOICE", "Windows Agent chưa hỗ trợ G-OmniVoice (cần cập nhật Agent)", status=409)
    _ready_or_refuse()
    deadline = now() + timedelta(minutes=15)
    with SessionLocal() as db:
        profile = _profile(db, owner_id)
        _synced(db, profile)
        rows = [_new(db, owner_id=owner_id, agent_id=agent_id, session_id=session_id, reply_id=None, source="prepare",
                     part="prepare", profile=profile, text=t, priority="prepare", deadline=deadline) for t in texts]
        db.commit()
        kept, refs = [], []
        for u, t in zip(rows, texts):
            try:
                _job(db, u, t)
                kept.append(t)
                refs.append({"utterance_id": u.id, "job_id": u.tts_job_id})
            except tts_server.TtsUnavailable as exc:
                _fail(db, [r for r in rows if r.state != "JOB_CREATED" or r is u], "FAILED", exc.code, exc.message)
                if not kept:
                    raise SpeakRefused(exc.code, exc.message) from exc
                break
            except tts_server.TtsError as exc:          # e.g. queue full / LIVE active: keep what was accepted
                _fail(db, [u], "FAILED", exc.code, exc.message)
        if not kept:
            raise SpeakRefused("TTS_REFUSED", "Máy chủ giọng nói không nhận câu chuẩn bị nào")
        sent = [r for r in rows if r.state == "JOB_CREATED"]
        payload = {"texts": kept, "provider": "omnivoice", "model": PRESET, "voice": profile.id, "rate": 0,
                   "settings": {}, "tts_jobs": refs}
        mode, spec = fallback(owner_id, "prepare")
        payload["fallback"] = {"mode": "provider", "provider": spec} if mode == "provider" and spec else {"mode": "none"}
        try:
            command = create_command(agent_id, owner_id, "tts_prepare", payload, session_id=session_id,
                                     idempotency_key=f"ttsprep:{sent[0].id}",
                                     context={"module": "tts_orchestrator", "utterances": [u.id for u in sent]})
        except _HTTPException as exc:
            _fail(db, sent, "ABORTED", "COMMAND_FAILED", str(exc.detail))
            raise
        for u in sent:
            u.command_id, u.state, u.updated_at = command.get("command_id"), "SENT", now()
        db.commit()
        return {"command": command, "accepted": len(sent), "refused": len(rows) - len(sent)}


def _cancel_where(owner_id: str, condition, code: str, message: str, tag: str | None) -> int:
    with SessionLocal() as db:
        rows = db.scalars(select(TtsUtterance).where(TtsUtterance.owner_id == owner_id, condition,
                                                     TtsUtterance.state.in_(OPEN_STATES))).all()
        if rows and tag and tts_server.configured():
            try:
                tts_server.cancel_tag(tag)
            except (tts_server.TtsUnavailable, tts_server.TtsError) as exc:
                log.info("cancel %s on the voice server failed now: %s", tag, getattr(exc, "code", ""))
        for u in rows:
            u.state, u.error_code, u.error_message, u.updated_at = "CANCELLED", code, message, now()
        db.commit()
        return len(rows)


def cancel_agent(owner_id: str, agent_id: str) -> int:
    """Dừng giọng nói: every open utterance of this Agent is cancelled on the voice server too."""
    return _cancel_where(owner_id, TtsUtterance.agent_id == agent_id, "STOPPED", "Dừng giọng nói", f"agent:{agent_id}")


def session_ended(owner_id: str, session_id: int) -> int:
    """LIVE finished: cancel its unfinished jobs, release its lease; speak() refuses the session from now on."""
    n = _cancel_where(owner_id, TtsUtterance.session_id == session_id, "SESSION_ENDED", "Phiên LIVE đã kết thúc",
                      f"session:{session_id}")
    if (owner_id, session_id) in _leased and tts_server.configured():
        try:
            tts_server.delete_lease(owner_id, str(session_id))
        except (tts_server.TtsUnavailable, tts_server.TtsError):
            pass                                        # expires by itself after its TTL
    _leased.discard((owner_id, session_id))
    return n


# --- readiness and LIVE leases ---------------------------------------------------------------------------
_leased: set[tuple[str, int]] = set()


def readiness(owner_id: str, agent_id: str | None, act: bool = False) -> dict:
    """TTS part of the pre-LIVE check. `act=True` also syncs the voice and warms its prompt; it never takes a
    lease (the lease starts when the session is RUNNING), so pressing Đồng bộ cannot hold the GPU."""
    from app.ai_voice import voice_config
    config = voice_config(owner_id)
    report: dict = {"provider": config.provider, "applicable": config.provider == "omnivoice", "ready": True,
                    "warnings": []}
    if config.provider != "omnivoice":
        return report
    warn = report["warnings"].append
    health = tts_server.ready(max_age=0 if act else 5) if tts_server.configured() else \
        {"online": False, "state": None, "error": {"code": "TTS_NOT_CONFIGURED"}}
    report["service"] = {"configured": tts_server.configured(), "online": health["online"], "state": health["state"],
                         "error": health.get("error")}
    if not health["online"]:
        warn((health.get("error") or {}).get("code") or "TTS_BACKEND_UNREACHABLE")
    elif health["state"] != "ready":
        warn("TTS_NOT_READY")
    report["agent"] = {"agent_id": agent_id, "supports_omnivoice": bool(agent_id and supports_omnivoice(agent_id))}
    if agent_id and not report["agent"]["supports_omnivoice"]:
        warn("AGENT_NO_OMNIVOICE")
    from app.voice_profiles import VoiceProfile, ensure_synced, view as profile_view
    with SessionLocal() as db:
        row = db.get(VoiceProfile, config.voice)
        if row is None or row.owner_id != owner_id:
            warn("VOICE_NOT_FOUND")
            report["voice"] = None
        else:
            if act and health["online"] and health["state"] == "ready":
                try:
                    ensure_synced(db, row, force=False)
                    w = tts_server.warmup(row.id)
                    report["warmup"] = {"cache": w.get("cache"), "ms": w.get("ms")}
                except (tts_server.TtsUnavailable, tts_server.TtsError) as exc:
                    warn(getattr(exc, "code", "TTS_ERROR"))
                db.refresh(row)
            v = profile_view(row)
            report["voice"] = {"profile_id": row.id, "name": row.name, "revision": row.revision, **v["sync"]}
            if not v["sync"]["up_to_date"]:
                warn("VOICE_NOT_SYNCED")
    if health["online"]:
        try:
            adm = tts_server.admission()
            running = (adm.get("gpu") or {}).get("running")
            report["gpu"] = {"busy": (adm.get("gpu") or {}).get("busy"), "running": running,
                             "queued": (adm.get("gpu") or {}).get("queued"), "live_active": adm.get("live_active"),
                             "leases": len(adm.get("leases") or [])}
            if running and running.get("class") in ("long", "benchmark"):
                warn("LONG_JOB_RUNNING")
                report["gpu"]["note"] = ("Một job dài đang chạy trên GPU và không dừng được; câu trả lời LIVE sẽ phải "
                                         f"chờ, ước tính còn khoảng {running.get('estimated_remaining_s')} giây "
                                         "(ước tính, không phải cam kết)")
        except (tts_server.TtsUnavailable, tts_server.TtsError) as exc:
            warn(getattr(exc, "code", "TTS_ERROR"))
    report["ready"] = not report["warnings"]
    return report


def sync_leases() -> None:
    """Every RUNNING session whose owner speaks with OmniVoice holds a lease, renewed each tick; sessions no longer
    running release it. If this Backend dies, the voice server drops the leases after their TTL."""
    if not tts_server.configured():
        return
    from app.ai_voice import voice_config
    from app.live_sessions import LiveSession
    with SessionLocal() as db:
        rows = db.execute(select(LiveSession.id, LiveSession.owner_id).where(LiveSession.status == "RUNNING")).all()
    wanted = {(owner, sid) for sid, owner in rows if voice_config(owner).provider == "omnivoice"}
    for owner, sid in wanted:
        try:
            tts_server.put_lease(owner, str(sid), settings.sarae_tts_lease_ttl_seconds)
            _leased.add((owner, sid))
        except (tts_server.TtsUnavailable, tts_server.TtsError) as exc:
            log.info("LIVE lease for session %s not renewed: %s", sid, getattr(exc, "code", ""))
    for owner, sid in list(_leased - wanted):
        session_ended(owner, sid)


def reconcile() -> int:
    """After a restart or a crash between job and command: utterances still NEW / JOB_CREATED after
    ORPHAN_AFTER_S are ORPHANED and their jobs cancelled (never re-sent). SENT utterances get the GPU job state."""
    cutoff = now() - timedelta(seconds=ORPHAN_AFTER_S)
    changed = 0
    with SessionLocal() as db:
        stale = db.scalars(select(TtsUtterance).where(TtsUtterance.state.in_(("NEW", "JOB_CREATED")),
                                                      TtsUtterance.updated_at < cutoff).limit(50)).all()
        if stale:
            _fail(db, stale, "ORPHANED", "ORPHANED", "Không tạo được lệnh cho Agent; không gửi lại")
            changed += len(stale)
        if tts_server.configured():
            recent = now() - timedelta(hours=1)
            sent = db.scalars(select(TtsUtterance).where(TtsUtterance.state == "SENT", TtsUtterance.created_at > recent,
                                                         TtsUtterance.tts_job_state.in_(("queued", "running")))
                              .limit(20)).all()
            for u in sent:
                try:
                    u.tts_job_state = tts_server.get_job(u.tts_job_id)["state"]
                except tts_server.TtsError as exc:
                    u.tts_job_state = {"JOB_LOST": "lost", "JOB_GONE": "gone"}.get(exc.code, u.tts_job_state)
                except tts_server.TtsUnavailable:
                    break
            db.commit()
    return changed


def tick() -> None:
    _purge_tokens()
    reconcile()
    if tts_server.configured():
        sync_leases()
        from app.voice_profiles import sync_pending
        sync_pending()


_loop: threading.Thread | None = None


def start_loop() -> None:
    global _loop
    if _loop is not None:
        return

    def run():
        while True:
            try:
                tick()
            except Exception:  # noqa: BLE001 - the loop must never die
                log.exception("TTS orchestrator tick failed")
            time.sleep(LOOP_SECONDS)
    _loop = threading.Thread(target=run, name="sarae-tts-orchestrator", daemon=True)
    _loop.start()


# --- Web: readiness and utterances ---------------------------------------------------------------------
from pydantic import BaseModel, Field  # noqa: E402


class ReadinessBody(BaseModel):
    agent_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")


@owner_router.post("/readiness")
def check_readiness(body: ReadinessBody, owner: Owner):
    """Pre-LIVE TTS check: service, model, voice synced at the current revision, prompt warmed, Agent able to play
    OmniVoice, GPU not held by a long job. Takes no lease."""
    from app.owner_agents import owned_agent
    agent = owned_agent(owner, body.agent_id)
    return readiness(owner["id"], agent["id"], act=True)


@owner_router.get("/utterances")
def list_utterances(owner: Owner, agent_id: str | None = None, limit: int = Query(default=50, ge=1, le=200)):
    with SessionLocal() as db:
        q = select(TtsUtterance).where(TtsUtterance.owner_id == owner["id"])
        if agent_id:
            q = q.where(TtsUtterance.agent_id == agent_id)
        rows = db.scalars(q.order_by(TtsUtterance.created_at.desc()).limit(limit)).all()
        return {"utterances": [view(u) for u in rows]}


# --- Agent: relay and read token -------------------------------------------------------------------------
def _agent_utterance(utterance_id: str, agent_id: str, credential: str | None) -> TtsUtterance:
    """The utterance only if it belongs to this authenticated Agent and its owner, and is still worth playing."""
    from app.identity import verify_agent
    agent = verify_agent(agent_id, credential)
    with SessionLocal() as db:
        u = db.get(TtsUtterance, utterance_id) if len(utterance_id) <= 32 else None
        if u is None or u.agent_id != agent_id or u.owner_id != agent["owner_id"]:
            raise HTTPException(404, "Không tìm thấy câu nói", headers={"X-Sarae-Error": "UTTERANCE_NOT_FOUND"})
        if u.state in DEAD_STATES:
            raise HTTPException(410, f"Câu nói đã {u.state}", headers={"X-Sarae-Error": f"UTTERANCE_{u.state}"})
        if not u.tts_job_id:
            raise HTTPException(409, "Câu nói chưa có job", headers={"X-Sarae-Error": "UTTERANCE_NOT_READY"})
        if now() > aware(u.deadline_at) + timedelta(seconds=RELAY_GRACE_S):
            raise HTTPException(410, "Câu nói đã quá hạn", headers={"X-Sarae-Error": "UTTERANCE_EXPIRED"})
        db.expunge(u)
        return u


@agent_router.get("/audio/{utterance_id}")
def relay_audio(utterance_id: str, agent_id: str = Query(pattern=r"^[A-Za-z0-9_-]{1,80}$"),
                wait: float = Query(default=10, ge=0, le=20), x_sarae_agent_key: str | None = Header(None)):
    """Relay: the Agent downloads its own utterance's WAV through the Backend (Agent key). Upstream host and job id
    come from configuration and the database only; the body is streamed with a size cap."""
    u = _agent_utterance(utterance_id, agent_id, x_sarae_agent_key)
    cap = settings.sarae_tts_relay_max_bytes
    ctx = tts_server.open_audio(u.tts_job_id, wait)
    try:
        response = ctx.__enter__()
    except tts_server.TtsUnavailable as exc:
        raise HTTPException(502, exc.message, headers={"X-Sarae-Error": exc.code}) from exc
    except tts_server.TtsError as exc:
        raise HTTPException(exc.status, exc.message, headers={"X-Sarae-Error": exc.code}) from exc
    status = response.status_code
    if status != 200:
        try:
            body = response.read()
        finally:
            ctx.__exit__(None, None, None)
        if status == 202:
            return JSONResponse({"state": "pending"}, 202, headers={"Retry-After": "1"})
        code = "TTS_ERROR"
        try:
            import json
            code = json.loads(body).get("error", {}).get("code", code)
        except ValueError:
            pass
        mapped = {"JOB_CANCELLED": 410, "JOB_EXPIRED": 410, "JOB_LOST": 410, "JOB_GONE": 410, "RESULT_GONE": 410,
                  "JOB_FAILED": 409}.get(code, 502)
        raise HTTPException(mapped, f"Không lấy được audio ({code})", headers={"X-Sarae-Error": code})
    length = response.headers.get("content-length")
    if length and length.isdigit() and int(length) > cap:
        ctx.__exit__(None, None, None)
        raise HTTPException(502, "Audio vượt giới hạn kích thước", headers={"X-Sarae-Error": "AUDIO_TOO_LARGE"})

    def chunks():
        sent = 0
        try:
            for piece in response.iter_bytes(64 * 1024):
                sent += len(piece)
                if sent > cap:
                    log.warning("relay of %s stopped: larger than %s bytes", u.id, cap)
                    return
                yield piece
        except Exception as exc:  # noqa: BLE001 - upstream dropped mid-stream: the Agent sees a short body
            log.warning("relay of %s interrupted: %s", u.id, type(exc).__name__)
        finally:
            ctx.__exit__(None, None, None)
    headers = {"Cache-Control": "no-store", "X-Utterance-Id": u.id}
    if length and length.isdigit():
        headers["Content-Length"] = length
    return StreamingResponse(chunks(), media_type="audio/wav", headers=headers)


@agent_router.post("/audio/{utterance_id}/token")
def audio_token(utterance_id: str, agent_id: str = Query(pattern=r"^[A-Za-z0-9_-]{1,80}$"),
                x_sarae_agent_key: str | None = Header(None)):
    """Direct path (LAN/VPN): the read token for this Agent's own utterance, from memory only. 404 when this
    Backend no longer has it (restart) or no direct URL is configured: the Agent then uses the relay."""
    u = _agent_utterance(utterance_id, agent_id, x_sarae_agent_key)
    base = settings.sarae_tts_agent_url.strip()
    with _tokens_lock:
        held = _tokens.get(u.tts_job_id)
    if not base or not held or held[1] < time.time():
        raise HTTPException(404, "Không có đường tải trực tiếp; dùng relay", headers={"X-Sarae-Error": "USE_RELAY"})
    return JSONResponse({"job_id": u.tts_job_id, "base_url": base, "read_token": held[0],
                         "expires_at": datetime.fromtimestamp(held[1], timezone.utc).isoformat()},
                        headers={"Cache-Control": "no-store"})


def playback_event(agent_id: str, detail: dict):
    """Apply bounded Agent state only to its own utterance; duplicates/late transitions cannot resurrect it."""
    states = {"accepted": "SENT", "waiting_audio": "SENT", "ready": "SENT", "playing": "PLAYING",
              "played": "PLAYED", "expired": "EXPIRED", "cancelled": "CANCELLED", "stopped": "STOPPED", "failed": "FAILED"}
    state = states.get(detail.get("status"))
    if state is None:
        return
    with SessionLocal() as db:
        u = db.get(TtsUtterance, str(detail.get("utterance_id", "")))
        if not u or u.agent_id != agent_id or u.tts_job_id != detail.get("job_id"):
            return
        if u.state in (*DEAD_STATES, "PLAYED", "STOPPED") or (u.state == "PLAYING" and state == "SENT"):
            return
        u.state, u.updated_at = state, now()
        u.error_code = str(detail["error_code"])[:40] if detail.get("error_code") else None
        db.commit()
