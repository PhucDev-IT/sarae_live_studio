"""The Windows Agents of an OWNER, and the ownership check every Agent-targeted action goes through.

An OWNER may run several Agents at once (license `max_agents`), and may have several browser tabs open, each working with
a different Agent. So the server never keeps an "Agent in use" per account: every request that acts on an Agent names it
(`agent_id` in the path, query or body), or takes it from the business object it belongs to (`live_session.agent_id`).
`owned_agent` then checks the Agent belongs to the caller, is not revoked and that the license is valid.
"""
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.identity import db_open, license_not_expired
from app.ownership import Owner

ONLINE_SECONDS = 35
AGENT_ID_PATTERN = r"^[A-Za-z0-9_-]{1,80}$"
# Required query parameter naming the Windows Agent a request acts on.
AgentId = Annotated[str, Query(pattern=AGENT_ID_PATTERN, description="Windows Agent the request acts on")]

router = APIRouter(prefix="/api/owner", tags=["OWNER workspace"])


def _heartbeats(ids: list[str]) -> dict[str, tuple[str, str]]:
    from app.cloud_agents import _db
    if not ids:
        return {}
    with _db() as db:
        return {aid: (seen, payload) for aid, seen, payload in db.execute(
            f"SELECT agent_id,last_seen,status_json FROM agent_status WHERE agent_id IN ({','.join('?' * len(ids))})", ids)}


def _describe(row, beat) -> dict:
    seen, payload = beat or (None, None)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(seen)).total_seconds() if seen else None
    return {"id": row["id"], "hostname": row["hostname"], "platform": row["platform"],
            "created_at": row["created_at"], "last_seen": seen,
            "online": age is not None and age < ONLINE_SECONDS, "status": json.loads(payload) if payload else {}}


def agents_of(owner_id: str) -> list[dict]:
    """Enabled Agents of the owner with their latest heartbeat, most recently seen first."""
    with db_open() as registry:
        rows = registry.execute("SELECT id,hostname,platform,created_at FROM registered_agents WHERE owner_id=? AND enabled=1",
                                (owner_id,)).fetchall()
    beats = _heartbeats([r["id"] for r in rows])
    return sorted((_describe(r, beats.get(r["id"])) for r in rows), key=lambda a: a["last_seen"] or "", reverse=True)


def owned_agent(owner: dict, agent_id: str | None, online: bool = False) -> dict:
    """The caller's Agent `agent_id`, or an error: 404 not theirs/unknown, 403 revoked or license invalid,
    409 offline (when `online`). Another owner's Agent is a 404 so its existence is not revealed."""
    if not agent_id:
        raise HTTPException(422, "Chọn Windows Agent cần thao tác (agent_id)")
    with db_open() as registry:
        row = registry.execute("""SELECT a.id,a.hostname,a.platform,a.created_at,a.enabled,l.enabled AS license_enabled,l.expires_at
            FROM registered_agents a JOIN licenses l ON l.owner_id=a.owner_id
            WHERE a.id=? AND a.owner_id=?""", (agent_id, owner["id"])).fetchone()
    if row is None:
        raise HTTPException(404, "Không tìm thấy Windows Agent")
    if not row["enabled"]:
        raise HTTPException(403, "Windows Agent này đã bị thu hồi")
    if not row["license_enabled"] or not license_not_expired(row["expires_at"]):
        raise HTTPException(403, "License đã hết hạn hoặc bị khóa")
    agent = _describe(row, _heartbeats([agent_id]).get(agent_id))
    if online and not agent["online"]:
        raise HTTPException(409, f"Windows Agent “{agent['hostname']}” đang offline")
    return agent


def send_command(owner: dict, agent_id: str, kind: str, payload: dict | None = None, **options) -> dict:
    """Create a command for one of the owner's online Agents (see cloud_agents.create_command for options)."""
    from app.cloud_agents import create_command
    agent = owned_agent(owner, agent_id, online=True)
    return {"agent": public(agent), **create_command(agent["id"], owner["id"], kind, payload, **options)}


def public(agent: dict | None) -> dict | None:
    return None if agent is None else {k: agent[k] for k in ("id", "hostname", "platform", "online", "last_seen")}


@router.get("/agents")
def list_owner_agents(owner: Owner):
    """The caller's Agents; which one a browser tab works with is kept by that tab, not by the server."""
    return {"agents": [{**public(a), **summary(a)} for a in agents_of(owner["id"])]}


def summary(agent: dict) -> dict:
    """What the Agent picker shows next to each PC (from its last heartbeat; nothing path-like)."""
    status = agent.get("status") or {}
    playback = status.get("playback") or {}
    monitor = ((status.get("verification") or {}).get("state") or {})
    online = agent["online"]
    return {"obs_connected": online and bool(playback.get("connected")),
            "playing": online and bool(playback.get("running")),
            "playback_state": playback.get("state"),
            "playback_media_id": playback.get("current_media_id"),
            "playback_position_ms": playback.get("position_ms"),
            "playback_duration_ms": playback.get("duration_ms"),
            "playback_error": playback.get("error"),
            "mock_live": bool(playback.get("mock_live")),
            "session_id": playback.get("session_id") if online else None,
            "monitor_running": online and bool(monitor.get("running"))}
