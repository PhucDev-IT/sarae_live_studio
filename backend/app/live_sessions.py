"""Operator-managed LIVE sessions, product queues and history (Phase 1)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.identity import current_owner
from app.ownership import Owner, owned_ids
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from app.catalog import Product
from app.database import Base, SessionLocal

router = APIRouter(prefix="/api/live/sessions", tags=["LIVE Sessions"], dependencies=[Depends(current_owner)])


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class LiveSession(Base):
    __tablename__ = "live_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    # Windows Agent that plays this session; set when it starts.
    agent_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
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
    # The Windows Agent (PC) this LIVE runs on: fixed when the session is created; every later action uses it.
    agent_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    # A DRAFT may start empty; the lineup is built afterwards and checked when the session starts.
    product_ids: list[int] = Field(default_factory=list, max_length=300)

class SessionProducts(BaseModel):
    product_ids: list[int] = Field(default_factory=list, max_length=300)

class SessionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    # Moving a LIVE to another PC is only allowed while it is a DRAFT, and only explicitly.
    agent_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")

class StartInput(BaseModel):
    product_id: int | None = None

class SwitchInput(BaseModel):
    product_id: int
    mode: str = Field(default="immediate", pattern="^(immediate|after_current)$")

def ensure_products(db, ids, owner):
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Danh sách chứa sản phẩm trùng nhau")
    if owned_ids(db, Product, ids, owner) != set(ids):
        raise HTTPException(422, "Có sản phẩm không tồn tại")

def get_session(db, session_id, owner=None):
    """Session by id; with `owner`, only that owner's (another owner's session is a 404).

    Internal callers driven by playback (events, Stop) pass no owner.
    """
    item = db.get(LiveSession, session_id)
    if item is None or (owner is not None and item.owner_id != owner["id"]):
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
    return {"id": item.id, "name": item.name, "status": item.status, "agent_id": item.agent_id,
            "product_ids": ids_for(db, item.id), "current_product_id": item.current_product_id,
            "created_at": item.created_at, "started_at": item.started_at, "ended_at": item.ended_at}

def event(db, sid, kind, pid=None, detail=""):
    db.add(SessionEvent(session_id=sid, event_type=kind, product_id=pid, detail=detail))

def record_event(session_id: int, kind: str, product_id: int | None = None, detail: str = ""):
    with SessionLocal() as db:
        event(db, session_id, kind, product_id, detail)
        db.commit()

def record_product_switch(session_id: int, product_id: int, detail: str = ""):
    """Record a product change the Agent has actually made (right away, or later at a video boundary)."""
    with SessionLocal() as db:
        item = db.get(LiveSession, session_id)
        if item is not None and item.status == "RUNNING":
            item.current_product_id = product_id
            event(db, session_id, "PRODUCT_SWITCHED", product_id, detail)
            db.commit()


# ----- playback on the OWNER's Windows Agent ----------------------------------------------------------

def agent_by_id(owner_id: str, agent_id: str | None):
    from app.owner_agents import agents_of
    return next((a for a in agents_of(owner_id) if a["id"] == agent_id), None) if agent_id else None


def is_running_owner(item) -> bool:
    """True when the session's Agent is online and reports that it is playing this session."""
    from app.live import playback_of
    if item.status != "RUNNING":
        return False
    agent = agent_by_id(item.owner_id, item.agent_id)
    playback = playback_of(agent)
    return bool(agent and agent["online"] and playback.get("running") and playback.get("session_id") == item.id)


def playing_session(owner_id: str, agent_id: str | None = None) -> int | None:
    """Id of the owner's RUNNING session (on `agent_id` if given) that its Agent is actually playing, if any."""
    with SessionLocal() as db:
        query = select(LiveSession).where(LiveSession.owner_id == owner_id, LiveSession.status == "RUNNING")
        if agent_id is not None:
            query = query.where(LiveSession.agent_id == agent_id)
        running = db.scalars(query.order_by(LiveSession.started_at.desc())).all()
    return next((item.id for item in running if is_running_owner(item)), None)


def session_agent(owner: dict, item, free: bool = False):
    """The session's own Agent — never another one — checked to belong to the owner, be enabled, licensed and online.
    With `free`, also not busy playing something else."""
    from app.live import playback_of
    from app.owner_agents import owned_agent
    if not item.agent_id:
        raise HTTPException(409, "Phiên chưa gắn Windows Agent")
    agent = owned_agent(owner, item.agent_id, online=True)
    playback = playback_of(agent)
    if free and playback.get("running") and playback.get("session_id") != item.id:
        raise HTTPException(409, f"Agent “{agent['hostname']}” đang phát nội dung khác; hãy dừng trước")
    return agent


def play_product(db, owner, agent, session_id: int, product_id: int, mode: str = "immediate"):
    """Send the product's videos on that Agent; the Agent starts the session or switches within it."""
    from app.cloud_agents import rpc
    from app.media import product_media
    media_ids = product_media(db, owner["id"], agent["id"], product_id)
    if not media_ids:
        raise HTTPException(409, f"Sản phẩm chưa có video trên máy “{agent['hostname']}”")
    return rpc(agent["id"], owner["id"], "product_play",
               {"session_id": session_id, "product_id": product_id, "media_ids": media_ids, "mode": mode}, timeout=15)


def sync_agent_events(owner_id: str):
    """Apply what the owner's Agents did on their own (pending switch applied, playback ended, errors) to sessions."""
    from app.cloud_agents import pop_events
    from app.owner_agents import agents_of
    for agent in agents_of(owner_id):
        for ev in pop_events(agent["id"]):
            with SessionLocal() as db:
                item = db.get(LiveSession, ev.get("session_id") or 0)
                if item is None or item.owner_id != owner_id or item.agent_id != agent["id"]:
                    continue
                detail = ";".join(f"{k}={v}" for k, v in (ev.get("detail") or {}).items() if v is not None)
                if ev["type"] == "PRODUCT_SWITCHED" and ev.get("product_id") is not None and item.status == "RUNNING":
                    item.current_product_id = ev["product_id"]
                    event(db, item.id, "PRODUCT_SWITCHED", ev["product_id"], detail)
                elif ev["type"] in ("PLAYBACK_ENDED", "ERROR"):
                    event(db, item.id, ev["type"], ev.get("product_id"), detail[:500])
                db.commit()


@router.get("")
def list_sessions(owner: Owner):
    sync_agent_events(owner["id"])
    with SessionLocal() as db:
        rows = db.scalars(select(LiveSession).where(LiveSession.owner_id == owner["id"]).order_by(LiveSession.id.desc())).all()
        return [serialize(db, x) for x in rows]

@router.post("", status_code=201)
def create_session(body: SessionInput, owner: Owner):
    from app.owner_agents import owned_agent
    agent = owned_agent(owner, body.agent_id)
    with SessionLocal() as db:
        ensure_products(db, body.product_ids, owner)
        item = LiveSession(name=body.name.strip(), status="DRAFT", owner_id=owner["id"], agent_id=agent["id"])
        db.add(item)
        db.flush()
        replace_products(db, item.id, body.product_ids)
        event(db, item.id, "CREATED")
        db.commit()
        return serialize(db, item)

@router.get("/{session_id}")
def session_detail(session_id: int, owner: Owner):
    sync_agent_events(owner["id"])
    with SessionLocal() as db:
        return serialize(db, get_session(db, session_id, owner))

@router.put("/{session_id}/products")
def update_products(session_id: int, body: SessionProducts, owner: Owner):
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status == "ENDED":
            raise HTTPException(409, "Không thể chỉnh sửa phiên đã kết thúc")
        ensure_products(db, body.product_ids, owner)
        if item.status == "RUNNING":
            if not body.product_ids:
                raise HTTPException(422, "Phiên đang LIVE phải còn ít nhất một sản phẩm")
            if item.current_product_id is not None and item.current_product_id not in body.product_ids:
                raise HTTPException(409, "Không thể gỡ sản phẩm đang phát")
        replace_products(db, session_id, body.product_ids)
        event(db, session_id, "QUEUE_UPDATED")
        db.commit()
        return serialize(db, item)

@router.post("/{session_id}/start")
def start_session(session_id: int, body: StartInput, owner: Owner):
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status != "DRAFT":
            raise HTTPException(409, "Chỉ bắt đầu được phiên DRAFT")
        ids = ids_for(db, session_id)
        if not ids:
            raise HTTPException(409, "Phiên chưa có sản phẩm nào")
        pid = body.product_id or ids[0]
        if pid not in ids:
            raise HTTPException(422, "Sản phẩm không nằm trong phiên")
        agent = session_agent(owner, item, free=True)
        # The Agent validates the files and starts OBS before the session is marked RUNNING.
        result = play_product(db, owner, agent, session_id, pid)
        item.status = "RUNNING"
        item.current_product_id = pid
        item.started_at = now()
        event(db, session_id, "STARTED", pid, f"agent={agent['hostname']}")
        db.commit()
        return {"session": serialize(db, item), "playback": result}

@router.post("/{session_id}/resume")
def resume_session(session_id: int, body: StartInput, owner: Owner):
    """Play a RUNNING session again after its playback was lost (Agent restart, PC sleep, OBS crash…).

    Plays `product_id` (added to the session if missing) or the product that was live, on the session's own
    Agent. A session is never moved to another PC implicitly.
    """
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status != "RUNNING":
            raise HTTPException(409, "Chỉ tiếp tục được phiên đang LIVE bị ngắt")
        if is_running_owner(item):
            raise HTTPException(409, "Phiên đang phát bình thường")
        ids = ids_for(db, session_id)
        pid = body.product_id or item.current_product_id or (ids[0] if ids else None)
        if pid is None:
            raise HTTPException(409, "Phiên chưa có sản phẩm nào")
        ensure_products(db, [pid], owner)
        agent = session_agent(owner, item, free=True)
        result = play_product(db, owner, agent, session_id, pid)
        if pid not in ids:
            db.add(SessionProduct(session_id=session_id, product_id=pid, sort_order=len(ids)))
        item.current_product_id = pid
        event(db, session_id, "RESUMED", pid, f"agent={agent['hostname']}")
        db.commit()
        return {"session": serialize(db, item), "playback": result}

@router.post("/{session_id}/switch")
def switch_session_product(session_id: int, body: SwitchInput, owner: Owner):
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status != "RUNNING":
            raise HTTPException(409, "Phiên chưa bắt đầu hoặc đã kết thúc")
        agent = session_agent(owner, item)
        # The lineup is a prepared pool, not a fixed order: any of the owner's products with videos can go live.
        ensure_products(db, [body.product_id], owner)
        if body.product_id == item.current_product_id and body.mode == "immediate":
            return {"ok": True, "unchanged": True}
        result = play_product(db, owner, agent, session_id, body.product_id, body.mode)
        ids = ids_for(db, session_id)
        if body.product_id not in ids:
            db.add(SessionProduct(session_id=session_id, product_id=body.product_id, sort_order=len(ids)))
            event(db, session_id, "PRODUCT_ADDED", body.product_id, "during_live=true")
        if body.mode == "after_current" and body.product_id != item.current_product_id:
            event(db, session_id, "SWITCH_QUEUED", body.product_id)
            db.commit()
            return {"ok": True, "pending_product_id": body.product_id}
        db.commit()
    # Immediate switches are recorded here; the Agent reports deferred ones as events.
    record_product_switch(session_id, body.product_id)
    with SessionLocal() as db:
        return {"ok": True, "playback": result, "session": serialize(db, get_session(db, session_id, owner))}

@router.patch("/{session_id}")
def update_session(session_id: int, body: SessionPatch, owner: Owner):
    """Rename, or move a DRAFT to another of the owner's Agents (its video setup is per Agent)."""
    from app.owner_agents import owned_agent
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status == "ENDED":
            raise HTTPException(409, "Không thể sửa phiên đã kết thúc")
        if body.agent_id is not None and body.agent_id != item.agent_id:
            if item.status != "DRAFT":
                raise HTTPException(409, "Không thể đổi Windows Agent của phiên đang LIVE")
            item.agent_id = owned_agent(owner, body.agent_id)["id"]
            event(db, session_id, "AGENT_CHANGED", detail=f"agent={item.agent_id}")
        if body.name is not None:
            item.name = body.name.strip()
        db.commit()
        return serialize(db, item)

@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: int, owner: Owner):
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status == "RUNNING":
            raise HTTPException(409, "Hãy kết thúc phiên trước khi xóa")
        # SQLite does not enforce ON DELETE CASCADE unless foreign keys are enabled; delete children explicitly.
        db.execute(delete(SessionEvent).where(SessionEvent.session_id == session_id))
        db.execute(delete(SessionProduct).where(SessionProduct.session_id == session_id))
        db.delete(item)
        db.commit()

@router.post("/{session_id}/duplicate", status_code=201)
def duplicate_session(session_id: int, owner: Owner):
    """New DRAFT with the same lineup, e.g. to rerun an ended LIVE."""
    with SessionLocal() as db:
        source = get_session(db, session_id, owner)
        existing = set(db.scalars(select(Product.id).where(Product.owner_id == owner["id"])).all())
        ids = [pid for pid in ids_for(db, session_id) if pid in existing]
        item = LiveSession(name=f"{source.name} (bản sao)"[:200], status="DRAFT", owner_id=owner["id"], agent_id=source.agent_id)
        db.add(item)
        db.flush()
        replace_products(db, item.id, ids)
        event(db, item.id, "CREATED", detail=f"duplicate_of={session_id}")
        db.commit()
        return serialize(db, item)

@router.post("/{session_id}/finish")
def finish_session(session_id: int, owner: Owner):
    from app.cloud_agents import rpc
    from app.live import playback_of
    sync_agent_events(owner["id"])
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status != "RUNNING":
            raise HTTPException(409, "Phiên chưa bắt đầu hoặc đã kết thúc")
        if not is_running_owner(item):
            # Its Agent no longer plays it (Agent restarted, offline…): nothing to stop, just close the record.
            item.status = "ENDED"
            item.ended_at = now()
            event(db, session_id, "ENDED", item.current_product_id, "stale=true")
            db.commit()
            return serialize(db, item)
        agent = session_agent(owner, item)
        counters = playback_of(agent)
        rpc(agent["id"], owner["id"], "playback_stop")
        item.status = "ENDED"
        item.ended_at = now()
        event(db, session_id, "ENDED", item.current_product_id,
              f"transitions={counters.get('transition_count', 0)};errors={counters.get('error_count', 0)};"
              f"recoveries={counters.get('recovery_count', 0)}")
        db.commit()
        return serialize(db, item)

@router.get("/{session_id}/events")
def list_events(session_id: int, owner: Owner):
    sync_agent_events(owner["id"])
    with SessionLocal() as db:
        get_session(db, session_id, owner)
        rows = db.scalars(select(SessionEvent).where(
            SessionEvent.session_id == session_id).order_by(SessionEvent.id)).all()
        return [{"id": row.id, "event_type": row.event_type, "product_id": row.product_id,
                 "detail": row.detail, "created_at": row.created_at} for row in rows]
