"""Serialize owner voice mutations with session start in the single-process Backend.

Sessions currently read owner settings when each utterance is created (only jobs snapshot
voice revision). Pending starts must therefore protect the same settings as RUNNING.
"""
import threading
from contextlib import contextmanager
from fastapi import HTTPException
from sqlalchemy import or_, select

_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


@contextmanager
def owner_voice_lock(owner_id: str):
    with _locks_guard:
        lock = _locks.setdefault(owner_id, threading.RLock())
    with lock:
        yield


def require_voice_mutable(db, owner_id: str, profile_id: str | None = None):
    from app.live_sessions import LiveSession
    from app.ai_voice import voice_config
    active = db.scalars(select(LiveSession.id).where(LiveSession.owner_id == owner_id,
        or_(LiveSession.status == 'RUNNING', (LiveSession.status == 'DRAFT') & LiveSession.agent_id.is_not(None)))).all()
    if not active:
        return
    if profile_id:
        from app.tts_orchestrator import TtsUtterance
        config = voice_config(owner_id)
        referenced = db.scalar(select(TtsUtterance.id).where(TtsUtterance.owner_id == owner_id,
            TtsUtterance.session_id.in_(active), TtsUtterance.voice_id == profile_id).limit(1))
        if not referenced and not (config.provider == 'omnivoice' and config.voice == profile_id):
            return
    raise HTTPException(409, 'Giọng đang được dùng bởi phiên RUNNING hoặc đang bắt đầu; kết thúc phiên trước khi thay đổi.',
                        headers={'X-Sarae-Error': 'VOICE_IN_USE'})
