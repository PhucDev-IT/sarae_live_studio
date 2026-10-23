"""Playback policy of a LIVE session: video order and automatic product rotation (docs/PLAYBACK_POLICY.md, spec §6).

The Windows Agent applies the policy itself (it keeps rotating if the Cloud is unreachable); the Cloud stores it per
session (live_sessions.playback_policy), remembers the last one as the owner's default for new sessions, and pushes it
with the session's lineup (`playback_policy` command) whenever it changes, the session starts, its products change,
or the operator switches product by hand (which pauses the automatic rotation, as the spec requires).
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.database import SessionLocal
from app.identity import current_owner, owner_settings, update_owner_settings
from app.ownership import Owner

router = APIRouter(prefix="/api/live/sessions", tags=["LIVE Sessions"], dependencies=[Depends(current_owner)])
MAX_PRODUCTS = 300
MAX_MEDIA = 3000


class PlaybackPolicy(BaseModel):
    video_order: Literal["sequential", "random", "shuffle"] = "sequential"
    product_rotation: Literal["off", "sequential", "random", "shuffle"] = "off"
    rotate_after_videos: int | None = Field(default=3, ge=1, le=50)
    rotate_after_minutes: int | None = Field(default=None, ge=1, le=240)
    switch_mode: Literal["after_current", "immediate"] = "after_current"
    auto: bool = True

    @model_validator(mode="after")
    def needs_a_limit(self):
        if self.product_rotation != "off" and self.rotate_after_videos is None and self.rotate_after_minutes is None:
            raise ValueError("Tự đổi sản phẩm cần “đổi sau N video” hoặc “đổi sau N phút”")
        return self


def owner_default(owner_id: str) -> PlaybackPolicy:
    try:
        return PlaybackPolicy.model_validate(owner_settings(owner_id).get("playback_policy") or {})
    except ValueError:
        return PlaybackPolicy()


def policy_of(item) -> PlaybackPolicy:
    """The session's policy, else the owner's last used one, else the defaults."""
    if item.playback_policy:
        try:
            return PlaybackPolicy.model_validate_json(item.playback_policy)
        except ValueError:
            pass
    return owner_default(item.owner_id)


def lineup(db, owner_id: str, session_id: int) -> list[dict]:
    """The session's products in order with their playable videos, bounded for the Agent command."""
    from app.live_sessions import ids_for
    from app.media_library import product_video_ids
    out, total = [], 0
    for pid in ids_for(db, session_id)[:MAX_PRODUCTS]:
        media = product_video_ids(db, owner_id, pid)[:max(0, MAX_MEDIA - total)]
        if media:
            out.append({"product_id": pid, "media_ids": media})
            total += len(media)
    return out


def is_default(policy: PlaybackPolicy) -> bool:
    """Sequential videos, no rotation: what the Agent does without any policy."""
    return policy.video_order == "sequential" and policy.product_rotation == "off"


def push(owner_id: str, session_id: int, always: bool = False) -> tuple[bool, str | None]:
    """Send the session's policy and lineup to its Agent if the session is RUNNING. Without `always` (session start,
    lineup change), a default policy is not sent: the Agent plays sequentially without one. Never raises."""
    from app.cloud_agents import create_command
    from app.live_sessions import LiveSession
    with SessionLocal() as db:
        item = db.get(LiveSession, session_id)
        if item is None or item.status != "RUNNING" or not item.agent_id:
            return False, None
        policy = policy_of(item)
        if not always and is_default(policy):
            return False, None
        payload = {"session_id": session_id, "policy": policy.model_dump(),
                   "lineup": lineup(db, owner_id, session_id)}
        agent_id = item.agent_id
    try:
        create_command(agent_id, owner_id, "playback_policy", payload, session_id=session_id)
        return True, None
    except HTTPException as exc:
        return False, str(exc.detail)
    except Exception as exc:  # noqa: BLE001 - a policy push must never break the action that triggered it
        return False, f"{type(exc).__name__}: {exc}"[:200]


def store(owner_id: str, session_id: int, policy: PlaybackPolicy, event_kind: str | None = None) -> None:
    from app.live_sessions import LiveSession, event
    with SessionLocal() as db:
        item = db.get(LiveSession, session_id)
        item.playback_policy = policy.model_dump_json()
        if event_kind:
            event(db, session_id, event_kind, item.current_product_id)
        db.commit()


def pause_auto(owner_id: str, session_id: int) -> None:
    """The operator switched product by hand: automatic rotation waits until they turn it back on (spec §6)."""
    from app.live_sessions import LiveSession
    with SessionLocal() as db:
        item = db.get(LiveSession, session_id)
        policy = policy_of(item) if item is not None else None
    if policy is None or policy.product_rotation == "off" or not policy.auto:
        return
    store(owner_id, session_id, policy.model_copy(update={"auto": False}), "AUTO_PAUSED")
    push(owner_id, session_id, always=True)


def _session(owner: dict, session_id: int):
    from app.live_sessions import get_session
    with SessionLocal() as db:
        item = get_session(db, session_id, owner)
        db.expunge(item)
        return item


def _view(owner: dict, session_id: int, sent: bool | None = None, error: str | None = None) -> dict:
    view = {"policy": policy_of(_session(owner, session_id)).model_dump()}
    if sent is not None:
        view.update(sent=sent, agent_error=error)
    return view


@router.get("/{session_id}/playback-policy")
def get_policy(session_id: int, owner: Owner):
    return _view(owner, session_id)


@router.put("/{session_id}/playback-policy")
def put_policy(session_id: int, body: PlaybackPolicy, owner: Owner):
    item = _session(owner, session_id)
    if item.status == "ENDED":
        raise HTTPException(409, "Phiên đã kết thúc")
    store(owner["id"], session_id, body)
    update_owner_settings(owner["id"], playback_policy=body.model_copy(update={"auto": True}).model_dump())
    sent, error = push(owner["id"], session_id, always=True)
    return _view(owner, session_id, sent, error)


@router.post("/{session_id}/playback-policy/resume")
def resume_auto(session_id: int, owner: Owner):
    item = _session(owner, session_id)
    if item.status == "ENDED":
        raise HTTPException(409, "Phiên đã kết thúc")
    store(owner["id"], session_id, policy_of(item).model_copy(update={"auto": True}), "AUTO_RESUMED")
    sent, error = push(owner["id"], session_id, always=True)
    return _view(owner, session_id, sent, error)


def validate_command(payload: dict) -> dict:
    """Payload check of the `playback_policy` Agent command (cloud_agents.validate_payload)."""
    session_id = payload.get("session_id")
    if not isinstance(session_id, int) or isinstance(session_id, bool) or session_id < 1:
        raise HTTPException(422, "session_id không hợp lệ")
    try:
        policy = PlaybackPolicy.model_validate(payload.get("policy") or {}).model_dump()
    except ValueError as exc:
        raise HTTPException(422, f"policy không hợp lệ: {str(exc).splitlines()[0]}") from None
    items = payload.get("lineup") or []
    if not isinstance(items, list) or len(items) > MAX_PRODUCTS:
        raise HTTPException(422, f"lineup tối đa {MAX_PRODUCTS} sản phẩm")
    from app.cloud_agents import _media_id
    clean, total = [], 0
    for entry in items:
        if not isinstance(entry, dict) or not isinstance(entry.get("product_id"), int) or entry["product_id"] < 1:
            raise HTTPException(422, "lineup: product_id không hợp lệ")
        media = entry.get("media_ids") or []
        if not isinstance(media, list) or any(not _media_id(m) for m in media):
            raise HTTPException(422, "lineup: media_ids không hợp lệ")
        total += len(media)
        clean.append({"product_id": entry["product_id"], "media_ids": list(media)})
    if total > MAX_MEDIA:
        raise HTTPException(422, f"lineup tối đa {MAX_MEDIA} video")
    return {"session_id": session_id, "policy": policy, "lineup": clean}

