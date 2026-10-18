"""Which Windows Agent an OWNER's actions go to, and sending commands to it.

An OWNER may run several Agents (license `max_agents`). The one in use is `owner_settings.active_agent_id` when it is
still enabled; otherwise the most recently seen enabled Agent. Commands reuse `cloud_agents.queue_command`, which keeps
the license, ownership and online checks in one place.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.identity import db_open, owner_settings, update_owner_settings
from app.ownership import Owner

ONLINE_SECONDS = 35

router = APIRouter(prefix="/api/owner", tags=["OWNER workspace"])


def agents_of(owner_id: str) -> list[dict]:
    """Enabled Agents of the owner with their latest heartbeat, most recently seen first."""
    from app.cloud_agents import connection
    with db_open() as registry:
        rows = registry.execute("SELECT id,hostname,created_at FROM registered_agents WHERE owner_id=? AND enabled=1",
                                (owner_id,)).fetchall()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    with connection() as db:
        beats = {aid: (seen, payload) for aid, seen, payload in db.execute(
            f"SELECT agent_id,last_seen,status_json FROM agent_status WHERE agent_id IN ({','.join('?' * len(ids))})", ids)}
    now = datetime.now(timezone.utc)
    result = []
    for r in rows:
        seen, payload = beats.get(r["id"], (None, None))
        age = (now - datetime.fromisoformat(seen)).total_seconds() if seen else None
        result.append({"id": r["id"], "hostname": r["hostname"], "created_at": r["created_at"], "last_seen": seen,
                       "online": age is not None and age < ONLINE_SECONDS,
                       "status": json.loads(payload) if payload else {}})
    return sorted(result, key=lambda a: a["last_seen"] or "", reverse=True)


def active_agent(owner_id: str) -> dict | None:
    agents = agents_of(owner_id)
    chosen = owner_settings(owner_id).get("active_agent_id")
    return next((a for a in agents if a["id"] == chosen), agents[0] if agents else None)


def require_online_agent(owner_id: str) -> dict:
    agent = active_agent(owner_id)
    if agent is None:
        raise HTTPException(409, "Chưa có Windows Agent nào được kích hoạt cho tài khoản này")
    if not agent["online"]:
        raise HTTPException(409, f"Windows Agent “{agent['hostname']}” đang offline")
    return agent


def send_command(owner: dict, authorization: str | None, kind: str, payload: dict | None = None) -> dict:
    """Queue `kind` for the owner's active, online Agent. Returns {agent, command_id}."""
    from app.cloud_agents import CommandBody, queue_command
    agent = require_online_agent(owner["id"])
    queued = queue_command(CommandBody(agent_id=agent["id"], kind=kind, payload=payload or {}), authorization=authorization)
    return {"agent": {"id": agent["id"], "hostname": agent["hostname"]}, "command_id": queued["command_id"]}


class ActiveAgentBody(BaseModel):
    agent_id: str = Field(min_length=1, max_length=80)


def public(agent: dict | None) -> dict | None:
    return None if agent is None else {k: agent[k] for k in ("id", "hostname", "online", "last_seen")}


@router.get("/agents")
def list_owner_agents(owner: Owner):
    agents = agents_of(owner["id"])
    active = active_agent(owner["id"])
    return {"active_agent_id": active["id"] if active else None, "agents": [public(a) for a in agents]}


@router.put("/active-agent")
def set_active_agent(body: ActiveAgentBody, owner: Owner):
    if body.agent_id not in {a["id"] for a in agents_of(owner["id"])}:
        raise HTTPException(404, "Không tìm thấy Agent")
    update_owner_settings(owner["id"], active_agent_id=body.agent_id)
    return list_owner_agents(owner)
