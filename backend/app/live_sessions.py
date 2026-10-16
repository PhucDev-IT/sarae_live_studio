"""Operator-managed LIVE sessions, product queues and history (Phase 1)."""
from datetime import datetime, timezone

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
    product_ids: list[int] = Field(min_length=1, max_length=300)

class SessionProducts(BaseModel):
    product_ids: list[int] = Field(min_length=1, max_length=300)

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

def ids_for(db, session_id):
    return list(db.scalars(select(SessionProduct.product_id).where(
        SessionProduct.session_id == session_id).order_by(SessionProduct.sort_order)).all())

def serialize(db, item):
    return {"id": item.id, "name": item.name, "status": item.status,
            "product_ids": ids_for(db, item.id), "current_product_id": item.current_product_id,
            "created_at": item.created_at, "started_at": item.started_at, "ended_at": item.ended_at}

def event(db, sid, kind, pid=None, detail=""):
    db.add(SessionEvent(session_id=sid, event_type=kind, product_id=pid, detail=detail))

def record_product_switch(session_id: int, product_id: int):
    """Called only after OBS has successfully switched, including deferred transitions."""
    with SessionLocal() as db:
        item = db.get(LiveSession, session_id)
        if item is not None and item.status == "RUNNING":
            item.current_product_id = product_id
            event(db, session_id, "PRODUCT_SWITCHED", product_id)
            db.commit()


def require_running_owner(item):
    if item.status != "RUNNING" or not state.active or state.session_mode != "product" or state.session_id != item.id:
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
        db.add_all(SessionProduct(session_id=item.id, product_id=pid, sort_order=i)
                   for i, pid in enumerate(body.product_ids))
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
                if state.product_id not in body.product_ids:
                    raise HTTPException(409, "Không thể gỡ sản phẩm đang phát")
                if state.pending_product_id is not None and state.pending_product_id not in body.product_ids:
                    state.pending_product_id = None
            db.execute(delete(SessionProduct).where(SessionProduct.session_id == session_id))
            db.add_all(SessionProduct(session_id=session_id, product_id=pid, sort_order=i)
                       for i, pid in enumerate(body.product_ids))
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
            pid = body.product_id or ids[0]
            if pid not in ids:
                raise HTTPException(422, "Sản phẩm không nằm trong phiên")
            # Validate queue and OBS playback before marking the database session RUNNING.
            queue = state.product_queue(pid)
            state.ids = queue
            state.loop = True
            state.playlist_id = None
            state.pending_product_id = None
            state.session_mode = "product"
            state.product_id = pid
            try:
                result = state.play(0)
            except Exception:
                state.active = False
                state.session_id = None
                raise
            state.session_id = session_id
            item.status = "RUNNING"
            item.current_product_id = pid
            item.started_at = now()
            event(db, session_id, "STARTED", pid)
            db.commit()
            return {"session": serialize(db, item), "playback": result}

@router.post("/{session_id}/switch")
def switch_session_product(session_id: int, body: SwitchInput):
    with state.lock:
        with SessionLocal() as db:
            item = get_session(db, session_id)
            require_running_owner(item)
            if body.product_id not in ids_for(db, session_id):
                raise HTTPException(422, "Sản phẩm chưa được thêm vào phiên")
            state.product_queue(body.product_id)
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

@router.post("/{session_id}/finish")
def finish_session(session_id: int):
    with state.lock:
        with SessionLocal() as db:
            item = get_session(db, session_id)
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
