"""Owner-owned playlists pointing to local media IDs on a specific Agent.

The cloud stores only metadata and sends media IDs, never absolute Windows paths.
"""
import json
import sqlite3
import uuid
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from app.database import DATA_DIR
from app.identity import auth, allow_owner, db_open
from app.agent_library import connection as media_connection
from app.cloud_agents import queue_command, CommandBody

router=APIRouter(prefix="/api/agent-playlists",tags=["Agent Playlists"])
DB=DATA_DIR/"agent_playlists.sqlite3"

def connection():
    db=sqlite3.connect(DB,timeout=10)
    db.row_factory=sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS playlists (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,agent_id TEXT NOT NULL,
        name TEXT NOT NULL,media_json TEXT NOT NULL,loop INTEGER NOT NULL DEFAULT 1)""")
    return db

class PlaylistBody(BaseModel):
    agent_id:str=Field(min_length=1,max_length=80)
    name:str=Field(min_length=1,max_length=150)
    media_ids:list[str]=Field(min_length=1,max_length=300)
    loop:bool=True

def check_owner(authorization:str|None, agent_id:str):
    with db_open() as db:
        row=db.execute("SELECT owner_id FROM registered_agents WHERE id=? AND enabled=1",(agent_id,)).fetchone()
    if row is None:raise HTTPException(404,"Không tìm thấy Agent")
    return allow_owner(authorization,row["owner_id"]),row["owner_id"]

def owner_write_access(authorization:str|None,agent_id:str):
    actor,owner_id=check_owner(authorization,agent_id)
    if actor["role"]!="OWNER" or actor["id"]!=owner_id:
        raise HTTPException(403,"Chỉ OWNER sở hữu Agent mới có quyền sửa và phát playlist")
    return owner_id

def check_media(agent_id:str,ids:list[str]):
    if len(ids)!=len(set(ids)):raise HTTPException(422,"Playlist có video trùng")
    with media_connection() as db:
        rows=db.execute("SELECT media_id FROM agent_media WHERE agent_id=? AND available=1",
                        (agent_id,)).fetchall()
    available={x["media_id"] for x in rows}
    if not set(ids).issubset(available):
        raise HTTPException(422,"Có video chưa đồng bộ hoặc không còn ở Agent")

def output(row):
    return {"id":row["id"],"agent_id":row["agent_id"],"name":row["name"],
            "media_ids":json.loads(row["media_json"]),"loop":bool(row["loop"])}

@router.get("")
def list_playlists(agent_id:str,authorization:str|None=Header(None)):
    check_owner(authorization,agent_id)
    with connection() as db:
        return [output(r) for r in db.execute("SELECT * FROM playlists WHERE agent_id=? ORDER BY name",
                                               (agent_id,)).fetchall()]

@router.post("")
def create_playlist(body:PlaylistBody,authorization:str|None=Header(None)):
    owner_id=owner_write_access(authorization,body.agent_id)
    check_media(body.agent_id,body.media_ids)
    pid="pl_"+uuid.uuid4().hex
    with connection() as db:
        db.execute("INSERT INTO playlists VALUES(?,?,?,?,?,?)",
                   (pid,owner_id,body.agent_id,body.name.strip(),json.dumps(body.media_ids),int(body.loop)))
    return {"id":pid,**body.model_dump()}

@router.put("/{playlist_id}")
def update_playlist(playlist_id:str,body:PlaylistBody,authorization:str|None=Header(None)):
    with connection() as db:
        row=db.execute("SELECT * FROM playlists WHERE id=?",(playlist_id,)).fetchone()
        if row is None:raise HTTPException(404,"Không tìm thấy playlist")
        owner_write_access(authorization,row["agent_id"])
        if body.agent_id!=row["agent_id"]:
            raise HTTPException(422,"Không được chuyển playlist sang Agent khác")
        check_media(body.agent_id,body.media_ids)
        db.execute("UPDATE playlists SET name=?,media_json=?,loop=? WHERE id=?",
                   (body.name.strip(),json.dumps(body.media_ids),int(body.loop),playlist_id))
    return {"id":playlist_id,**body.model_dump()}

@router.delete("/{playlist_id}")
def delete_playlist(playlist_id:str,authorization:str|None=Header(None)):
    with connection() as db:
        row=db.execute("SELECT * FROM playlists WHERE id=?",(playlist_id,)).fetchone()
        if row is None:raise HTTPException(404,"Không tìm thấy playlist")
        owner_write_access(authorization,row["agent_id"])
        db.execute("DELETE FROM playlists WHERE id=?",(playlist_id,))
    return {"deleted":playlist_id}

@router.post("/{playlist_id}/play")
def play_playlist(playlist_id:str,authorization:str|None=Header(None)):
    with connection() as db:
        row=db.execute("SELECT * FROM playlists WHERE id=?",(playlist_id,)).fetchone()
    if row is None:raise HTTPException(404,"Không tìm thấy playlist")
    owner_write_access(authorization,row["agent_id"])
    return queue_command(CommandBody(agent_id=row["agent_id"],kind="local_playlist_start",
                    payload={"media_ids":json.loads(row["media_json"]),"loop":bool(row["loop"])}),authorization)
