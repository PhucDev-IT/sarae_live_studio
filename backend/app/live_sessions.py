"""Operator-managed LIVE sessions, product queues and history (Phase 1)."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException

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
    shop_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    shop_id: int = Field(gt=0)
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
    return {"id": item.id, "name": item.name, "status": item.status, "agent_id": item.agent_id, "shop_id": item.shop_id,
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


# How long a LIVE action waits for the Agent before answering 202 (the command keeps going; poll it or the session).
LIVE_WAIT_SECONDS = 8


def in_flight(session_id: int, agent_id: str, kind: str = "product_play"):
    """A product command for this session still on its way (PENDING/RUNNING), if any."""
    from app.cloud_agents import _db, command_view
    with _db() as db:
        row = db.execute("""SELECT * FROM agent_commands WHERE agent_id=? AND session_id=? AND kind=?
            AND status IN ('PENDING','RUNNING') ORDER BY created_at DESC LIMIT 1""", (agent_id, session_id, kind)).fetchone()
    return command_view(row) if row else None


def play_product(db, owner, agent, item, product_id: int, mode: str, action: str, idempotency_key: str | None):
    """Create the product_play command for the session's Agent and wait briefly; its outcome is applied by
    apply_command_outcome (here if the Agent answers in time, otherwise when it does)."""
    from app.cloud_agents import run_command
    from app.media import product_media
    busy = in_flight(item.id, agent["id"])
    if busy is not None and not idempotency_key:
        raise HTTPException(409, "Lệnh trước của phiên này còn đang được Agent xử lý")
    media_ids = product_media(db, owner["id"], agent["id"], product_id)
    if not media_ids:
        raise HTTPException(409, f"Sản phẩm chưa có video trên máy “{agent['hostname']}”")
    return run_command(agent["id"], owner["id"], "product_play",
                       {"session_id": item.id, "product_id": product_id, "media_ids": media_ids, "mode": mode},
                       wait=LIVE_WAIT_SECONDS, idempotency_key=idempotency_key, session_id=item.id,
                       context={"module": "live_sessions", "action": action, "session_id": item.id,
                                "product_id": product_id, "mode": mode})


def apply_command_outcome(command: dict, context: dict):
    """Apply a final product_play outcome to its session. Called exactly once per final status (cloud_agents)."""
    # CANCELLED is the server's own decision (e.g. the session ended); the event that caused it says why.
    if context.get("module") != "live_sessions" or command["status"] == "CANCELLED":
        return
    sid, pid, action = context["session_id"], context.get("product_id"), context.get("action")
    ok = command["status"] == "SUCCESS"
    with SessionLocal() as db:
        item = db.get(LiveSession, sid)
        if item is None or item.agent_id != command["agent_id"]:
            return
        if not ok:
            reason = command["error"] or command["status"]
            event(db, sid, f"{(action or 'command').upper()}_{command['status']}", pid, f"command={command['command_id']};{reason}"[:500])
            db.commit()
            return
        late = ";late_ack=true" if command["late_ack"] else ""
        if action == "start" and item.status in ("DRAFT", "RUNNING"):
            if item.status == "DRAFT":
                item.started_at = now()
            item.status = "RUNNING"
            item.current_product_id = pid
            event(db, sid, "STARTED", pid, f"agent={item.agent_id}{late}")
        elif action == "resume" and item.status == "RUNNING":
            ids = ids_for(db, sid)
            if pid not in ids:
                db.add(SessionProduct(session_id=sid, product_id=pid, sort_order=len(ids)))
            item.current_product_id = pid
            event(db, sid, "RESUMED", pid, f"agent={item.agent_id}{late}")
        elif action == "switch" and item.status == "RUNNING":
            if context.get("mode") == "after_current" and pid != item.current_product_id:
                event(db, sid, "SWITCH_QUEUED", pid, late.lstrip(";"))
            else:
                item.current_product_id = pid
                event(db, sid, "PRODUCT_SWITCHED", pid, f"trigger=immediate{late}")
        db.commit()


RECONCILE_STOP_SECONDS = 60


def stop_sent_recently(agent_id: str, session_id: int) -> bool:
    """A stop for this session was already sent within the last minute (heartbeats come every few seconds)."""
    from app.cloud_agents import _db
    since = (datetime.now(timezone.utc) - timedelta(seconds=RECONCILE_STOP_SECONDS)).isoformat()
    with _db() as db:
        return db.execute("""SELECT 1 FROM agent_commands WHERE agent_id=? AND session_id=? AND kind='playback_stop'
            AND status!='CANCELLED' AND created_at>? LIMIT 1""", (agent_id, session_id, since)).fetchone() is not None


def reconcile_playback(owner_id: str, agent_id: str, playback: dict):
    """Compare what an Agent reports playing (heartbeat) with the Cloud's sessions; called on every heartbeat.

    - It plays a session the Cloud has ended (or one that is not this owner's / not on this Agent): stop it.
    - It plays a RUNNING session's other product than recorded and no product command is on its way: record the
      product the Agent actually plays.
    A RUNNING session the Agent no longer plays is shown as interrupted (is_running_owner) and resumed by the operator.
    """
    from app.cloud_agents import create_command
    sid = playback.get("session_id") if playback.get("running") else None
    if not sid:
        return
    with SessionLocal() as db:
        item = db.get(LiveSession, sid)
        foreign = item is None or item.owner_id != owner_id or item.agent_id != agent_id
        if foreign or item.status == "ENDED":
            if stop_sent_recently(agent_id, sid):
                return
            try:
                create_command(agent_id, owner_id, "playback_stop", session_id=sid, require_online=False)
            except HTTPException:
                return
            if not foreign:
                event(db, sid, "RECONCILE_STOP", playback.get("product_id"), "agent_still_playing_ended_session")
                db.commit()
            return
        reported = playback.get("product_id")
        if item.status == "RUNNING" and reported and reported != item.current_product_id and in_flight(sid, agent_id) is None:
            item.current_product_id = reported
            event(db, sid, "RECONCILED", reported, "product_from_agent_heartbeat")
            db.commit()


def sync_agent_events(owner_id: str):
    """Apply what the owner's Agents did on their own (pending switch applied, playback ended, errors) and any
    command outcomes not applied yet."""
    from app.cloud_agents import apply_outcomes, pop_events
    from app.owner_agents import agents_of
    for agent in agents_of(owner_id):
        apply_outcomes(agent["id"])
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


def answer(view: dict, owner: dict, session_id: int):
    """HTTP answer of a session action: the session (after any outcome applied) and its command."""
    from app.cloud_agents import respond
    with SessionLocal() as db:
        return respond(view, session=serialize(db, get_session(db, session_id, owner)))


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
        from sqlalchemy import text
        ok=db.execute(text("SELECT 1 FROM tiktok_shops WHERE id=:s AND owner_id=:o"),
                      {"s":body.shop_id,"o":owner["id"]}).scalar()
        if not ok:raise HTTPException(422,"TikTok Shop không thuộc OWNER")
        ensure_products(db, body.product_ids, owner)
        for pid in body.product_ids:
            if db.get(Product,pid).shop_id!=body.shop_id:
                raise HTTPException(422,"Sản phẩm không thuộc TikTok Shop của phiên")
        item = LiveSession(name=body.name.strip(), status="DRAFT", owner_id=owner["id"], agent_id=agent["id"], shop_id=body.shop_id)
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

@router.get("/{session_id}/readiness")
def session_readiness(session_id:int,owner:Owner):
    """Readiness and TikTok Console comparison, without modifying catalog/session."""
    from app.session_product_readiness import reconcile
    with SessionLocal() as db:
        item=get_session(db,session_id,owner)
        return {"session_id":session_id,"agent_id":item.agent_id,
                **reconcile(db,owner["id"],item.agent_id,ids_for(db,session_id),item.shop_id)}

@router.put("/{session_id}/products")
def update_products(session_id: int, body: SessionProducts, owner: Owner):
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status == "ENDED":
            raise HTTPException(409, "Không thể chỉnh sửa phiên đã kết thúc")
        ensure_products(db, body.product_ids, owner)
        for pid in body.product_ids:
            if db.get(Product,pid).shop_id!=item.shop_id:
                raise HTTPException(422,"Sản phẩm không thuộc TikTok Shop của phiên")
        from app.session_product_readiness import assess
        previous=set(ids_for(db,session_id))
        added=[pid for pid in body.product_ids if pid not in previous]
        if added:
            check=assess(db,owner["id"],item.agent_id,added)
            if not check["ready"]:
                raise HTTPException(409,"Sản phẩm mới phải đang bán và có video khả dụng trên Agent")
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
def start_session(session_id: int, body: StartInput, owner: Owner,
                  idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    """Start on the session's Agent. 200 when the Agent confirmed in time, 202 while the command is on its way
    (the session turns RUNNING when it succeeds). Retrying with the same Idempotency-Key never starts twice."""
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status != "DRAFT" and not idempotency_key:
            raise HTTPException(409, "Chỉ bắt đầu được phiên DRAFT")
        ids = ids_for(db, session_id)
        if item.shop_id is None:
            raise HTTPException(409,"Phiên cũ chưa được gắn TikTok Shop; hãy tạo phiên mới")
        if not ids:
            raise HTTPException(409, "Phiên chưa có sản phẩm nào")
        pid = body.product_id or ids[0]
        if pid not in ids:
            raise HTTPException(422, "Sản phẩm không nằm trong phiên")
        agent = session_agent(owner, item, free=True)
        from app.session_product_readiness import assess
        check=assess(db,owner["id"],agent["id"],ids)
        if not check["ready"]:
            problems=[str(p["product_id"])+": "+",".join(p["reasons"]) for p in check["products"] if not p["ready"]]
            raise HTTPException(409,"Sản phẩm chưa sẵn sàng LIVE: "+"; ".join(problems[:12]))
        view = play_product(db, owner, agent, item, pid, "immediate", "start", idempotency_key)
    return answer(view, owner, session_id)

@router.post("/{session_id}/resume")
def resume_session(session_id: int, body: StartInput, owner: Owner,
                   idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    """Play a RUNNING session again after its playback was lost (Agent restart, PC sleep, OBS crash…).

    Plays `product_id` (added to the session if missing) or the product that was live, on the session's own
    Agent. A session is never moved to another PC implicitly.
    """
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status != "RUNNING":
            raise HTTPException(409, "Chỉ tiếp tục được phiên đang LIVE bị ngắt")
        if is_running_owner(item) and not idempotency_key:
            raise HTTPException(409, "Phiên đang phát bình thường")
        ids = ids_for(db, session_id)
        pid = body.product_id or item.current_product_id or (ids[0] if ids else None)
        if pid is None:
            raise HTTPException(409, "Phiên chưa có sản phẩm nào")
        ensure_products(db, [pid], owner)
        if item.shop_id is not None and db.get(Product,pid).shop_id!=item.shop_id:
            raise HTTPException(422,"Sản phẩm không thuộc TikTok Shop của phiên")
        agent = session_agent(owner, item, free=True)
        view = play_product(db, owner, agent, item, pid, "immediate", "resume", idempotency_key)
    return answer(view, owner, session_id)

@router.post("/{session_id}/switch")
def switch_session_product(session_id: int, body: SwitchInput, owner: Owner,
                           idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status != "RUNNING":
            raise HTTPException(409, "Phiên chưa bắt đầu hoặc đã kết thúc")
        agent = session_agent(owner, item)
        # The lineup is a prepared pool, not a fixed order: any of the owner's products with videos can go live.
        ensure_products(db, [body.product_id], owner)
        if item.shop_id is not None and db.get(Product,body.product_id).shop_id!=item.shop_id:
            raise HTTPException(422,"Sản phẩm không thuộc TikTok Shop của phiên")
        if body.product_id == item.current_product_id and body.mode == "immediate" and not idempotency_key:
            return {"ok": True, "unchanged": True}
        ids = ids_for(db, session_id)
        if body.product_id not in ids:
            db.add(SessionProduct(session_id=session_id, product_id=body.product_id, sort_order=len(ids)))
            event(db, session_id, "PRODUCT_ADDED", body.product_id, "during_live=true")
            db.commit()
        view = play_product(db, owner, agent, item, body.product_id, body.mode, "switch", idempotency_key)
    return answer(view, owner, session_id)

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
        item = LiveSession(name=f"{source.name} (bản sao)"[:200], status="DRAFT", owner_id=owner["id"], agent_id=source.agent_id, shop_id=source.shop_id)
        db.add(item)
        db.flush()
        replace_products(db, item.id, ids)
        event(db, item.id, "CREATED", detail=f"duplicate_of={session_id}")
        db.commit()
        return serialize(db, item)

@router.post("/{session_id}/finish")
def finish_session(session_id: int, owner: Owner):
    """End the session in the Cloud right away, cancel its commands not yet delivered and ask its Agent to stop.

    If the Agent is offline or no longer plays it, nothing is sent: when it reconnects still playing an ENDED
    session, reconcile_playback stops it.
    """
    from app.cloud_agents import cancel_commands, create_command
    from app.live import playback_of
    sync_agent_events(owner["id"])
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status == "ENDED":
            return serialize(db, item)
        if item.status != "RUNNING":
            raise HTTPException(409, "Phiên chưa bắt đầu")
        playing = is_running_owner(item)
        agent = agent_by_id(owner["id"], item.agent_id)
        counters = playback_of(agent) if playing else {}
        item.status = "ENDED"
        item.ended_at = now()
        detail = ("stale=true" if not playing else
                  f"transitions={counters.get('transition_count', 0)};errors={counters.get('error_count', 0)};"
                  f"recoveries={counters.get('recovery_count', 0)}")
        event(db, session_id, "ENDED", item.current_product_id, detail)
        db.commit()
        if item.agent_id:
            cancel_commands(item.agent_id, session_id=session_id)
        stop = None
        if playing:
            stop = create_command(item.agent_id, owner["id"], "playback_stop", session_id=session_id,
                                  idempotency_key=f"finish:{session_id}")
        return {**serialize(db, item), "command": stop}

@router.get("/{session_id}/events")
def list_events(session_id: int, owner: Owner):
    sync_agent_events(owner["id"])
    with SessionLocal() as db:
        get_session(db, session_id, owner)
        rows = db.scalars(select(SessionEvent).where(
            SessionEvent.session_id == session_id).order_by(SessionEvent.id)).all()
        return [{"id": row.id, "event_type": row.event_type, "product_id": row.product_id,
                 "detail": row.detail, "created_at": row.created_at} for row in rows]
