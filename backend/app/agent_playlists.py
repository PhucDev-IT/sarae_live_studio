"""Owner-owned playlists pointing to local media IDs on a specific Agent.

The cloud stores only metadata and sends media IDs, never absolute Windows paths. Every call names its Agent and is
checked like any other Agent action (owner, Agent enabled, license valid); SUPER_ADMIN has no access.
"""
import json
import sqlite3
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.agent_library import connection as media_connection
from app.database import DATA_DIR
from app.identity import current_owner
from app.ownership import Owner
from app.owner_agents import AgentId, owned_agent

router = APIRouter(prefix="/api/agent-playlists", tags=["Agent Playlists"], dependencies=[Depends(current_owner)])
DB = DATA_DIR / "agent_playlists.sqlite3"


def connection():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS playlists (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,agent_id TEXT NOT NULL,
        name TEXT NOT NULL,media_json TEXT NOT NULL,loop INTEGER NOT NULL DEFAULT 1)""")
    return db


class PlaylistBody(BaseModel):
    agent_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=150)
    media_ids: list[str] = Field(min_length=1, max_length=300)
    loop: bool = True


def check_media(agent_id: str, ids: list[str]):
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Playlist có video trùng")
    with media_connection() as db:
        rows = db.execute("SELECT media_id FROM agent_media WHERE agent_id=? AND available=1", (agent_id,)).fetchall()
    if not set(ids).issubset({x["media_id"] for x in rows}):
        raise HTTPException(422, "Có video chưa đồng bộ hoặc không còn ở Agent")


def output(row):
    return {"id": row["id"], "agent_id": row["agent_id"], "name": row["name"],
            "media_ids": json.loads(row["media_json"]), "loop": bool(row["loop"])}


def owned_playlist(db, owner: dict, playlist_id: str):
    row = db.execute("SELECT * FROM playlists WHERE id=? AND owner_id=?", (playlist_id, owner["id"])).fetchone()
    if row is None:
        raise HTTPException(404, "Không tìm thấy playlist")
    return row


@router.get("")
def list_playlists(owner: Owner, agent_id: AgentId):
    agent = owned_agent(owner, agent_id)
    with connection() as db:
        return [output(r) for r in db.execute("SELECT * FROM playlists WHERE owner_id=? AND agent_id=? ORDER BY name",
                                               (owner["id"], agent["id"])).fetchall()]


@router.post("")
def create_playlist(body: PlaylistBody, owner: Owner):
    agent = owned_agent(owner, body.agent_id)
    check_media(agent["id"], body.media_ids)
    pid = "pl_" + uuid.uuid4().hex
    with connection() as db:
        db.execute("INSERT INTO playlists VALUES(?,?,?,?,?,?)",
                   (pid, owner["id"], agent["id"], body.name.strip(), json.dumps(body.media_ids), int(body.loop)))
    return {"id": pid, **body.model_dump()}


@router.put("/{playlist_id}")
def update_playlist(playlist_id: str, body: PlaylistBody, owner: Owner):
    with connection() as db:
        row = owned_playlist(db, owner, playlist_id)
        if body.agent_id != row["agent_id"]:
            raise HTTPException(422, "Không được chuyển playlist sang Agent khác")
        owned_agent(owner, row["agent_id"])
        check_media(body.agent_id, body.media_ids)
        db.execute("UPDATE playlists SET name=?,media_json=?,loop=? WHERE id=?",
                   (body.name.strip(), json.dumps(body.media_ids), int(body.loop), playlist_id))
    return {"id": playlist_id, **body.model_dump()}


@router.delete("/{playlist_id}")
def delete_playlist(playlist_id: str, owner: Owner):
    with connection() as db:
        owned_playlist(db, owner, playlist_id)
        db.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
    return {"deleted": playlist_id}


@router.post("/{playlist_id}/play")
def play_playlist(playlist_id: str, owner: Owner):
    """Test playback of a playlist on its own Agent (never another one); refused while a LIVE session plays there."""
    from app.live_sessions import playing_session
    from app.owner_agents import send_command
    with connection() as db:
        row = owned_playlist(db, owner, playlist_id)
    if playing_session(owner["id"], row["agent_id"]) is not None:
        raise HTTPException(409, "Agent này đang phát một phiên LIVE; kết thúc phiên trước khi phát thử playlist")
    return send_command(owner, row["agent_id"], "local_playlist_start",
                        {"media_ids": json.loads(row["media_json"]), "loop": bool(row["loop"])})
