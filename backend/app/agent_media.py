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
from app.database import get_db
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


def session_manifest(db: Session, owner_id: str, session_id: int) -> dict:
    """Every AVAILABLE Media Library video of the session's products (random playback may pick any of them).

    A Single Video Timeline session needs its own videos only."""
    from app.live_timeline import is_single, session_videos
    session = db.get(LiveSession, session_id)
    if session is not None and is_single(session):
        found = [db.get(MediaItem, media_id) for media_id in session_videos(session)]
        ok = [m for m in found if m is not None and m.owner_id == owner_id and m.status == "AVAILABLE"]
        return {"session_id": session_id, "product_ids": [], "items": [manifest_item(m, []) for m in ok],
                "total_bytes": sum(m.size_bytes for m in ok), "products_without_video": []}
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
    return {"session_id": session_id, "product_ids": product_ids,
            "items": [manifest_item(media, pids) for media, pids in items.values()],
            "total_bytes": sum(media.size_bytes for media, _ in items.values()),
            "products_without_video": [pid for pid in product_ids if pid not in with_video]}


@router.get("/manifest")
def manifest(agent_id: str, session_id: int, db: DB, x_sarae_agent_key: str | None = Header(None)):
    """What the Agent must download into its cache before this session can play."""
    owner_id = agent_owner(agent_id, x_sarae_agent_key)
    session = db.get(LiveSession, session_id)
    if session is None or session.owner_id != owner_id or (session.agent_id and session.agent_id != agent_id):
        raise agent_error(404, "Không tìm thấy phiên LIVE của Agent này", "SESSION_NOT_FOUND")
    body = session_manifest(db, owner_id, session_id)
    body.pop("product_ids")
    return {"session_id": session_id, "agent_id": agent_id, **body}


@router.get("/overlay-snapshot")
def overlay_snapshot(agent_id: str, session_id: int, db: DB, x_sarae_agent_key: str | None = Header(None),
                     timeline_id: int | None = None):
    """Overlays of the session's ACTIVE timeline version, for the Agent's local OBS renderer (`snapshot` null when no
    version is active or the session is not a Single Video one). Images are signed URLs the Agent downloads once.
    With `timeline_id`: that version of the session whatever its status (the editor's "Xem thử trên OBS")."""
    from app.live_overlays import snapshot
    from app.live_timeline import LiveTimeline, is_single
    owner_id = agent_owner(agent_id, x_sarae_agent_key)
    session = db.get(LiveSession, session_id)
    if session is None or session.owner_id != owner_id or (session.agent_id and session.agent_id != agent_id):
        raise agent_error(404, "Không tìm thấy phiên LIVE của Agent này", "SESSION_NOT_FOUND")
    if timeline_id is not None:
        timeline = db.get(LiveTimeline, timeline_id)
        if timeline is None or timeline.session_id != session_id or not is_single(session):
            raise agent_error(404, "Không tìm thấy phiên bản timeline của phiên này", "TIMELINE_NOT_FOUND")
        return {"session_id": session_id, "agent_id": agent_id, "snapshot": snapshot(db, session, timeline)}
    timeline = db.get(LiveTimeline, session.active_timeline_id) if session.active_timeline_id else None
    active = is_single(session) and timeline is not None and timeline.status == "ACTIVE"
    return {"session_id": session_id, "agent_id": agent_id, "snapshot": snapshot(db, session, timeline) if active else None}


def agent_cache_report(agent_id: str) -> tuple[dict, str | None]:
    """The Agent's last reported media cache job (heartbeat) and when it was seen."""
    import json
    from app.cloud_agents import _db
    with _db() as db:
        row = db.execute("SELECT last_seen,status_json FROM agent_status WHERE agent_id=?", (agent_id,)).fetchone()
    if row is None:
        return {}, None
    report = (json.loads(row["status_json"] or "{}") or {}).get("media_cache") or {}
    return (report if isinstance(report, dict) else {}), row["last_seen"]


def media_readiness(db: Session, owner_id: str, agent_id: str, session_id: int) -> dict:
    """Whether `agent_id` holds a verified copy of EVERY video of the session (same revision as the Cloud).

    state: NO_VIDEO (nothing to download) / NOT_STARTED / DOWNLOADING / FAILED / INCOMPLETE / READY.
    Only the Agent's own report for this session counts; a report for another session or an older revision of a
    video never makes the session ready.
    """
    manifest = session_manifest(db, owner_id, session_id)
    required = {item["media_id"]: item for item in manifest["items"]}
    report, last_seen = agent_cache_report(agent_id)
    if report.get("session_id") != session_id:
        report = {}
    cached = {r.get("media_id"): r.get("revision") for r in report.get("ready") or [] if isinstance(r, dict)}
    ok = [mid for mid, item in required.items() if cached.get(mid) == item["revision"]]
    failed = [f for f in report.get("failed") or [] if isinstance(f, dict)]
    if not required:
        state = "NO_VIDEO"
    elif len(ok) == len(required):
        state = "READY"
    elif report.get("state") == "PREPARING":
        state = "DOWNLOADING"
    elif report.get("state") == "FAILED" or failed:
        state = "FAILED"
    elif report:
        state = "INCOMPLETE"  # finished, but the videos changed since (re-linked / new video): download again
    else:
        state = "NOT_STARTED"
    names = {item["media_id"]: item["filename"] for item in manifest["items"]}
    products = []
    for pid in manifest["product_ids"]:
        ids = [mid for mid, item in required.items() if pid in item["product_ids"]]
        products.append({"product_id": pid, "total": len(ids), "ready": sum(1 for mid in ids if mid in ok)})
    return {"session_id": session_id, "agent_id": agent_id, "state": state, "ready": state == "READY",
            "total": len(required), "ready_count": len(ok),
            "bytes_total": manifest["total_bytes"],
            "bytes_done": manifest["total_bytes"] if state == "READY" else min(int(report.get("bytes_done") or 0), manifest["total_bytes"]),
            "missing": [mid for mid in required if mid not in ok],
            "failed": [{"media_id": f.get("media_id"), "filename": names.get(f.get("media_id")), "error": f.get("error")} for f in failed][:20],
            "products": products, "products_without_video": manifest["products_without_video"],
            "reported_at": last_seen}


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
