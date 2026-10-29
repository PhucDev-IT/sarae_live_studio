"""LIVE console API for an OWNER: a facade over one of their Windows Agents, which drives OBS on that PC.

Nothing here touches OBS directly. Every call names its Agent (`agent_id`); the server checks it belongs to the caller
(`owner_agents.owned_agent`) and never falls back to an "Agent in use" stored per account, so two browser tabs working with
two Agents of the same OWNER cannot affect each other. Status is the Agent's latest heartbeat (`PlaybackEngine.status()`);
commands go through `cloud_agents`. Product LIVE sessions themselves are managed in `live_sessions`.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.catalog import Product
from app.database import SessionLocal
from app.identity import current_owner
from app.owner_agents import AgentId, owned_agent, public
from app.ownership import Owner

router = APIRouter(prefix="/api/live", tags=["LIVE / OBS"], dependencies=[Depends(current_owner)])


class ObsConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=4455, ge=1, le=65535)
    password: str = ""
    input_name: str = "Sarae Video"
    dual_mode: bool = False
    # A/B Media Source names for dual mode; empty falls back to "<input_name> A" / "<input_name> B".
    source_a: str = Field(default="", max_length=200)
    source_b: str = Field(default="", max_length=200)


class ObsConnection(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=4455, ge=1, le=65535)
    password: str = ""


class SeekPayload(BaseModel):
    milliseconds: int = Field(ge=0)


class SelectPayload(BaseModel):
    index: int = Field(ge=0)


IDLE = {"connected": False, "running": False, "paused": False, "loop": False, "session_mode": "product",
        "playlist_id": None, "product_id": None, "pending_product_id": None, "session_id": None, "queue_ids": [],
        "index": 0, "count": 0, "last_status": "", "retry_count": 0, "error_count": 0, "transition_count": 0,
        "recovery_count": 0, "session_seconds": 0}


def playback_of(agent: dict | None) -> dict:
    """The Agent's last reported playback (IDLE when unknown); `connected` also requires the Agent online."""
    reported = ((agent or {}).get("status") or {}).get("playback") or {}
    status = {**IDLE, **reported}
    if agent is not None and not agent["online"]:
        status.update(connected=False, error=f"Windows Agent “{agent['hostname']}” đang offline")
    return status


CONTROL_WAIT_SECONDS = 5
IdempotencyKey = Header(None, alias="Idempotency-Key")


def agent_call(owner: dict, agent_id: str, kind: str, payload: dict | None = None, timeout: float = 10,
               session_id: int | None = None):
    """A call whose answer the page needs now (OBS inputs, saved config): the Agent's result or an error."""
    from app.cloud_agents import rpc
    agent = owned_agent(owner, agent_id, online=True)
    return rpc(agent["id"], owner["id"], kind, payload, timeout, session_id=session_id)


def control(owner: dict, agent_id: str, kind: str, payload: dict | None = None,
            idempotency_key: str | None = None, session_id: int | None = None):
    """A playback control as a tracked command: 200 when the Agent confirmed within a few seconds, else 202 with the
    command to poll. The same Idempotency-Key (e.g. a retried click) never runs the control twice."""
    from app.cloud_agents import respond, run_command
    agent = owned_agent(owner, agent_id, online=True)
    return respond(run_command(agent["id"], owner["id"], kind, payload, wait=CONTROL_WAIT_SECONDS,
                               idempotency_key=idempotency_key, session_id=session_id))


@router.get("/status")
def get_status(owner: Owner, agent_id: AgentId):
    from app.live_sessions import sync_agent_events
    agent = owned_agent(owner, agent_id)
    sync_agent_events(owner["id"])
    return {**playback_of(agent), "pin": ((agent.get("status") or {}).get("pin") or {}), "agent": public(agent)}


TIMELINE_POLICY = r"^(HOLD_UNTIL_RESUME|RESUME_AT_NEXT_MARKER)$"


class PinInput(BaseModel):
    product_id: int = Field(gt=0)
    confirmed_token: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{1,80}$")
    # Single Video Timeline session: how long the operator's pin wins over the timeline (default: until resume).
    timeline_policy: str = Field(default="HOLD_UNTIL_RESUME", pattern=TIMELINE_POLICY)


def hold_timeline(owner: dict, agent_id: str, session_id: int, product_id: int | None, policy: str,
                  idempotency_key: str | None):
    """Before a manual pin of a Single Video session: tell its pin scheduler to stop acting, so it never overwrites
    the operator. Same Agent lane as the pin, so it always runs first."""
    from app.cloud_agents import create_command
    from app.live_sessions import LiveSession
    from app.live_timeline import is_single
    with SessionLocal() as db:
        item = db.get(LiveSession, session_id)
        if item is None or not is_single(item):
            return
    create_command(agent_id, owner["id"], "timeline_hold",
                   {"session_id": session_id, "product_id": product_id, "policy": policy},
                   idempotency_key=f"hold:{idempotency_key}" if idempotency_key else None, session_id=session_id)


class AutoPinInput(BaseModel):
    enabled: bool


def _pin_account(db, owner: dict, agent_id: str) -> tuple[str, int]:
    from app.live_sessions import LiveSession, playing_session
    session_id = playing_session(owner["id"], agent_id)
    session = db.get(LiveSession, session_id) if session_id is not None else None
    if (session is None or session.status != "RUNNING" or session.owner_id != owner["id"]
            or session.agent_id != agent_id or session.tiktok_account_id is None):
        raise HTTPException(409, "Cần một phiên LIVE đang phát với TikTok Account đã xác minh")
    row = db.execute(text("""SELECT a.user_id FROM tiktok_accounts a
        JOIN user_tiktok_accounts ua ON ua.account_id=a.id
        WHERE a.id=:id AND ua.owner_id=:owner"""),
                     {"id": session.tiktok_account_id, "owner": owner["id"]}).first()
    if not row or not row[0]:
        raise HTTPException(409, "Chưa xác minh TikTok Account của phiên LIVE")
    return str(row[0]), session.id


def _pin_mapping(product: Product, expected_user_id: str) -> dict:
    return {"tiktok_product_id": product.tiktok_product_id, "name": product.name,
            "image_url": product.image_url or "", "price_text": str(int(product.price)) if product.price is not None else "",
            "expected_user_id": expected_user_id}


@router.get("/pin-state")
def get_pin_state(owner: Owner, agent_id: AgentId, product_id: int | None = None):
    owned_agent(owner, agent_id, online=True)
    target = None
    if product_id is not None:
        with SessionLocal() as db:
            account, session_id = _pin_account(db, owner, agent_id)
            product = db.get(Product, product_id)
            if product is None or product.owner_id != owner["id"]:
                raise HTTPException(404, "Không tìm thấy sản phẩm")
            target = _pin_mapping(product, account)
    return agent_call(owner, agent_id, "GET_PIN_STATE", {"target": target} if target else {}, timeout=15,
                      session_id=session_id if target else None)


@router.post("/pin")
def pin_product(body: PinInput, owner: Owner, agent_id: AgentId,
                idempotency_key: str | None = IdempotencyKey):
    owned_agent(owner, agent_id, online=True)
    with SessionLocal() as db:
        account, session_id = _pin_account(db, owner, agent_id)
        product = db.get(Product, body.product_id)
        if product is None or product.owner_id != owner["id"]:
            raise HTTPException(404, "Không tìm thấy sản phẩm")
        target = _pin_mapping(product, account)
    hold_timeline(owner, agent_id, session_id, body.product_id, body.timeline_policy, idempotency_key)
    return control(owner, agent_id, "PIN_PRODUCT", {"session_id": session_id, "target": target,
        "confirmed_token": body.confirmed_token}, idempotency_key, session_id)


@router.post("/unpin")
def unpin_product(owner: Owner, agent_id: AgentId, idempotency_key: str | None = IdempotencyKey,
                  timeline_policy: str = Query("HOLD_UNTIL_RESUME", pattern=TIMELINE_POLICY)):
    with SessionLocal() as db:
        account, session_id = _pin_account(db, owner, agent_id)
    hold_timeline(owner, agent_id, session_id, None, timeline_policy, idempotency_key)
    return control(owner, agent_id, "UNPIN_PRODUCT", {"session_id": session_id,
        "expected_user_id": account}, idempotency_key, session_id)


@router.post("/auto-pin")
def set_auto_pin(body: AutoPinInput, owner: Owner, agent_id: AgentId,
                 idempotency_key: str | None = IdempotencyKey):
    owned_agent(owner, agent_id, online=True)
    products = []
    session_id = None
    if body.enabled:
        with SessionLocal() as db:
            from app.live_sessions import LiveSession
            account, session_id = _pin_account(db, owner, agent_id)
            session = db.scalar(select(LiveSession).where(LiveSession.owner_id == owner["id"],
                LiveSession.agent_id == agent_id, LiveSession.id == session_id, LiveSession.status == "RUNNING"))
            if session is None:
                raise HTTPException(409, "Phiên LIVE đã kết thúc")
            # Pin-per-video would fight the timeline scheduler, which owns the pins of a Single Video session.
            from app.live_timeline import require_legacy
            require_legacy(session, "Tự động ghim theo video")
            rows =db.scalars(select(Product).where(Product.owner_id == owner["id"])).all()
            if len(rows) > 300:
                raise HTTPException(422, "Catalog có hơn 300 sản phẩm; chưa thể bật tự động ghim cho toàn bộ catalog")
            products = [{"sarae_product_id": p.id, **_pin_mapping(p, account)} for p in rows]
    return control(owner, agent_id, "SET_AUTO_PIN", {"enabled": body.enabled, "session_id": session_id,
        "products": products}, idempotency_key, session_id)


@router.post("/config")
def set_config(body: ObsConfig, owner: Owner, agent_id: AgentId):
    """Save the OBS connection on the Agent's PC (an empty password keeps the saved one) and return its status."""
    status = agent_call(owner, agent_id, "obs_configure", body.model_dump(), timeout=15)
    return {**IDLE, **(status or {})}


@router.post("/obs/inputs")
def list_obs_inputs(body: ObsConnection, owner: Owner, agent_id: AgentId):
    """Real input names from the OBS next to the Agent, so the UI can offer them; does not change the saved config."""
    return agent_call(owner, agent_id, "obs_inputs", body.model_dump(), timeout=15)


@router.post("/pause")
def pause_video(owner: Owner, agent_id: AgentId, idempotency_key: str | None = IdempotencyKey):
    return control(owner, agent_id, "playback_pause", idempotency_key=idempotency_key)


@router.post("/resume")
def resume_video(owner: Owner, agent_id: AgentId, idempotency_key: str | None = IdempotencyKey):
    return control(owner, agent_id, "playback_resume", idempotency_key=idempotency_key)


@router.post("/next")
def next_video(owner: Owner, agent_id: AgentId, idempotency_key: str | None = IdempotencyKey):
    return control(owner, agent_id, "playback_next", idempotency_key=idempotency_key)


@router.post("/previous")
def previous_video(owner: Owner, agent_id: AgentId, idempotency_key: str | None = IdempotencyKey):
    return control(owner, agent_id, "playback_previous", idempotency_key=idempotency_key)


@router.post("/select")
def play_selected(body: SelectPayload, owner: Owner, agent_id: AgentId, idempotency_key: str | None = IdempotencyKey):
    return control(owner, agent_id, "playback_select", {"index": body.index}, idempotency_key)


@router.post("/seek")
def seek_video(body: SeekPayload, owner: Owner, agent_id: AgentId, idempotency_key: str | None = IdempotencyKey):
    return control(owner, agent_id, "playback_seek", {"ms": body.milliseconds}, idempotency_key)


@router.post("/stop")
def stop_video(owner: Owner, agent_id: AgentId, idempotency_key: str | None = IdempotencyKey):
    """Stop playback on that Agent; a LIVE session it is playing ends with it (kept in history)."""
    from app.live_sessions import playing_session, finish_session
    owned_agent(owner, agent_id)
    session_id = playing_session(owner["id"], agent_id)
    if session_id is not None:
        return finish_session(session_id, owner)
    return control(owner, agent_id, "playback_stop", idempotency_key=idempotency_key)


@router.get("/catalog-products")
def catalog_products(owner: Owner):
    """Owner catalog independent of Windows Agent, for drafting future LIVE sessions.

    Counts are AVAILABLE videos of the owner's Media Library linked to each product. No cross-owner exposure.
    """
    from app.media_access import public_url
    from app.media_library import product_video_counts
    with SessionLocal() as db:
        products=db.scalars(select(Product).where(Product.owner_id==owner["id"]).order_by(Product.id)).all()
        counts=product_video_counts(db,owner["id"])
        return [{"id":p.id,"shop_id":p.shop_id,"tiktok_product_id":p.tiktok_product_id,"name":p.name,"sku":p.sku,
                 "image_url":public_url(p.image_url,p.owner_id),"status":p.status,
                 "price":p.price,"video_count":counts.get(p.id,0)} for p in products]


@router.get("/products")
def available_products(owner: Owner, agent_id: AgentId):
    """The owner's products with how many Media Library videos each has (the same for every Agent)."""
    from app.media_access import public_url
    from app.media_library import product_video_counts
    owned_agent(owner, agent_id)
    with SessionLocal() as db:
        products = db.scalars(select(Product).where(Product.owner_id == owner["id"]).order_by(Product.id)).all()
        counts = product_video_counts(db, owner["id"])
        return [{"id": p.id, "shop_id": p.shop_id, "tiktok_product_id": p.tiktok_product_id, "name": p.name, "sku": p.sku, "image_url": public_url(p.image_url, p.owner_id), "status": p.status,
                 "price": p.price, "video_count": counts.get(p.id, 0)} for p in products]


@router.post("/product-session/cancel-switch")
def cancel_product_switch(owner: Owner, agent_id: AgentId):
    from app.live_sessions import playing_session, record_event
    result = agent_call(owner, agent_id, "playback_cancel_pending", timeout=CONTROL_WAIT_SECONDS)
    session_id = playing_session(owner["id"], agent_id)
    cancelled = (result or {}).get("cancelled_product_id")
    if session_id is not None and cancelled is not None:
        record_event(session_id, "SWITCH_CANCELLED", cancelled)
    return {"ok": True}
