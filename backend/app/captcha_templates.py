"""CAPTCHA reference images for the Windows Agents' monitor, managed by the platform operator (SUPER_ADMIN).

Detection is part of the service: SUPER_ADMIN uploads and removes templates here and every Agent mirrors the full
set (GET /api/captcha-templates/agent, then each image). OWNERs have no access and the set is global, not per OWNER.
The Cloud checks only the image signature and size (no OpenCV on the VPS); the Agent decodes and validates again.

    SUPER_ADMIN (Bearer): GET/POST /api/captcha-templates, DELETE /{id}, GET /{id}/image (preview)
    Agent (X-Sarae-Agent-Key): GET /api/captcha-templates/agent?agent_id=, GET /agent/{id}/image?agent_id=
"""
import base64
import binascii
import hashlib
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.database import DATA_DIR
from app.identity import require_admin, verify_agent

router = APIRouter(prefix="/api/captcha-templates", tags=["CAPTCHA templates"])
DB = DATA_DIR / "captcha_templates.sqlite3"
FILES = DATA_DIR / "captcha_templates"
MAX_BYTES = 3_000_000
MAX_TEMPLATES = 50
SIGNATURES = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpg"}
ID = re.compile(r"^[0-9a-f]{32}$")


@contextmanager
def _db():
    db = sqlite3.connect(DB, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS captcha_templates (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, bytes INTEGER NOT NULL,
            kind TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL)""")
        with db:
            yield db
    finally:
        db.close()


def _view(row) -> dict:
    return {"id": row["id"], "name": row["name"], "sha256": row["sha256"], "bytes": row["bytes"],
            "created_at": row["created_at"]}


def _list() -> list[dict]:
    with _db() as db:
        return [_view(r) for r in db.execute("SELECT * FROM captcha_templates ORDER BY created_at")]


def _file(template_id: str):
    if not ID.fullmatch(template_id):
        raise HTTPException(404, "Không tìm thấy ảnh mẫu")
    with _db() as db:
        row = db.execute("SELECT id,kind FROM captcha_templates WHERE id=?", (template_id,)).fetchone()
    path = FILES / f"{template_id}.{row['kind']}" if row else None
    if path is None or not path.is_file():
        raise HTTPException(404, "Không tìm thấy ảnh mẫu")
    return FileResponse(path, media_type="image/png" if row["kind"] == "png" else "image/jpeg",
                        headers={"Cache-Control": "no-store"})


class Upload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    image_base64: str = Field(min_length=20, max_length=4_100_000)


# ----- SUPER_ADMIN ---------------------------------------------------------------------------------------

@router.get("")
def admin_list(authorization: str | None = Header(None)):
    require_admin(authorization)
    return {"templates": _list()}


@router.post("")
def admin_upload(body: Upload, authorization: str | None = Header(None)):
    admin = require_admin(authorization)
    try:
        raw = base64.b64decode(body.image_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "Ảnh không phải base64 hợp lệ")
    kind = next((k for sig, k in SIGNATURES.items() if raw.startswith(sig)), None)
    if kind is None or len(raw) > MAX_BYTES:
        raise HTTPException(422, "Ảnh mẫu phải là PNG hoặc JPEG, tối đa 3 MB")
    digest = hashlib.sha256(raw).hexdigest()
    template_id = uuid.uuid4().hex
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM captcha_templates WHERE sha256=?", (digest,)).fetchone():
            raise HTTPException(409, "Ảnh mẫu này đã có")
        if db.execute("SELECT COUNT(*) FROM captcha_templates").fetchone()[0] >= MAX_TEMPLATES:
            raise HTTPException(409, f"Tối đa {MAX_TEMPLATES} ảnh mẫu")
        db.execute("INSERT INTO captcha_templates VALUES(?,?,?,?,?,?,?)",
                   (template_id, body.name.strip(), digest, len(raw), kind,
                    datetime.now(timezone.utc).isoformat(), admin["id"]))
        # Written inside the transaction: a failed write rolls the row back.
        FILES.mkdir(parents=True, exist_ok=True)
        (FILES / f"{template_id}.{kind}").write_bytes(raw)
        row = db.execute("SELECT * FROM captcha_templates WHERE id=?", (template_id,)).fetchone()
    return _view(row)


@router.delete("/{template_id}")
def admin_delete(template_id: str, authorization: str | None = Header(None)):
    require_admin(authorization)
    if not ID.fullmatch(template_id):
        raise HTTPException(404, "Không tìm thấy ảnh mẫu")
    with _db() as db:
        row = db.execute("SELECT kind FROM captcha_templates WHERE id=?", (template_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Không tìm thấy ảnh mẫu")
        db.execute("DELETE FROM captcha_templates WHERE id=?", (template_id,))
    try:
        (FILES / f"{template_id}.{row['kind']}").unlink()
    except OSError:
        pass
    return {"deleted": template_id}


@router.get("/{template_id}/image")
def admin_image(template_id: str, authorization: str | None = Header(None)):
    require_admin(authorization)
    return _file(template_id)


# ----- Windows Agent -------------------------------------------------------------------------------------

@router.get("/agent")
def agent_list(agent_id: str = Query(min_length=1, max_length=80), x_sarae_agent_key: str | None = Header(None)):
    """The full current set: the Agent keeps exactly these templates and removes any other."""
    verify_agent(agent_id, x_sarae_agent_key)
    return {"templates": [{k: t[k] for k in ("id", "sha256", "bytes")} for t in _list()]}


@router.get("/agent/{template_id}/image")
def agent_image(template_id: str, agent_id: str = Query(min_length=1, max_length=80),
                x_sarae_agent_key: str | None = Header(None)):
    verify_agent(agent_id, x_sarae_agent_key)
    return _file(template_id)
