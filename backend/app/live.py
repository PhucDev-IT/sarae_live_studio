"""LIVE console API for an OWNER: a facade over their Windows Agent, which drives OBS on the OWNER's PC.

Nothing here touches OBS directly. Commands go to the active Agent through `cloud_agents.rpc` (the Agent's real result
or error comes back), and status is the Agent's latest heartbeat (`PlaybackEngine.status()`, same shape as before).
Product LIVE sessions themselves are managed in `live_sessions`.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.catalog import Product
from app.database import SessionLocal
from app.identity import current_owner
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
    if agent is None:
        status["error"] = "Chưa có Windows Agent nào được kích hoạt cho tài khoản này"
    elif not agent["online"]:
        status.update(connected=False, error=f"Windows Agent “{agent['hostname']}” đang offline")
    return status


def agent_call(owner: dict, kind: str, payload: dict | None = None, timeout: float = 10):
    from app.cloud_agents import rpc
    from app.owner_agents import require_online_agent
    agent = require_online_agent(owner["id"])
    return rpc(agent["id"], owner["id"], kind, payload, timeout)


@router.get("/status")
def get_status(owner: Owner):
    from app.live_sessions import sync_agent_events
    from app.owner_agents import active_agent, public
    sync_agent_events(owner["id"])
    agent = active_agent(owner["id"])
    return {**playback_of(agent), "agent": public(agent)}


@router.post("/config")
def set_config(body: ObsConfig, owner: Owner):
    """Save the OBS connection on the Agent's PC (an empty password keeps the saved one) and return its status."""
    status = agent_call(owner, "obs_configure", body.model_dump(), timeout=15)
    return {**IDLE, **(status or {})}


@router.post("/obs/inputs")
def list_obs_inputs(body: ObsConnection, owner: Owner):
    """Real input names from the OBS next to the Agent, so the UI can offer them; does not change the saved config."""
    return agent_call(owner, "obs_inputs", body.model_dump(), timeout=15)


@router.post("/pause")
def pause_video(owner: Owner):
    return agent_call(owner, "playback_pause")


@router.post("/resume")
def resume_video(owner: Owner):
    return agent_call(owner, "playback_resume")


@router.post("/next")
def next_video(owner: Owner):
    return agent_call(owner, "playback_next")


@router.post("/previous")
def previous_video(owner: Owner):
    return agent_call(owner, "playback_previous")


@router.post("/select")
def play_selected(body: SelectPayload, owner: Owner):
    return agent_call(owner, "playback_select", {"index": body.index})


@router.post("/seek")
def seek_video(body: SeekPayload, owner: Owner):
    return agent_call(owner, "playback_seek", {"ms": body.milliseconds})


@router.post("/stop")
def stop_video(owner: Owner):
    """Stop playback; a LIVE session that is playing ends with it (kept in history)."""
    from app.live_sessions import playing_session, finish_session
    session_id = playing_session(owner["id"])
    if session_id is not None:
        return finish_session(session_id, owner)
    return agent_call(owner, "playback_stop")


@router.get("/products")
def available_products(owner: Owner):
    """The owner's products with how many LIVE videos each has on the active Agent."""
    from app.media import library_agent, library
    from app.product_videos import ProductLiveVideo
    agent = library_agent(owner)
    present = {mid for mid, row in library(agent["id"]).items() if row["available"]} if agent else set()
    with SessionLocal() as db:
        products = db.scalars(select(Product).where(Product.owner_id == owner["id"]).order_by(Product.id)).all()
        counts = {}
        if agent:
            for row in db.scalars(select(ProductLiveVideo).where(ProductLiveVideo.owner_id == owner["id"],
                                                                 ProductLiveVideo.agent_id == agent["id"])).all():
                if row.media_id in present:
                    counts[row.product_id] = counts.get(row.product_id, 0) + 1
        return [{"id": p.id, "name": p.name, "sku": p.sku, "image_url": p.image_url, "status": p.status,
                 "price": p.price, "video_count": counts.get(p.id, 0)} for p in products]


@router.post("/product-session/cancel-switch")
def cancel_product_switch(owner: Owner):
    from app.live_sessions import playing_session, record_event
    result = agent_call(owner, "playback_cancel_pending")
    session_id = playing_session(owner["id"])
    cancelled = (result or {}).get("cancelled_product_id")
    if session_id is not None and cancelled is not None:
        record_event(session_id, "SWITCH_CANCELLED", cancelled)
    return {"ok": True}
