"""Media Library access for Windows Agents (X-Sarae-Agent-Key), read-only.

An Agent only receives media of the User who owns it; a session manifest also requires the session to belong to
that User and, once an Agent is assigned, to be assigned to THIS Agent. The Agent downloads bytes through the
Backend (Range supported for resume) and never receives storage credentials or provider URLs.
LIVE Session logic is not changed here (Phase 3 wires the cache to it).
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import storage_service
from app.identity import agent_error, verify_agent
from app.live_sessions import LiveSession, SessionProduct
from app.media import get_db
from app.storage_errors import StorageError, http_error
from app.storage_models import MediaItem, ProductMedia

log = logging.getLogger("sarae.agent_media")
DB = Annotated[Session, Depends(get_db)]
router = APIRouter(prefix="/api/agent-media", tags=["Agent media"])


def agent_owner(agent_id: str, key: str | None) -> str:
    return verify_agent(agent_id, key)["owner_id"]


def manifest_item(media: MediaItem, product_ids: list[int]) -> dict:
    return {"media_id": media.id, "filename": media.filename, "mime_type": media.mime_type,
            "size_bytes": media.size_bytes, "sha256": media.sha256, "md5": media.md5,
            # Changes whenever the stored object changes (re-upload / provider migration keeps the same bytes).
            "revision": media.sha256 or media.md5 or f"{media.provider}:{media.provider_file_id}",
            "product_ids": product_ids, "download_path": f"/api/agent-media/{media.id}/content"}


@router.get("/manifest")
def manifest(agent_id: str, session_id: int, db: DB, x_sarae_agent_key: str | None = Header(None)):
    """Every AVAILABLE video of the session's products (random playback may pick any of them)."""
    owner_id = agent_owner(agent_id, x_sarae_agent_key)
    session = db.get(LiveSession, session_id)
    if session is None or session.owner_id != owner_id or (session.agent_id and session.agent_id != agent_id):
        raise agent_error(404, "Không tìm thấy phiên LIVE của Agent này", "SESSION_NOT_FOUND")
    product_ids = list(db.scalars(select(SessionProduct.product_id).where(SessionProduct.session_id == session_id)
                                  .order_by(SessionProduct.sort_order)))
    rows = db.execute(select(ProductMedia.product_id, MediaItem)
                      .join(MediaItem, MediaItem.id == ProductMedia.media_id)
                      .where(ProductMedia.product_id.in_(product_ids), ProductMedia.owner_id == owner_id,
                             MediaItem.owner_id == owner_id, MediaItem.status == "AVAILABLE")
                      .order_by(ProductMedia.product_id, ProductMedia.sort_order)).all()
    items: dict[str, tuple[MediaItem, list[int]]] = {}
    for product_id, media in rows:
        items.setdefault(media.id, (media, []))[1].append(product_id)
    with_video = {pid for _, pids in items.values() for pid in pids}
    return {"session_id": session_id, "agent_id": agent_id,
            "items": [manifest_item(media, pids) for media, pids in items.values()],
            "total_bytes": sum(media.size_bytes for media, _ in items.values()),
            "products_without_video": [pid for pid in product_ids if pid not in with_video]}


@router.get("/{media_id}/content")
def content(media_id: str, agent_id: str, db: DB, x_sarae_agent_key: str | None = Header(None),
            range: str | None = Header(None)):
    owner_id = agent_owner(agent_id, x_sarae_agent_key)
    media = db.get(MediaItem, media_id)
    if media is None or media.owner_id != owner_id or media.status != "AVAILABLE":
        log.warning("agent %s denied media %s", agent_id, media_id)
        raise agent_error(404, "Không tìm thấy video", "MEDIA_NOT_FOUND")
    try:
        body = storage_service.open_media(db, media, range)
    except StorageError as exc:
        raise http_error(exc)
    headers = {**body.headers, "Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    if media.sha256:
        headers["X-Sarae-Sha256"] = media.sha256
    if media.md5:
        headers["X-Sarae-Md5"] = media.md5
    return StreamingResponse(body.iter(), status_code=body.status, headers=headers)
