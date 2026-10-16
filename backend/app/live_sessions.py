"""Operator-managed LIVE sessions, product queues and history (Phase 1)."""
from datetime import datetime, timezone
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from app.catalog import Product
from app.database import Base, SessionLocal
from app.live import state

router = APIRouter(prefix="/api/live/sessions", tags=["LIVE Sessions"])

def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class LiveSession(Base):
    __tablename__ = "live_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    current_product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class SessionProduct(Base):
    __tablename__ = "live_session_products"
    __table_args__ = (UniqueConstraint("session_id", "product_id", name="uq_live_session_product"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("live_sessions.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer)

class SessionEvent(Base):
    __tablename__ = "live_session_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("live_sessions.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class SessionInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # A DRAFT may start empty; the lineup is built afterwards and checked when the session starts.
    product_ids: list[int] = Field(default_factory=list, max_length=300)

class SessionProducts(BaseModel):
    product_ids: list[int] = Field(default_factory=list, max_length=300)

class SessionRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)

class StartInput(BaseModel):
    product_id: int | None = None

class SwitchInput(BaseModel):
    product_id: int
    mode: str = Field(default="immediate", pattern="^(immediate|after_current)$")

def ensure_products(db, ids):
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Danh sách chứa sản phẩm trùng nhau")
    found = set(db.scalars(select(Product.id).where(Product.id.in_(ids))).all())
    if found != set(ids):
        raise HTTPException(422, "Có sản phẩm không tồn tại")

def get_session(db, session_id):
    item = db.get(LiveSession, session_id)
    if item is None:
        raise HTTPException(404, "Không tìm thấy phiên LIVE")
    return item

def replace_products(db, session_id, product_ids):
    db.execute(delete(SessionProduct).where(SessionProduct.session_id == session_id))
    db.add_all(SessionProduct(session_id=session_id, product_id=pid, sort_order=i)
               for i, pid in enumerate(product_ids))

def ids_for(db, session_id):
    return list(db.scalars(select(SessionProduct.product_id).where(
        SessionProduct.session_id == session_id).order_by(SessionProduct.sort_order)).all())

def serialize(db, item):
    return {"id": item.id, "name": item.name, "status": item.status,
            "product_ids": ids_for(db, item.id), "current_product_id": item.current_product_id,
            "created_at": item.created_at, "started_at": item.started_at, "ended_at": item.ended_at}

def event(db, sid, kind, pid=None, detail=""):
    db.add(SessionEvent(session_id=sid, event_type=kind, product_id=pid, detail=detail))

def record_event(session_id: int, kind: str, product_id: int | None = None, detail: str = ""):
    with SessionLocal() as db:
        event(db, session_id, kind, product_id, detail)
        db.commit()

def record_product_switch(session_id: int, product_id: int):
    """Called only after OBS has successfully switched, including deferred transitions."""
    with SessionLocal() as db:
        item = db.get(LiveSession, session_id)
        if item is not None and item.status == "RUNNING":
            item.current_product_id = product_id
            event(db, session_id, "PRODUCT_SWITCHED", product_id)
            db.commit()


def is_running_owner(item):
    return item.status == "RUNNING" and state.active and state.session_mode == "product" and state.session_id == item.id

def begin_playback(session_id, product_id, started_at):
    """Point OBS at a product's videos and attach the managed session; the caller holds state.lock."""
    state.ids = state.product_queue(product_id)
    state.session_started_at = started_at
    state.transition_count = state.error_count = state.recovery_count = 0
    state.loop = True
    state.playlist_id = None
    state.pending_product_id = None
    state.session_mode = "product"
    state.session_id = None
    state.product_id = product_id
    try:
        result = state.play(0)
    except Exception:
        state.active = False
        raise
    state.session_id = session_id
    return result

def require_running_owner(item):
    if not is_running_owner(item):
        raise HTTPException(409, "Phiên này không phải phiên đang phát trong Backend hiện tại")

@router.get("")
def list_sessions():
    with SessionLocal() as db:
        return [serialize(db, x) for x in db.scalars(select(LiveSession).order_by(LiveSession.id.desc())).all()]

@router.post("", status_code=201)
def create_session(body: SessionInput):
    with SessionLocal() as db:
        ensure_products(db, body.product_ids)
        item = LiveSession(name=body.name.strip(), status="DRAFT")
        db.add(item)
        db.flush()
        replace_products(db, item.id, body.product_ids)
        event(db, item.id, "CREATED")
        db.commit()
        return serialize(db, item)

@router.get("/{session_id}")
def session_detail(session_id: int):
    with SessionLocal() as db:
        return serialize(db, get_session(db, session_id))

@router.put("/{session_id}/products")
def update_products(session_id: int, body: SessionProducts):
    with state.lock:
        with SessionLocal() as db:
            item = get_session(db, session_id)
            if item.status == "ENDED":
                raise HTTPException(409, "Không thể chỉnh sửa phiên đã kết thúc")
            ensure_products(db, body.product_ids)
            if item.status == "RUNNING":
                require_running_owner(item)
                if not body.product_ids:
                    raise HTTPException(422, "Phiên đang LIVE phải còn ít nhất một sản phẩm")
                if state.product_id not in body.product_ids:
                    raise HTTPException(409, "Không thể gỡ sản phẩm đang phát")
                if state.pending_product_id is not None and state.pending_product_id not in body.product_ids:
                    state.pending_product_id = None
            replace_products(db, session_id, body.product_ids)
            event(db, session_id, "QUEUE_UPDATED")
            db.commit()
            return serialize(db, item)

@router.post("/{session_id}/start")
def start_session(session_id: int, body: StartInput):
    with state.lock:
        if state.active:
            raise HTTPException(409, "OBS đang phát một phiên khác; hãy Stop trước")
        with SessionLocal() as db:
            item = get_session(db, session_id)
            if item.status != "DRAFT":
                raise HTTPException(409, "Chỉ bắt đầu được phiên DRAFT")
            ids = ids_for(db, session_id)
            if not ids:
                raise HTTPException(409, "Phiên chưa có sản phẩm nào")
            pid = body.product_id or ids[0]
            if pid not in ids:
                raise HTTPException(422, "Sản phẩm không nằm trong phiên")
            # Validate queue and OBS playback before marking the database session RUNNING.
            result = begin_playback(session_id, pid, time.time())
            item.status = "RUNNING"
            item.current_product_id = pid
            item.started_at = now()
            event(db, session_id, "STARTED", pid)
            db.commit()
            return {"session": serialize(db, item), "playback": result}

@router.post("/{session_id}/resume")
def resume_session(session_id: int, body: StartInput):
    """Re-attach OBS playback to a RUNNING session whose playback was lost (Backend restart or crash).

    Plays `product_id` (any catalog product, added to the session if missing) or the product that was live.
    """
    with state.lock:
        if state.active:
            raise HTTPException(409, "OBS đang phát một phiên khác; hãy Stop trước")
        with SessionLocal() as db:
            item = get_session(db, session_id)
            if item.status != "RUNNING":
                raise HTTPException(409, "Chỉ tiếp tục được phiên đang LIVE bị ngắt")
            ids = ids_for(db, session_id)
            pid = body.product_id or item.current_product_id or (ids[0] if ids else None)
            if pid is None:
                raise HTTPException(409, "Phiên chưa có sản phẩm nào")
            ensure_products(db, [pid])
            started = item.started_at.replace(tzinfo=timezone.utc).timestamp() if item.started_at else time.time()
            result = begin_playback(session_id, pid, started)
            if pid not in ids:
                db.add(SessionProduct(session_id=session_id, product_id=pid, sort_order=len(ids)))
            item.current_product_id = pid
            event(db, session_id, "RESUMED", pid)
            db.commit()
            return {"session": serialize(db, item), "playback": result}

@router.post("/{session_id}/switch")
def switch_session_product(session_id: int, body: SwitchInput):
    with state.lock:
        with SessionLocal() as db:
            item = get_session(db, session_id)
            require_running_owner(item)
            # The lineup is a prepared pool, not a fixed order: any catalog product with videos can go live.
            # Validate its videos first, then add it so the session's history and list stay complete.
            ids = ids_for(db, session_id)
            state.product_queue(body.product_id)
            if body.product_id not in ids:
                db.add(SessionProduct(session_id=session_id, product_id=body.product_id, sort_order=len(ids)))
                event(db, session_id, "PRODUCT_ADDED", body.product_id, "during_live=true")
                db.commit()
            if body.mode == "after_current" and body.product_id != state.product_id:
                state.pending_product_id = body.product_id
                event(db, session_id, "SWITCH_QUEUED", body.product_id)
                db.commit()
                return {"ok": True, "pending_product_id": body.product_id}
            if state.product_id == body.product_id:
                state.pending_product_id = None
                return {"ok": True, "unchanged": True}
            result = state.switch_product(body.product_id)
            db.refresh(item)
            return {"ok": True, "playback": result, "session": serialize(db, item)}

@router.patch("/{session_id}")
def rename_session(session_id: int, body: SessionRename):
    with SessionLocal() as db:
        item = get_session(db, session_id)
        if item.status == "ENDED":
            raise HTTPException(409, "Không thể đổi tên phiên đã kết thúc")
        item.name = body.name.strip()
        db.commit()
        return serialize(db, item)

@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: int):
    with state.lock:
        with SessionLocal() as db:
            item = get_session(db, session_id)
            if item.status == "RUNNING":
                raise HTTPException(409, "Hãy kết thúc phiên trước khi xóa")
            # SQLite does not enforce ON DELETE CASCADE unless foreign keys are enabled; delete children explicitly.
            db.execute(delete(SessionEvent).where(SessionEvent.session_id == session_id))
            db.execute(delete(SessionProduct).where(SessionProduct.session_id == session_id))
            db.delete(item)
            db.commit()

@router.post("/{session_id}/duplicate", status_code=201)
def duplicate_session(session_id: int):
    """New DRAFT with the same lineup, e.g. to rerun an ended LIVE."""
    with SessionLocal() as db:
        source = get_session(db, session_id)
        existing = set(db.scalars(select(Product.id)).all())
        ids = [pid for pid in ids_for(db, session_id) if pid in existing]
        item = LiveSession(name=f"{source.name} (bản sao)"[:200], status="DRAFT")
        db.add(item)
        db.flush()
        replace_products(db, item.id, ids)
        event(db, item.id, "CREATED", detail=f"duplicate_of={session_id}")
        db.commit()
        return serialize(db, item)

@router.post("/{session_id}/finish")
def finish_session(session_id: int):
    with state.lock:
        with SessionLocal() as db:
            item = get_session(db, session_id)
            if item.status == "RUNNING" and not is_running_owner(item):
                # Left RUNNING by a Backend restart: playback is gone, so just close the record.
                item.status = "ENDED"
                item.ended_at = now()
                event(db, session_id, "ENDED", item.current_product_id, "stale=true")
                db.commit()
                return serialize(db, item)
            require_running_owner(item)
            state.stop()
            state.session_id = None
            item.status = "ENDED"
            item.ended_at = now()
            item.current_product_id = state.product_id
            event(db, session_id, "ENDED", item.current_product_id,
                  f"transitions={state.transition_count};errors={state.error_count};recoveries={state.recovery_count}")
            db.commit()
            return serialize(db, item)

@router.get("/{session_id}/events")
def list_events(session_id: int):
    with SessionLocal() as db:
        get_session(db, session_id)
        rows = db.scalars(select(SessionEvent).where(
            SessionEvent.session_id == session_id).order_by(SessionEvent.id)).all()
        return [{"id": row.id, "event_type": row.event_type, "product_id": row.product_id,
                 "detail": row.detail, "created_at": row.created_at} for row in rows]
