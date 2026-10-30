"""Operator-managed LIVE sessions, product queues and history (Phase 1)."""
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from app.identity import current_owner
from app.ownership import Owner, owned_ids
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from app.catalog import Product
from app.database import Base, SessionLocal
from app.live_timeline import (ADVANCED_LEGACY, MAX_VIDEOS, MEDIA_ID_PATTERN, PLAYBACK_MODE_PATTERN,
                               SINGLE_VIDEO_TIMELINE, VIDEO_ORDER_PATTERN, is_single, main_video, require_legacy,
                               session_videos, set_videos)

router = APIRouter(prefix="/api/live/sessions", tags=["LIVE Sessions"], dependencies=[Depends(current_owner)])


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class LiveSession(Base):
    __tablename__ = "live_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    # Windows Agent that plays this session; set when it starts.
    agent_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    shop_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # legacy only
    tiktok_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    current_product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Video order and automatic product rotation (app/playback_policy.py), JSON; empty = the owner's default.
    playback_policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SINGLE_VIDEO_TIMELINE (the session's videos, pins by timeline) or ADVANCED_LEGACY (product queue);
    # app/live_timeline.py. `video_ids` is the JSON video list (empty: [main_media_id]); main_media_id its first video.
    playback_mode: Mapped[str] = mapped_column(String(30), default=ADVANCED_LEGACY, server_default=ADVANCED_LEGACY)
    main_media_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    video_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_order: Mapped[str] = mapped_column(String(20), default="sequential", server_default="sequential")
    video_loop: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    active_timeline_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    agent_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    tiktok_account_id: int = Field(gt=0)
    shop_id: int | None = Field(default=None, gt=0)
    # A DRAFT may start empty; the lineup is built afterwards and checked when the session starts.
    product_ids: list[int] = Field(default_factory=list, max_length=300)
    # Omitted by older clients: they keep the product-queue behaviour. The web console sends SINGLE_VIDEO_TIMELINE.
    playback_mode: str = Field(default=ADVANCED_LEGACY, pattern=PLAYBACK_MODE_PATTERN)
    # Single Video Timeline: the videos in playback order (older clients send main_media_id: a one-video list).
    media_ids: list[Annotated[str, Field(pattern=MEDIA_ID_PATTERN)]] | None = Field(default=None, max_length=MAX_VIDEOS)
    main_media_id: str | None = Field(default=None, pattern=MEDIA_ID_PATTERN)
    video_order: str = Field(default="sequential", pattern=VIDEO_ORDER_PATTERN)
    video_loop: bool = True

class SessionProducts(BaseModel):
    product_ids: list[int] = Field(default_factory=list, max_length=300)

class SessionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    shop_id: int | None = Field(default=None, gt=0)
    tiktok_account_id: int | None = Field(default=None, gt=0)
    # Moving a LIVE to another PC is only allowed while it is a DRAFT, and only explicitly.
    agent_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    # Mode, videos and their order are fixed once the session starts; loop can change while RUNNING.
    playback_mode: str | None = Field(default=None, pattern=PLAYBACK_MODE_PATTERN)
    media_ids: list[Annotated[str, Field(pattern=MEDIA_ID_PATTERN)]] | None = Field(default=None, max_length=MAX_VIDEOS)
    main_media_id: str | None = Field(default=None, pattern=MEDIA_ID_PATTERN)
    video_order: str | None = Field(default=None, pattern=VIDEO_ORDER_PATTERN)
    video_loop: bool | None = None

class StartInput(BaseModel):
    product_id: int | None = None
    agent_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    # Single Video Timeline resume: which video (default: the first) continues where (ms); the pin in force there is
    # restored.
    media_id: str | None = Field(default=None, pattern=MEDIA_ID_PATTERN)
    position_ms: int = Field(default=0, ge=0, le=86_400_000)

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
    return {"id": item.id, "name": item.name, "status": item.status, "agent_id": item.agent_id, "shop_id": item.shop_id, "tiktok_account_id": item.tiktok_account_id,
            "product_ids": ids_for(db, item.id), "current_product_id": item.current_product_id,
            "playback_mode": item.playback_mode or ADVANCED_LEGACY, "main_media_id": item.main_media_id,
            "media_ids": session_videos(item), "video_order": item.video_order or "sequential",
            "video_loop": item.video_loop is not False, "active_timeline_id": item.active_timeline_id,
            "created_at": item.created_at, "started_at": item.started_at, "ended_at": item.ended_at}

def acquire_live_claim(db, item, agent_id: str):
    """Atomically reserve one broadcaster and one Agent before dispatching playback.

    A claim is retained on ambiguous timeouts so another user cannot hijack
    a possibly live account until its state is reconciled.
    """
    from sqlalchemy import text
    db.execute(text("""
        INSERT OR IGNORE INTO live_start_claims
          (tiktok_account_id,session_id,agent_id,acquired_at)
        VALUES (:account,:session,:agent,:time)
    """),{"account":item.tiktok_account_id,"session":item.id,
          "agent":agent_id,"time":datetime.now(timezone.utc).isoformat()})
    rows=db.execute(text("""
        SELECT tiktok_account_id,session_id,agent_id FROM live_start_claims
        WHERE tiktok_account_id=:account OR agent_id=:agent OR session_id=:session
    """),{"account":item.tiktok_account_id,"agent":agent_id,
          "session":item.id}).mappings().all()
    if len(rows)!=1 or rows[0]["session_id"]!=item.id or rows[0]["agent_id"]!=agent_id:
        raise HTTPException(409,"Tài khoản TikTok hoặc Windows Agent đã được giữ cho phiên LIVE khác")


def release_live_claim(db, session_id: int):
    from sqlalchemy import text
    db.execute(text("DELETE FROM live_start_claims WHERE session_id=:session"),
               {"session":session_id})


def claims_of_agent(db, agent_id: str) -> list[int]:
    from sqlalchemy import text
    return [row[0] for row in db.execute(text("SELECT session_id FROM live_start_claims WHERE agent_id=:a"),
                                         {"a": agent_id}).all()]


def release_idle_claims(db, owner_id: str, agent_id: str):
    """The Agent reports it plays nothing (fresh heartbeat): free the claims it still holds for sessions that
    cannot be on air through Sarae any more — ENDED sessions, and DRAFT starts with no command still on its way.

    A RUNNING session keeps its claim (it is "interrupted", resumed or finished by the operator), and nothing is
    released while the Agent is offline: an unknown state never frees a TikTok account.
    """
    for sid in claims_of_agent(db, agent_id):
        item = db.get(LiveSession, sid)
        if item is None:
            release_live_claim(db, sid)
            continue
        if item.owner_id != owner_id:
            continue
        if item.status == "ENDED" or (item.status == "DRAFT" and in_flight(sid, agent_id) is None):
            release_live_claim(db, sid)
            if item.status == "DRAFT":
                item.agent_id = None
            event(db, sid, "CLAIM_RELEASED", detail=f"agent={agent_id};agent_idle=true")


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


PLAY_KINDS = ("product_play", "video_play")


def in_flight(session_id: int, agent_id: str, kinds: tuple[str, ...] = PLAY_KINDS):
    """A playback command (product or main video) for this session still on its way (PENDING/RUNNING), if any."""
    from app.cloud_agents import _db, command_view
    marks = ",".join("?" for _ in kinds)
    with _db() as db:
        row = db.execute(f"""SELECT * FROM agent_commands WHERE agent_id=? AND session_id=? AND kind IN ({marks})
            AND status IN ('PENDING','RUNNING') ORDER BY created_at DESC LIMIT 1""", (agent_id, session_id, *kinds)).fetchone()
    return command_view(row) if row else None


def command_for_key(session_id: int, idempotency_key: str | None):
    """The command an earlier request of this session made with the same Idempotency-Key (a retried click)."""
    from app.cloud_agents import _db, command_view
    if not idempotency_key:
        return None
    with _db() as db:
        row = db.execute("""SELECT * FROM agent_commands WHERE session_id=? AND idempotency_key=?
            ORDER BY created_at DESC LIMIT 1""", (session_id, idempotency_key)).fetchone()
    return command_view(row) if row else None


def play_product(db, owner, agent, item, product_id: int, mode: str, action: str, idempotency_key: str | None):
    """Play a product on its Agent with the product's Media Library videos (AVAILABLE, in order).

    The Agent plays them from its local media cache; videos missing from the cache make the Agent fail the command.
    """
    from app.cloud_agents import run_command
    from app.media_library import product_video_ids
    busy = in_flight(item.id, agent["id"])
    if busy is not None and not idempotency_key:
        raise HTTPException(409, "Lệnh trước của phiên này còn đang được Agent xử lý")
    media_ids = product_video_ids(db, owner["id"], product_id)
    if not media_ids:
        raise HTTPException(409, f"Sản phẩm #{product_id} chưa có video trong Thư viện video")
    payload = {"session_id": item.id, "product_id": product_id, "media_ids": media_ids, "mode": mode}
    context = {"module": "live_sessions", "action": action, "session_id": item.id,
               "product_id": product_id, "mode": mode}
    return run_command(agent["id"], owner["id"], "product_play", payload,
                       wait=LIVE_WAIT_SECONDS, idempotency_key=idempotency_key, session_id=item.id,
                       context=context)


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
        if action == "finish":
            if ok and item.status == "ENDED":
                release_live_claim(db,sid)
                event(db,sid,"AGENT_STOP_CONFIRMED",detail=f"command={command['command_id']}")
            elif not ok:
                event(db,sid,"AGENT_STOP_UNCONFIRMED",detail=command["error"] or command["status"])
            db.commit()
            return
        if not ok:
            if action == "start" and command["status"] == "FAILED" and item.status == "DRAFT":
                release_live_claim(db,sid)
                item.agent_id=None
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
        elif action == "resume" and item.status == "RUNNING" and pid is None:
            event(db, sid, "RESUMED", None, f"agent={item.agent_id}{late}")
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
        follow = action in ("start", "resume") and item.status == "RUNNING"
        owner_id, agent_id, account_id = item.owner_id, item.agent_id, item.tiktok_account_id
        db.commit()
    if follow:
        # The session reads its own TikTok chat from now on (AI replies need the comments), and its Agent gets the
        # video order / product rotation policy with the lineup.
        from app.tiktok_chat import follow_session
        from app.playback_policy import push
        follow_session(owner_id, agent_id, account_id)
        push(owner_id, sid)


RECONCILE_STOP_SECONDS = 60


def stop_sent_recently(agent_id: str, session_id: int, kind: str = "playback_stop") -> bool:
    """A stop (or `kind`) for this session was already sent within the last minute (heartbeats come every few
    seconds)."""
    from app.cloud_agents import _db
    since = (datetime.now(timezone.utc) - timedelta(seconds=RECONCILE_STOP_SECONDS)).isoformat()
    with _db() as db:
        return db.execute("""SELECT 1 FROM agent_commands WHERE agent_id=? AND session_id=? AND kind=?
            AND status!='CANCELLED' AND created_at>? LIMIT 1""", (agent_id, session_id, kind, since)).fetchone() is not None


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
        # Only an explicit "not running" frees claims; a heartbeat without playback data says nothing.
        if playback.get("running") is False:
            with SessionLocal() as db:
                release_idle_claims(db, owner_id, agent_id)
                db.commit()
        return
    with SessionLocal() as db:
        item = db.get(LiveSession, sid)
        foreign = item is None or item.owner_id != owner_id or item.agent_id != agent_id
        if not foreign and item.status == "DRAFT" and sid in claims_of_agent(db, agent_id):
            # The start reached the Agent although its answer was lost or late (TIMEOUT): the session is on air.
            item.status = "RUNNING"
            item.started_at = item.started_at or now()
            item.current_product_id = playback.get("product_id") or item.current_product_id
            event(db, sid, "STARTED", item.current_product_id, f"agent={agent_id};adopted_from_heartbeat=true")
            db.commit()
            return
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
        if item.status == "RUNNING" and is_single(item):
            reconcile_timeline(db, owner_id, agent_id, item, playback.get("timeline") or {})
            return
        reported = playback.get("product_id")
        if item.status == "RUNNING" and reported and reported != item.current_product_id and in_flight(sid, agent_id) is None:
            item.current_product_id = reported
            event(db, sid, "RECONCILED", reported, "product_from_agent_heartbeat")
            db.commit()


# What the Agent's pin scheduler did (sarae_agent/timeline/scheduler.py), kept in the session history.
TIMELINE_EVENTS = ("TIMELINE_LOADED", "TIMELINE_STOPPED", "TIMELINE_RESUMED", "PIN_APPLIED", "PIN_FAILED",
                   "PIN_DRIFT", "MANUAL_OVERRIDE", "LIVE_GATE_CHANGED")


def reconcile_timeline(db, owner_id: str, agent_id: str, item, reported: dict) -> None:
    """The Agent plays this Single Video session but runs another timeline version (or none, e.g. it restarted and
    the session was resumed before an activation reached it): send the active one again, at most once a minute."""
    from app.live_timeline import LiveTimeline, push_timeline
    # The product "on air" of a Single Video session is the one its timeline pins at the current position: AI replies
    # (ai_live_batches.context) and the console read current_product_id. Between pins it keeps the last one.
    on_air = reported.get("desired_product_id") if reported.get("session_id") == item.id else None
    if isinstance(on_air, int) and on_air != item.current_product_id:
        item.current_product_id = on_air
        db.commit()
    active = db.get(LiveTimeline, item.active_timeline_id) if item.active_timeline_id else None
    wanted = active.version if active is not None and active.status == "ACTIVE" else None
    if reported.get("session_id") == item.id and reported.get("version") == wanted:
        return
    if stop_sent_recently(agent_id, item.id, "timeline_set"):
        return
    sent, _ = push_timeline(owner_id, item.id)
    if sent:
        event(db, item.id, "TIMELINE_RECONCILED", detail=f"agent_version={reported.get('version')};version={wanted}")
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
                elif ev["type"] in ("PLAYBACK_ENDED", "ERROR") + TIMELINE_EVENTS:
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
    from sqlalchemy import text
    with SessionLocal() as db:
        ok=db.execute(text("SELECT 1 FROM user_tiktok_accounts WHERE owner_id=:o AND account_id=:a"),
                      {"o":owner["id"],"a":body.tiktok_account_id}).scalar()
        if not ok: raise HTTPException(422,"TikTok Account chưa liên kết với User")
        if body.playback_mode == SINGLE_VIDEO_TIMELINE and body.product_ids:
            raise HTTPException(422, "Phiên Single Video lấy sản phẩm từ timeline, không nhận product_ids")
        media_ids = body.media_ids if body.media_ids is not None else ([body.main_media_id] if body.main_media_id else [])
        if media_ids and body.playback_mode != SINGLE_VIDEO_TIMELINE:
            raise HTTPException(422, "Danh sách video chỉ dùng cho chế độ Single Video")
        ensure_products(db,body.product_ids,owner)
        item=LiveSession(name=body.name.strip(),status="DRAFT",owner_id=owner["id"],
                         agent_id=None,tiktok_account_id=body.tiktok_account_id,shop_id=None,
                         playback_mode=body.playback_mode,video_order=body.video_order,
                         video_loop=body.video_loop)
        if media_ids:
            set_videos(db, owner["id"], item, media_ids)
        db.add(item)
        db.flush()
        replace_products(db, item.id, body.product_ids)
        event(db, item.id, "CREATED", detail=f"mode={item.playback_mode}")
        db.commit()
        return serialize(db, item)

@router.get("/{session_id}")
def session_detail(session_id: int, owner: Owner):
    sync_agent_events(owner["id"])
    with SessionLocal() as db:
        return serialize(db, get_session(db, session_id, owner))

@router.get("/{session_id}/readiness")
def session_readiness(session_id:int,owner:Owner,agent_id:str|None=None):
    """Readiness and TikTok Console comparison, without modifying catalog/session.

    Videos and the Console snapshot belong to one Agent: a DRAFT has none yet, so the page passes the Agent the
    user is about to start on (`agent_id`); a started session always uses its own Agent.
    """
    from sqlalchemy import text
    from app.owner_agents import owned_agent
    from app.session_product_readiness import reconcile
    with SessionLocal() as db:
        item=get_session(db,session_id,owner)
        target=item.agent_id or agent_id
        if item.agent_id and agent_id and agent_id!=item.agent_id:
            raise HTTPException(409,"Phiên đã gắn Agent khác")
        if target:
            target=owned_agent(owner,target)["id"]
        expected=db.execute(text("SELECT user_id FROM tiktok_accounts WHERE id=:a"),
                            {"a":item.tiktok_account_id}).scalar_one_or_none() if item.tiktok_account_id else None
        return {"session_id":session_id,"agent_id":target,
                **reconcile(db,owner["id"],target,ids_for(db,session_id),item.shop_id,expected_user_id=expected)}

@router.put("/{session_id}/products")
def update_products(session_id: int, body: SessionProducts, owner: Owner):
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status == "ENDED":
            raise HTTPException(409, "Không thể chỉnh sửa phiên đã kết thúc")
        require_legacy(item, "Sửa danh sách sản phẩm")
        ensure_products(db, body.product_ids, owner)
        from app.session_product_readiness import assess
        previous=set(ids_for(db,session_id))
        added=[pid for pid in body.product_ids if pid not in previous]
        if added and item.status == "RUNNING":
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
        running = item.status == "RUNNING"
    if running:
        from app.playback_policy import push
        push(owner["id"], session_id)  # the Agent rotates over the new lineup
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        return serialize(db, item)

class PrepareInput(BaseModel):
    agent_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")


@router.post("/{session_id}/prepare")
def prepare_session(session_id: int, body: PrepareInput, owner: Owner):
    """User explicitly requests a fresh Console sync before reviewing the lineup.

    Does not claim the Agent or start playback. User confirms the synced products
    separately before calling /start.
    """
    from app.owner_agents import owned_agent
    from app.live import playback_of
    from app.cloud_agents import run_command,respond
    with SessionLocal() as db:
        item=get_session(db,session_id,owner)
        if item.status != "DRAFT":
            raise HTTPException(409,"Chỉ chuẩn bị được phiên DRAFT")
        if item.tiktok_account_id is None:
            raise HTTPException(409,"Chưa gắn TikTok Account")
        agent=owned_agent(owner,body.agent_id,online=True)
        if playback_of(agent).get("running"):
            raise HTTPException(409,"Agent đang vận hành phiên khác")
        from sqlalchemy import text
        expected=db.execute(text("SELECT user_id FROM tiktok_accounts WHERE id=:id"),
                            {"id":item.tiktok_account_id}).scalar_one_or_none()
        if not expected:
            raise HTTPException(409,"Chưa có user_id TikTok Account được xác minh")
    result=run_command(body.agent_id,owner["id"],"tiktok_products_sync",
                       {"expected_user_id":expected},wait=8,
                       context={"module":"tiktok_prepare","session_id":session_id})
    return respond(result,session_id=session_id,agent_id=body.agent_id)


def start_agent_and_console(db, owner: dict, item, body):
    """The Agent a DRAFT starts on (online, idle) and its LIVE Console sync, which must be of the session's TikTok
    Account and at most 10 minutes old. Returns (agent, snapshot row)."""
    from sqlalchemy import text
    from app.owner_agents import owned_agent
    from app.live import playback_of
    chosen_agent=body.agent_id or item.agent_id
    if not chosen_agent:
        raise HTTPException(422,"Chọn Windows Agent trước khi bắt đầu")
    agent=owned_agent(owner,chosen_agent,online=True)
    if playback_of(agent).get("running"):
        raise HTTPException(409,"Agent đang phát phiên khác")
    expected=db.execute(text("SELECT user_id FROM tiktok_accounts WHERE id=:a"),
                        {"a":item.tiktok_account_id}).scalar_one_or_none()
    from app.tiktok_products import connection as snapshot_connection
    with snapshot_connection() as snap_db:
        snap=snap_db.execute("SELECT account_user_id,scanned_at,products_json FROM snapshots WHERE agent_id=? AND owner_id=?",
                             (agent["id"],owner["id"])).fetchone()
    if not snap or not snap["account_user_id"] or snap["account_user_id"]!=expected:
        raise HTTPException(409,"Chưa đồng bộ đúng tài khoản TikTok trên LIVE Console")
    # Manual sync is mandatory immediately before starting this LIVE, not just any historical sync.
    from datetime import datetime as _dt
    try:
        checked_at=_dt.fromisoformat(snap["scanned_at"].replace("Z","+00:00"))
        if checked_at.tzinfo is None:
            checked_at=checked_at.replace(tzinfo=timezone.utc)
        recent=(datetime.now(timezone.utc)-checked_at).total_seconds() <= 600
    except (ValueError,TypeError):
        recent=False
    if not recent:
        raise HTTPException(409,"Danh sách LIVE Console đã cũ; đồng bộ sản phẩm lại trước khi LIVE")
    return agent, snap


def play_video(db, owner, agent, item, action: str, idempotency_key: str | None, start_ms: int = 0,
               media_id: str | None = None):
    """Play the session's videos on its Agent with the active pin timeline (none: the videos play alone), starting
    with `media_id` (default: the first one in order; random/shuffle: any)."""
    from app.cloud_agents import run_command
    from app.live_timeline import agent_timeline, plays_one_video
    if in_flight(item.id, agent["id"]) is not None and not idempotency_key:
        raise HTTPException(409, "Lệnh trước của phiên này còn đang được Agent xử lý")
    videos = session_videos(item)
    if media_id is not None and media_id not in videos:
        raise HTTPException(422, "Video không thuộc phiên này")
    payload = {"session_id": item.id, "media_id": media_id or videos[0], "loop": item.video_loop is not False,
               "start_ms": start_ms, "timeline": agent_timeline(db, item)}
    if not plays_one_video(item):
        payload.update(media_ids=videos, video_order=item.video_order or "sequential")
        if media_id is None and start_ms == 0:
            payload.pop("media_id")  # the Agent picks the first video by the order
    context = {"module": "live_sessions", "action": action, "session_id": item.id, "product_id": None}
    return run_command(agent["id"], owner["id"], "video_play", payload, wait=LIVE_WAIT_SECONDS,
                       idempotency_key=idempotency_key, session_id=item.id, context=context)


def start_single(db, owner: dict, item, body, idempotency_key: str | None):
    """Start a Single Video Timeline session. Products missing from the Console cart do not block it: their pins
    fail on the Agent (PIN_FAILED in the history) and the video keeps playing."""
    from sqlalchemy import text
    from app.live_timeline import main_video
    if item.tiktok_account_id is None:
        raise HTTPException(409, "Phiên chưa gắn TikTok Account")
    videos = session_videos(item)
    if not videos:
        raise HTTPException(409, "Phiên chưa chọn video")
    for media_id in videos:
        main_video(db, owner["id"], media_id)
    agent, _ = start_agent_and_console(db, owner, item, body)
    conflict = db.execute(text("""SELECT id FROM live_sessions WHERE tiktok_account_id=:a
        AND status='RUNNING' AND id<>:id LIMIT 1"""), {"a": item.tiktok_account_id, "id": item.id}).scalar()
    if conflict:
        raise HTTPException(409, "TikTok Account đang có LIVE Session khác")
    require_media_ready(db, owner, agent, item.id)
    acquire_live_claim(db, item, agent["id"])
    item.agent_id = agent["id"]
    db.commit()
    return play_video(db, owner, agent, item, "start", idempotency_key)


@router.post("/{session_id}/start")
def start_session(session_id: int, body: StartInput, owner: Owner,
                  idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    """Start on the session's Agent. 200 when the Agent confirmed in time, 202 while the command is on its way
    (the session turns RUNNING when it succeeds). Retrying with the same Idempotency-Key never starts twice."""
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status != "DRAFT":
            # Only a retry of the very request that started it gets its answer again; any other start is refused.
            prior = command_for_key(session_id, idempotency_key)
            if prior is None:
                raise HTTPException(409, "Chỉ bắt đầu được phiên DRAFT")
            return answer(prior, owner, session_id)
        if is_single(item):
            view = start_single(db, owner, item, body, idempotency_key)
            return answer(view, owner, session_id)
        ids = ids_for(db, session_id)
        if item.tiktok_account_id is None:
            raise HTTPException(409,"Phiên chưa gắn TikTok Account")
        if not ids:
            raise HTTPException(409, "Phiên chưa có sản phẩm nào")
        pid = body.product_id or ids[0]
        if pid not in ids:
            raise HTTPException(422, "Sản phẩm không nằm trong phiên")
        from sqlalchemy import text
        agent, snap = start_agent_and_console(db, owner, item, body)
        import json
        source=json.loads(snap["products_json"])
        source_keys={(str(x.get("seller_id") or ""),str(x.get("product_id") or "")) for x in source}
        legacy={int(row[0]):str(row[1]) for row in db.execute(text(
            "SELECT id,seller_id FROM tiktok_shops WHERE owner_id=:owner"),
            {"owner":owner["id"]}).all()}
        for product_id in ids:
            product=db.get(Product,product_id)
            seller=product.tiktok_seller_id or legacy.get(product.shop_id)
            if not seller or (str(seller),str(product.tiktok_product_id)) not in source_keys:
                raise HTTPException(409,f"Sản phẩm #{product_id} không còn trong giỏ LIVE Console vừa đồng bộ")

        conflict=db.execute(text("""SELECT id FROM live_sessions WHERE tiktok_account_id=:a
            AND status='RUNNING' AND id<>:id LIMIT 1"""),
            {"a":item.tiktok_account_id,"id":item.id}).scalar()
        if conflict:
            raise HTTPException(409,"TikTok Account đang có LIVE Session khác")
        from app.session_product_readiness import assess
        check=assess(db,owner["id"],agent["id"],ids)
        if not check["ready"]:
            problems=[str(p["product_id"])+": "+",".join(p["reasons"]) for p in check["products"] if not p["ready"]]
            raise HTTPException(409,"Sản phẩm chưa sẵn sàng LIVE: "+"; ".join(problems[:12]))
        # Playback reads local files only: every video of the session must be cached and verified first.
        require_media_ready(db, owner, agent, item.id)
        # Persist ownership of the selected Agent before dispatch: fast Agent ack
        # can arrive while the HTTP request is still waiting.
        acquire_live_claim(db,item,agent["id"])
        item.agent_id=agent["id"]
        db.commit()
        # Do not release the claim on ambiguous transport timeouts.
        view = play_product(db, owner, agent, item, pid, "immediate", "start", idempotency_key)
    return answer(view, owner, session_id)

def require_media_ready(db, owner: dict, agent: dict, session_id: int):
    """409 MEDIA_NOT_READY unless the Agent cached every video of the session (checked before any playback)."""
    from app.agent_media import media_readiness
    status = media_readiness(db, owner["id"], agent["id"], session_id)
    if not status["ready"]:
        raise HTTPException(409, f"Agent “{agent['hostname']}” chưa tải đủ video của phiên ({status['ready_count']}/{status['total']}). "
                                 "Hãy tải video về máy trước khi phát.", headers={"X-Sarae-Error": "MEDIA_NOT_READY"})
    return status


def require_product_cached(db, owner: dict, agent: dict, session_id: int, product_id: int):
    """During LIVE: a product added later (or whose videos changed) is downloaded first, never played half-cached."""
    from app.agent_media import media_readiness
    from app.owner_agents import send_command
    status = media_readiness(db, owner["id"], agent["id"], session_id)
    row = next((p for p in status["products"] if p["product_id"] == product_id), None)
    if row is None or row["total"] == 0 or row["ready"] == row["total"]:
        return  # no video at all is reported by play_product itself
    if status["state"] != "DOWNLOADING":
        send_command(owner, agent["id"], "media_prepare", {"session_id": session_id},
                     idempotency_key=f"media-prepare-{session_id}-{datetime.now(timezone.utc):%Y%m%d%H%M}")
    raise HTTPException(409, f"Đang tải video của sản phẩm #{product_id} về máy “{agent['hostname']}” "
                             f"({row['ready']}/{row['total']}). Thử lại sau khi tải xong.", headers={"X-Sarae-Error": "MEDIA_NOT_READY"})


@router.post("/{session_id}/prepare-media")
def prepare_media(session_id: int, body: StartInput, owner: Owner,
                  idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    """Ask the Agent to download every video of the session into its cache (it reports progress by heartbeat)."""
    from app.agent_media import media_readiness, session_manifest
    from app.owner_agents import send_command
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status not in ("DRAFT", "RUNNING"):
            raise HTTPException(409, "Phiên đã kết thúc")
        agent_id = item.agent_id if item.status == "RUNNING" else body.agent_id
        if item.agent_id and body.agent_id and body.agent_id != item.agent_id:
            raise HTTPException(409, "Phiên đang chạy trên Agent khác")
        if not session_manifest(db, owner["id"], session_id)["items"]:
            raise HTTPException(409, "Sản phẩm trong phiên chưa có video nào trong Thư viện video")
        view = send_command(owner, agent_id, "media_prepare", {"session_id": session_id},
                            idempotency_key=idempotency_key or f"media-prepare-{session_id}-{datetime.now(timezone.utc):%Y%m%d%H%M}")
        status = media_readiness(db, owner["id"], view["agent"]["id"], session_id)
        if status["state"] in ("NOT_STARTED", "INCOMPLETE", "FAILED"):
            status["state"] = "QUEUED"  # until the Agent's first progress report
    return {**status, "command": {k: view.get(k) for k in ("command_id", "status")}}


@router.get("/{session_id}/media-readiness")
def get_media_readiness(session_id: int, owner: Owner, agent_id: str | None = None):
    from app.agent_media import media_readiness
    from app.owner_agents import owned_agent
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        agent = owned_agent(owner, item.agent_id if item.status == "RUNNING" else agent_id)
        status = media_readiness(db, owner["id"], agent["id"], session_id)
    return {**status, "agent_online": agent["online"], "hostname": agent["hostname"]}


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
        if is_running_owner(item):
            prior = command_for_key(session_id, idempotency_key)
            if prior is None:
                raise HTTPException(409, "Phiên đang phát bình thường")
            return answer(prior, owner, session_id)
        if is_single(item):
            if body.product_id is not None:
                raise HTTPException(409, "Phiên Single Video tiếp tục theo vị trí video, không theo sản phẩm")
            agent = session_agent(owner, item, free=True)
            view = play_video(db, owner, agent, item, "resume", idempotency_key, start_ms=body.position_ms,
                              media_id=body.media_id)
            return answer(view, owner, session_id)
        ids = ids_for(db, session_id)
        pid = body.product_id or item.current_product_id or (ids[0] if ids else None)
        if pid is None:
            raise HTTPException(409, "Phiên chưa có sản phẩm nào")
        ensure_products(db, [pid], owner)
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
        require_legacy(item, "Chuyển sản phẩm")
        agent = session_agent(owner, item)
        # The lineup is a prepared pool, not a fixed order: any of the owner's products with videos can go live.
        ensure_products(db, [body.product_id], owner)
        if body.product_id == item.current_product_id and body.mode == "immediate" and not idempotency_key:
            return {"ok": True, "unchanged": True}
        ids = ids_for(db, session_id)
        if body.product_id not in ids:
            db.add(SessionProduct(session_id=session_id, product_id=body.product_id, sort_order=len(ids)))
            event(db, session_id, "PRODUCT_ADDED", body.product_id, "during_live=true")
            db.commit()
        require_product_cached(db, owner, agent, session_id, body.product_id)
        view = play_product(db, owner, agent, item, body.product_id, body.mode, "switch", idempotency_key)
    # A product chosen by hand pauses the automatic rotation until the operator turns it back on (spec §6).
    from app.playback_policy import pause_auto
    pause_auto(owner["id"], session_id)
    return answer(view, owner, session_id)

@router.patch("/{session_id}")
def update_session(session_id: int, body: SessionPatch, owner: Owner):
    """Rename, or move a DRAFT to another of the owner's Agents (its video setup is per Agent)."""
    from app.owner_agents import owned_agent
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        if item.status == "ENDED":
            raise HTTPException(409, "Không thể sửa phiên đã kết thúc")
        if body.tiktok_account_id is not None and body.tiktok_account_id != item.tiktok_account_id:
            from sqlalchemy import text
            claim=db.execute(text("SELECT 1 FROM live_start_claims WHERE session_id=:sid"),
                             {"sid":session_id}).scalar()
            if claim: raise HTTPException(409,"Phiên đang giữ quyền điều khiển TikTok Account; chưa thể đổi")
            if item.status != "DRAFT":
                raise HTTPException(409,"Không đổi được tài khoản LIVE đang chạy")
            from sqlalchemy import text
            allowed=db.execute(text("SELECT 1 FROM user_tiktok_accounts WHERE owner_id=:o AND account_id=:a"),
                               {"o":owner["id"],"a":body.tiktok_account_id}).scalar()
            if not allowed: raise HTTPException(422,"Tài khoản TikTok không thuộc User")
            item.tiktok_account_id=body.tiktok_account_id
            event(db,session_id,"ACCOUNT_CHANGED",detail=f"account_id={item.tiktok_account_id}")
        if body.agent_id is not None:
            raise HTTPException(409,"Windows Agent chỉ được chọn khi bắt đầu phiên LIVE")
        if body.playback_mode is not None and body.playback_mode != (item.playback_mode or ADVANCED_LEGACY):
            if item.status != "DRAFT":
                raise HTTPException(409, "Chỉ đổi chế độ phát khi phiên còn là DRAFT")
            item.playback_mode = body.playback_mode
            event(db, session_id, "MODE_CHANGED", detail=f"mode={item.playback_mode}")
        media_ids = body.media_ids if body.media_ids is not None else (
            [body.main_media_id] if body.main_media_id is not None else None)
        if media_ids is not None and media_ids != session_videos(item):
            if item.status != "DRAFT":
                raise HTTPException(409, "Không đổi danh sách video khi phiên đã bắt đầu")
            if not is_single(item):
                raise HTTPException(422, "Danh sách video chỉ dùng cho chế độ Single Video")
            # Markers belong to their own video: the timeline stays valid; markers of a removed video are ignored.
            set_videos(db, owner["id"], item, media_ids)
            event(db, session_id, "VIDEOS_CHANGED", detail=f"count={len(media_ids)};first={item.main_media_id}")
        if body.video_order is not None and body.video_order != (item.video_order or "sequential"):
            if item.status != "DRAFT":
                raise HTTPException(409, "Không đổi thứ tự phát khi phiên đã bắt đầu")
            item.video_order = body.video_order
            event(db, session_id, "VIDEO_ORDER_CHANGED", detail=f"order={item.video_order}")
        if body.video_loop is not None:
            item.video_loop = body.video_loop
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
        from sqlalchemy import text
        claim=db.execute(text("SELECT 1 FROM live_start_claims WHERE session_id=:sid"),
                         {"sid":session_id}).scalar()
        if claim:
            raise HTTPException(409,"Chưa xác nhận Agent đã ngừng phát; không thể xóa phiên còn giữ khóa LIVE")
        # SQLite does not enforce ON DELETE CASCADE unless foreign keys are enabled; delete children explicitly.
        from app.live_timeline import LiveTimeline, TimelineMarker
        db.execute(delete(TimelineMarker).where(TimelineMarker.timeline_id.in_(
            select(LiveTimeline.id).where(LiveTimeline.session_id == session_id))))
        db.execute(delete(LiveTimeline).where(LiveTimeline.session_id == session_id))
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
        item = LiveSession(name=f"{source.name} (bản sao)"[:200], status="DRAFT", owner_id=owner["id"], agent_id=None, shop_id=None, tiktok_account_id=source.tiktok_account_id,
                           playback_mode=source.playback_mode or ADVANCED_LEGACY, main_media_id=source.main_media_id,
                           video_ids=source.video_ids, video_order=source.video_order or "sequential",
                           video_loop=source.video_loop is not False)
        db.add(item)
        db.flush()
        replace_products(db, item.id, ids)
        event(db, item.id, "CREATED", detail=f"duplicate_of={session_id}")
        if is_single(source) and source.active_timeline_id and session_videos(source):
            # The copy starts from the timeline that ran, as a DRAFT to review again (products may have changed).
            from app.live_timeline import copy_markers, new_version
            timeline = new_version(db, item, owner["id"], note=f"Sao chép từ phiên #{session_id}")
            copy_markers(db, source.active_timeline_id, timeline.id)
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
        if item.status == "DRAFT":
            return abandon_start(db, owner, item)
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
        from app.tiktok_chat import release_session
        release_session(owner["id"], item.agent_id)
        if item.agent_id:
            cancel_commands(item.agent_id, session_id=session_id)
        stop = None
        if playing:
            stop = create_command(item.agent_id, owner["id"], "playback_stop", session_id=session_id,
                                  idempotency_key=f"finish:{session_id}",
                                  context={"module":"live_sessions","action":"finish","session_id":session_id})
        elif agent and agent["online"]:
            # User finished and the Agent reports idle; keep an offline claim reserved
            # until a confirmed stop/reconciliation rather than guessing.
            release_live_claim(db,session_id)
            db.commit()
        return {**serialize(db, item), "command": stop}

def abandon_start(db, owner: dict, item):
    """Cancel a start whose outcome is unknown (DRAFT still holding the claim), when it is provably not on air."""
    from app.cloud_agents import cancel_commands
    from app.live import playback_of
    if item.id not in claims_of_agent(db, item.agent_id or ""):
        raise HTTPException(409, "Phiên chưa bắt đầu")
    cancel_commands(item.agent_id, session_id=item.id)
    agent = agent_by_id(owner["id"], item.agent_id)
    playback = playback_of(agent)
    if agent is None or not agent["online"]:
        raise HTTPException(409, "Windows Agent đang offline; chưa xác nhận được phiên không phát nên vẫn giữ khóa tài khoản")
    if in_flight(item.id, item.agent_id) is not None or (playback.get("running") and playback.get("session_id") == item.id):
        raise HTTPException(409, "Agent đang thực hiện lệnh bắt đầu; chờ kết quả rồi thử lại")
    agent_id = item.agent_id
    release_live_claim(db, item.id)
    item.agent_id = None
    event(db, item.id, "START_ABANDONED", detail=f"agent={agent_id}")
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
