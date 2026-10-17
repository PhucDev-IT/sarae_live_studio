"""Agent-owned local video inventory for SaaS.

Stores metadata only; never uploads MP4 bytes or accepts arbitrary path commands.
"""
import sqlite3
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from app.database import DATA_DIR
from app.identity import verify_agent,auth,allow_owner,db_open

router=APIRouter(prefix="/api/agent-library",tags=["Agent Local Media"])
DB=DATA_DIR/"agent_library.sqlite3"

def connection():
    db=sqlite3.connect(DB,timeout=10)
    db.row_factory=sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS agent_media (
       agent_id TEXT NOT NULL,media_id TEXT NOT NULL,filename TEXT NOT NULL,
       size_bytes INTEGER NOT NULL,modified_ns INTEGER NOT NULL,
       available INTEGER NOT NULL DEFAULT 1,
       PRIMARY KEY(agent_id,media_id))""")
    return db

class Video(BaseModel):
    media_id:str=Field(pattern=r"^[a-f0-9]{64}$")
    filename:str=Field(min_length=1,max_length=255)
    size_bytes:int=Field(ge=0)
    modified_ns:int=Field(ge=0)

class Inventory(BaseModel):
    agent_id:str
    videos:list[Video]=Field(max_length=10000)

@router.post("/sync")
def sync(body:Inventory,x_sarae_agent_key:str|None=Header(None)):
    verify_agent(body.agent_id,x_sarae_agent_key)
    if len({x.media_id for x in body.videos})!=len(body.videos):
        raise HTTPException(422,"Trùng media_id")
    with connection() as db:
        db.execute("UPDATE agent_media SET available=0 WHERE agent_id=?",(body.agent_id,))
        db.executemany("""INSERT INTO agent_media(agent_id,media_id,filename,size_bytes,modified_ns,available)
           VALUES(?,?,?,?,?,1) ON CONFLICT(agent_id,media_id) DO UPDATE SET
           filename=excluded.filename,size_bytes=excluded.size_bytes,
           modified_ns=excluded.modified_ns,available=1""",
           [(body.agent_id,x.media_id,Path(x.filename).name,x.size_bytes,x.modified_ns) for x in body.videos])
    return {"synced":len(body.videos)}

@router.get("/{agent_id}")
def list_media(agent_id:str,authorization:str|None=Header(None)):
    with db_open() as registry:
        row=registry.execute("SELECT owner_id FROM registered_agents WHERE id=? AND enabled=1",(agent_id,)).fetchone()
    if row is None:raise HTTPException(404,"Không tìm thấy Agent")
    allow_owner(authorization,row["owner_id"])
    with connection() as db:
        rows=db.execute("SELECT media_id,filename,size_bytes,modified_ns,available FROM agent_media WHERE agent_id=? ORDER BY filename LIMIT 10000",(agent_id,)).fetchall()
    return {"agent_id":agent_id,"videos":[dict(x) for x in rows]}
