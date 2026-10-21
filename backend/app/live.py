"""LIVE console API for an OWNER: a facade over one of their Windows Agents, which drives OBS on that PC.

Nothing here touches OBS directly. Every call names its Agent (`agent_id`); the server checks it belongs to the caller
(`owner_agents.owned_agent`) and never falls back to an "Agent in use" stored per account, so two browser tabs working with
two Agents of the same OWNER cannot affect each other. Status is the Agent's latest heartbeat (`PlaybackEngine.status()`);
commands go through `cloud_agents`. Product LIVE sessions themselves are managed in `live_sessions`.
"""
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select

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


def agent_call(owner: dict, agent_id: str, kind: str, payload: dict | None = None, timeout: float = 10):
    """A call whose answer the page needs now (OBS inputs, saved config): the Agent's result or an error."""
    from app.cloud_agents import rpc
    agent = owned_agent(owner, agent_id, online=True)
    return rpc(agent["id"], owner["id"], kind, payload, timeout)


def control(owner: dict, agent_id: str, kind: str, payload: dict | None = None, idempotency_key: str | None = None):
    """A playback control as a tracked command: 200 when the Agent confirmed within a few seconds, else 202 with the
    command to poll. The same Idempotency-Key (e.g. a retried click) never runs the control twice."""
    from app.cloud_agents import respond, run_command
    agent = owned_agent(owner, agent_id, online=True)
    return respond(run_command(agent["id"], owner["id"], kind, payload, wait=CONTROL_WAIT_SECONDS,
                               idempotency_key=idempotency_key))


@router.get("/status")
def get_status(owner: Owner, agent_id: AgentId):
    from app.live_sessions import sync_agent_events
    agent = owned_agent(owner, agent_id)
    sync_agent_events(owner["id"])
    return {**playback_of(agent), "agent": public(agent)}


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
        return [{"id":p.id,"shop_id":p.shop_id,"name":p.name,"sku":p.sku,
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
        return [{"id": p.id, "shop_id": p.shop_id, "name": p.name, "sku": p.sku, "image_url": public_url(p.image_url, p.owner_id), "status": p.status,
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
