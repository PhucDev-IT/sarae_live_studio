"""Agent-owned local video inventory for SaaS.

Stores metadata only; never uploads MP4 bytes or accepts arbitrary path commands.
Small JPEG thumbnails sent by the Agent are kept as files with random names.
"""
import base64
import binascii
import secrets
import sqlite3
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.database import DATA_DIR
from app.identity import verify_agent,auth,allow_owner,db_open

router=APIRouter(prefix="/api/agent-library",tags=["Agent Local Media"])
DB=DATA_DIR/"agent_library.sqlite3"
THUMB_DIR=DATA_DIR/"media_thumbs"
THUMB_MAX_BYTES=40*1024

def connection():
    db=sqlite3.connect(DB,timeout=10)
    db.row_factory=sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS agent_media (
       agent_id TEXT NOT NULL,media_id TEXT NOT NULL,filename TEXT NOT NULL,
       size_bytes INTEGER NOT NULL,modified_ns INTEGER NOT NULL,
       available INTEGER NOT NULL DEFAULT 1,
       PRIMARY KEY(agent_id,media_id))""")
    columns={row[1] for row in db.execute("PRAGMA table_info(agent_media)")}
    if "duration_seconds" not in columns:
        db.execute("ALTER TABLE agent_media ADD COLUMN duration_seconds REAL")
    if "thumb_name" not in columns:
        db.execute("ALTER TABLE agent_media ADD COLUMN thumb_name TEXT")
    return db

class Video(BaseModel):
    media_id:str=Field(pattern=r"^[a-f0-9]{64}$")
    filename:str=Field(min_length=1,max_length=255)
    size_bytes:int=Field(ge=0)
    modified_ns:int=Field(ge=0)
    duration_seconds:float|None=Field(default=None,ge=0,le=7*24*3600)
    # Optional JPEG (<= 40 KB); sent once per new/changed video, see `thumbs_missing` in the reply.
    thumb_jpeg_base64:str|None=Field(default=None,max_length=56000)

class Inventory(BaseModel):
    agent_id:str
    videos:list[Video]=Field(max_length=10000)

def decode_thumb(data:str)->bytes:
    try:
        raw=base64.b64decode(data,validate=True)
    except (binascii.Error,ValueError):
        raise HTTPException(422,"Thumbnail không phải base64 hợp lệ")
    if len(raw)>THUMB_MAX_BYTES or not raw.startswith(b"\xff\xd8"):
        raise HTTPException(422,"Thumbnail phải là JPEG tối đa 40 KB")
    return raw

def _remove_thumb(name):
    if name:
        try:(THUMB_DIR/f"{name}.jpg").unlink()
        except OSError:pass

@router.post("/sync")
def sync(body:Inventory,x_sarae_agent_key:str|None=Header(None)):
    verify_agent(body.agent_id,x_sarae_agent_key)
    if len({x.media_id for x in body.videos})!=len(body.videos):
        raise HTTPException(422,"Trùng media_id")
    thumbs={x.media_id:decode_thumb(x.thumb_jpeg_base64) for x in body.videos if x.thumb_jpeg_base64}
    db=connection()
    try:
        with db:
            previous={r["media_id"]:dict(r) for r in db.execute(
                "SELECT media_id,modified_ns,thumb_name FROM agent_media WHERE agent_id=?",(body.agent_id,))}
            rows=[]
            for x in body.videos:
                old=previous.get(x.media_id) or {}
                thumb=old.get("thumb_name")
                # A changed file invalidates its thumbnail until the Agent sends a new one.
                if old and old["modified_ns"]!=x.modified_ns:
                    _remove_thumb(thumb);thumb=None
                if x.media_id in thumbs:
                    THUMB_DIR.mkdir(parents=True,exist_ok=True)
                    name=secrets.token_hex(16)
                    (THUMB_DIR/f"{name}.jpg").write_bytes(thumbs[x.media_id])
                    _remove_thumb(thumb);thumb=name
                rows.append((body.agent_id,x.media_id,Path(x.filename).name,x.size_bytes,x.modified_ns,
                             x.duration_seconds if x.duration_seconds is not None else None,thumb))
            db.execute("UPDATE agent_media SET available=0 WHERE agent_id=?",(body.agent_id,))
            db.executemany("""INSERT INTO agent_media(agent_id,media_id,filename,size_bytes,modified_ns,available,
               duration_seconds,thumb_name)
               VALUES(?,?,?,?,?,1,?,?) ON CONFLICT(agent_id,media_id) DO UPDATE SET
               filename=excluded.filename,size_bytes=excluded.size_bytes,
               modified_ns=excluded.modified_ns,available=1,
               duration_seconds=COALESCE(excluded.duration_seconds,agent_media.duration_seconds),
               thumb_name=excluded.thumb_name""",rows)
            missing=[r[0] for r in db.execute("""SELECT media_id FROM agent_media
               WHERE agent_id=? AND available=1 AND thumb_name IS NULL ORDER BY media_id LIMIT 500""",
               (body.agent_id,))]
    finally:
        db.close()
    return {"synced":len(body.videos),"thumbs_missing":missing}

@router.get("/thumbs/{name}")
def thumb(name:str,exp:int|None=None,sig:str|None=None):
    """Only through a URL signed for the owner of the Agent the thumbnail came from (see media_access)."""
    from app import media_access
    if len(name)!=32 or any(c not in "0123456789abcdef" for c in name):
        raise HTTPException(404,"Không tìm thấy ảnh")
    owner_id=media_access.thumb_owner(name)
    if owner_id is None:raise HTTPException(404,"Không tìm thấy ảnh")
    media_access.check("thumb",name,owner_id,exp,sig)
    path=THUMB_DIR/f"{name}.jpg"
    if not path.is_file():raise HTTPException(404,"Không tìm thấy ảnh")
    return FileResponse(path,media_type="image/jpeg",headers={"Cache-Control":"public, max-age=86400"})

@router.get("/{agent_id}")
def list_media(agent_id:str,authorization:str|None=Header(None)):
    with db_open() as registry:
        row=registry.execute("SELECT owner_id FROM registered_agents WHERE id=? AND enabled=1",(agent_id,)).fetchone()
    if row is None:raise HTTPException(404,"Không tìm thấy Agent")
    allow_owner(authorization,row["owner_id"])
    from app.media_access import thumb_url
    db=connection()
    try:
        rows=db.execute("""SELECT media_id,filename,size_bytes,modified_ns,available,duration_seconds,thumb_name
           FROM agent_media WHERE agent_id=? ORDER BY filename LIMIT 10000""",(agent_id,)).fetchall()
    finally:
        db.close()
    videos=[]
    for x in rows:
        item=dict(x);name=item.pop("thumb_name")
        item["thumb_url"]=thumb_url(name,row["owner_id"])
        videos.append(item)
    return {"agent_id":agent_id,"videos":videos}
