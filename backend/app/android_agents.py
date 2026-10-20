"""Owner-managed Android Agent video assets and Android-only command publishing."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.database import DATA_DIR
from app.identity import verify_agent, agent_platform
from app.owner_agents import owned_agent
from app.ownership import Owner

router = APIRouter(prefix="/api/android-agents", tags=["Android Agents"])
DB = DATA_DIR / "android_agent_media.sqlite3"
ASSET_DIR = DATA_DIR / "android_agent_media"
MAX_VIDEO_BYTES = 1_500 * 1024 * 1024


def connection():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS android_agent_media (
        agent_id TEXT NOT NULL, owner_id TEXT NOT NULL, media_id TEXT NOT NULL,
        filename TEXT NOT NULL, size_bytes INTEGER NOT NULL, modified_ns INTEGER NOT NULL,
        PRIMARY KEY(agent_id, media_id))""")
    return db


def _owner_android(owner: Owner, agent_id: str) -> dict:
    agent = owned_agent(owner, agent_id)
    if agent.get("platform") != "ANDROID":
        raise HTTPException(409, "Agent này không phải Android Agent")
    return agent


def _device_android(agent_id: str, credential: str | None) -> dict:
    agent = verify_agent(agent_id, credential)
    if agent_platform(agent_id) != "ANDROID":
        raise HTTPException(403, "ANDROID_AGENT_REQUIRED")
    return agent


def _asset_path(agent_id: str, media_id: str) -> Path:
    return ASSET_DIR / agent_id / f"{media_id}.mp4"


@router.get("/{agent_id}/videos")
def list_videos(agent_id: str, owner: Owner):
    _owner_android(owner, agent_id)
    with connection() as db:
        rows = db.execute("""SELECT media_id,filename,size_bytes,modified_ns FROM android_agent_media
                             WHERE agent_id=? AND owner_id=? ORDER BY filename COLLATE NOCASE""",
                          (agent_id, owner["id"])).fetchall()
    return {"videos": [dict(row) for row in rows]}


@router.post("/{agent_id}/videos", status_code=201)
def upload_video(agent_id: str, owner: Owner, video: UploadFile = File(...)):
    _owner_android(owner, agent_id)
    filename = Path(video.filename or "video.mp4").name[:255]
    if not filename.lower().endswith(".mp4"):
        raise HTTPException(415, "Chỉ nhận MP4")
    directory = ASSET_DIR / agent_id
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="upload-", suffix=".tmp", dir=directory)
    digest = hashlib.sha256()
    size = 0
    first = b""
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := video.file.read(1024 * 1024):
                if not first:
                    first = chunk[:32]
                size += len(chunk)
                if size > MAX_VIDEO_BYTES:
                    raise HTTPException(413, "MP4 vượt giới hạn 1.5 GB")
                digest.update(chunk)
                out.write(chunk)
        if size < 16 or b"ftyp" not in first:
            raise HTTPException(415, "Nội dung không có cấu trúc MP4 hợp lệ")
        media_id = digest.hexdigest()
        target = _asset_path(agent_id, media_id)
        os.replace(temporary, target)
        modified_ns = target.stat().st_mtime_ns
        with connection() as db:
            db.execute("""INSERT INTO android_agent_media(agent_id,owner_id,media_id,filename,size_bytes,modified_ns)
                VALUES(?,?,?,?,?,?) ON CONFLICT(agent_id,media_id) DO UPDATE SET
                owner_id=excluded.owner_id,filename=excluded.filename,size_bytes=excluded.size_bytes,
                modified_ns=excluded.modified_ns""",
                       (agent_id, owner["id"], media_id, filename, size, modified_ns))
        return {"media_id": media_id, "filename": filename, "size_bytes": size, "modified_ns": modified_ns}
    finally:
        video.file.close()
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@router.delete("/{agent_id}/videos/{media_id}")
def delete_video(agent_id: str, media_id: str, owner: Owner):
    _owner_android(owner, agent_id)
    with connection() as db:
        deleted = db.execute("DELETE FROM android_agent_media WHERE agent_id=? AND owner_id=? AND media_id=?",
                             (agent_id, owner["id"], media_id)).rowcount
    if not deleted:
        raise HTTPException(404, "Không tìm thấy video")
    try:
        _asset_path(agent_id, media_id).unlink()
    except FileNotFoundError:
        pass
    return {"deleted": media_id}


@router.get("/{agent_id}/sync")
def sync_catalog(agent_id: str, x_sarae_agent_key: str | None = Header(None)):
    _device_android(agent_id, x_sarae_agent_key)
    with connection() as db:
        rows = db.execute("""SELECT media_id,filename,size_bytes,modified_ns FROM android_agent_media
                             WHERE agent_id=? ORDER BY filename COLLATE NOCASE""", (agent_id,)).fetchall()
    return {"agent_id": agent_id, "videos": [dict(row) for row in rows]}


@router.get("/{agent_id}/videos/{media_id}/content")
def download_video(agent_id: str, media_id: str, x_sarae_agent_key: str | None = Header(None)):
    _device_android(agent_id, x_sarae_agent_key)
    with connection() as db:
        row = db.execute("SELECT filename FROM android_agent_media WHERE agent_id=? AND media_id=?",
                         (agent_id, media_id)).fetchone()
    path = _asset_path(agent_id, media_id)
    if row is None or not path.is_file():
        raise HTTPException(404, "Không tìm thấy MP4")
    return FileResponse(path, media_type="video/mp4", filename=row["filename"],
                        headers={"Cache-Control": "private, no-store"})
