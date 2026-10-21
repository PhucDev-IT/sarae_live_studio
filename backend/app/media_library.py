"""Centralized Media Library API for Sarae Web (OWNER only).

- Every query is filtered by the caller's owner_id; another owner's media/product answers 404.
- Responses never contain provider file ids, folder ids, provider URLs or credentials.
- <video>/<img> cannot send a Bearer token: they get short-lived HMAC-signed URLs (app.media_access), checked
  against the media's REAL owner. Bytes are proxied by the Backend with HTTP Range so the player can seek.
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app import media_access, storage_service
from app.catalog import Presenter, Product
from app.identity import current_owner
from app.database import get_db
from app.ownership import Owner, owned_ids
from app.storage_errors import StorageError, fail, http_error
from app.storage_models import MediaItem, MediaItemPresenter, MediaThumbnail, MediaUpload, ProductMedia, aware, now

log = logging.getLogger("sarae.media_library")
DB = Annotated[Session, Depends(get_db)]
router = APIRouter(prefix="/api/media-library", tags=["Media Library"])
products_router = APIRouter(prefix="/api/products", tags=["Media Library"], dependencies=[Depends(current_owner)])
SIGN_KIND = "media"
MAX_PRODUCT_VIDEOS = 200
THUMB_MAX_BYTES = 512 * 1024
THUMB_TYPES = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG", "image/webp": b"RIFF"}


# ----- views ----------------------------------------------------------------------------------

def owned_media(db: Session, owner_id: str, media_id: str) -> MediaItem:
    media = db.get(MediaItem, media_id)
    if media is None or media.owner_id != owner_id:
        raise fail(404, "MEDIA_NOT_FOUND", "Không tìm thấy video")
    return media


def _links(db: Session, media_ids: list[str]) -> dict[str, dict]:
    """Products, presenters and cover version of each media, in three queries for the whole page."""
    result: dict[str, dict] = {mid: {"product_ids": [], "presenter_ids": [], "thumb": None} for mid in media_ids}
    if media_ids:
        for media_id, product_id in db.execute(select(ProductMedia.media_id, ProductMedia.product_id)
                                               .where(ProductMedia.media_id.in_(media_ids)).order_by(ProductMedia.product_id)):
            result[media_id]["product_ids"].append(product_id)
        for media_id, presenter_id in db.execute(select(MediaItemPresenter.media_id, MediaItemPresenter.presenter_id)
                                                 .where(MediaItemPresenter.media_id.in_(media_ids)).order_by(MediaItemPresenter.presenter_id)):
            result[media_id]["presenter_ids"].append(presenter_id)
        for media_id, updated in db.execute(select(MediaThumbnail.media_id, MediaThumbnail.updated_at)
                                            .where(MediaThumbnail.media_id.in_(media_ids))):
            result[media_id]["thumb"] = int(aware(updated).timestamp())
    return result


def _one(db: Session, media: MediaItem) -> dict:
    return media_view(media, _links(db, [media.id])[media.id])


def media_view(media: MediaItem, links: dict) -> dict:
    available = media.status == "AVAILABLE"
    thumb = links["thumb"]
    return {
        "id": media.id,
        "filename": media.filename,
        "display_name": media.display_name or media.filename,
        "notes": media.notes,
        "tags": media.tags,
        "mime_type": media.mime_type,
        "size_bytes": media.size_bytes,
        "duration_seconds": media.duration_seconds,
        "width": media.width,
        "height": media.height,
        "checksum_sha256": media.sha256,
        "status": media.status,
        "available": available,
        "product_ids": links["product_ids"],
        "presenter_ids": links["presenter_ids"],
        "has_thumbnail": thumb is not None,
        "stream_url": media_access.signed(f"/api/media-library/{media.id}/stream", SIGN_KIND, media.id, media.owner_id) if available else None,
        # ?v= changes when the cover changes, so browsers never show a stale cached image.
        "thumb_url": media_access.signed(f"/api/media-library/{media.id}/thumbnail", SIGN_KIND, media.id, media.owner_id) + f"&v={thumb}"
        if available and thumb is not None else None,
        "created_at": aware(media.created_at).isoformat(),
        "updated_at": aware(media.updated_at).isoformat(),
    }


def upload_view(row: MediaUpload) -> dict:
    return {"id": row.id, "client_upload_id": row.client_upload_id, "filename": row.filename, "size_bytes": row.size_bytes,
            "received_bytes": row.received_bytes, "status": row.status, "media_id": row.media_id if row.status == "COMPLETED" else None,
            "error_code": row.error_code, "chunk_size": storage_service.CHUNK_SIZE}


def product_video_ids(db: Session, owner_id: str, product_id: int) -> list[str]:
    """AVAILABLE Media Library videos of a product, in playback order (what LIVE plays for that product)."""
    return list(db.scalars(select(MediaItem.id).join(ProductMedia, ProductMedia.media_id == MediaItem.id).where(
        ProductMedia.product_id == product_id, ProductMedia.owner_id == owner_id, MediaItem.owner_id == owner_id,
        MediaItem.status == "AVAILABLE").order_by(ProductMedia.sort_order, ProductMedia.id)))


def product_video_counts(db: Session, owner_id: str, product_ids: list[int] | None = None) -> dict[int, int]:
    """Number of AVAILABLE Media Library videos per product (shared by every Agent of the owner)."""
    query = select(ProductMedia.product_id, func.count(MediaItem.id)).join(MediaItem, MediaItem.id == ProductMedia.media_id).where(
        ProductMedia.owner_id == owner_id, MediaItem.owner_id == owner_id, MediaItem.status == "AVAILABLE")
    if product_ids is not None:
        query = query.where(ProductMedia.product_id.in_(product_ids))
    return {pid: count for pid, count in db.execute(query.group_by(ProductMedia.product_id))}


# ----- uploads --------------------------------------------------------------------------------

class UploadCreate(BaseModel):
    client_upload_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)


@router.post("/uploads", status_code=201)
def create_upload(body: UploadCreate, owner: Owner, db: DB):
    try:
        row = storage_service.create_upload(db, owner["id"], body.client_upload_id, body.filename, body.size_bytes)
    except StorageError as exc:
        raise http_error(exc)
    return upload_view(row)


@router.get("/uploads/{upload_id}")
def get_upload(upload_id: str, owner: Owner, db: DB):
    row = storage_service.owned_upload(db, owner["id"], upload_id)
    try:
        row = storage_service.refresh_upload(db, row)
    except StorageError as exc:
        raise http_error(exc)
    return upload_view(row)


@router.put("/uploads/{upload_id}/chunk")
async def put_chunk(upload_id: str, request: Request, owner: Owner, db: DB,
                    content_range: str | None = Header(None)):
    """Raw bytes of one chunk, `Content-Range: bytes start-end/total`. Retrying the same chunk is safe."""
    row = storage_service.owned_upload(db, owner["id"], upload_id)
    start, end = storage_service.parse_content_range(content_range, row.size_bytes)
    data = bytearray()
    async for piece in request.stream():
        data += piece
        if len(data) > storage_service.MAX_CHUNK:
            raise fail(413, "UPLOAD_CHUNK_TOO_LARGE", "Phần upload quá lớn")
    try:
        row = await run_in_threadpool(storage_service.put_chunk, db, row, start, end, bytes(data))
    except StorageError as exc:
        raise http_error(exc)
    return upload_view(row)


@router.delete("/uploads/{upload_id}")
def cancel_upload(upload_id: str, owner: Owner, db: DB):
    row = storage_service.owned_upload(db, owner["id"], upload_id)
    if row.status == "COMPLETED":
        raise fail(409, "UPLOAD_ALREADY_COMPLETED", "Upload đã hoàn tất; hãy xóa video trong thư viện")
    return upload_view(storage_service.cancel_upload(db, row))


# ----- library ----------------------------------------------------------------------------------

@router.get("")
def list_media(owner: Owner, db: DB, q: str = "", product_id: int | None = None, presenter_id: int | None = None,
               status: str = "AVAILABLE",
               limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    query = select(MediaItem).where(MediaItem.owner_id == owner["id"])
    if status in ("AVAILABLE", "DELETED"):
        query = query.where(MediaItem.status == status)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(MediaItem.display_name.ilike(like), MediaItem.filename.ilike(like), MediaItem.tags.ilike(like)))
    if product_id is not None:
        query = query.where(MediaItem.id.in_(select(ProductMedia.media_id).where(
            ProductMedia.product_id == product_id, ProductMedia.owner_id == owner["id"])))
    if presenter_id is not None:
        query = query.where(MediaItem.id.in_(select(MediaItemPresenter.media_id).where(
            MediaItemPresenter.presenter_id == presenter_id, MediaItemPresenter.owner_id == owner["id"])))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(MediaItem.created_at.desc()).limit(limit).offset(offset)).all()
    links = _links(db, [m.id for m in items])
    return {"total": total, "items": [media_view(m, links[m.id]) for m in items]}


@router.get("/{media_id}")
def get_media(media_id: str, owner: Owner, db: DB):
    media = owned_media(db, owner["id"], media_id)
    if media.status == "AVAILABLE":
        try:
            storage_service.refresh_metadata(db, media)
        except StorageError as exc:
            log.info("metadata refresh skipped for %s: %s", media_id, exc.code)
    return _one(db, media)


class MediaPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    tags: str | None = Field(default=None, max_length=500)


@router.patch("/{media_id}")
def patch_media(media_id: str, body: MediaPatch, owner: Owner, db: DB):
    """Sarae-only metadata; the stored file and its name in the storage are not changed."""
    media = owned_media(db, owner["id"], media_id)
    for field in ("display_name", "notes", "tags"):
        value = getattr(body, field)
        if value is not None:
            setattr(media, field, value.strip())
    db.commit()
    return _one(db, media)


@router.delete("/{media_id}")
def delete_media(media_id: str, owner: Owner, db: DB):
    """Hide from the library (DELETED). The stored object and product history are kept for LIVE audit."""
    media = owned_media(db, owner["id"], media_id)
    if media.status != "DELETED":
        media.status, media.deleted_at = "DELETED", now()
        db.commit()
    return _one(db, media)


def _signed_media(db: Session, media_id: str, exp: int | None, sig: str | None) -> MediaItem:
    media = db.get(MediaItem, media_id)
    media_access.check(SIGN_KIND, media_id, media.owner_id if media else None, exp, sig)
    if media.status != "AVAILABLE":
        raise fail(410, "MEDIA_DELETED", "Video đã bị xóa khỏi thư viện")
    return media


@router.get("/{media_id}/stream", include_in_schema=False)
def stream(media_id: str, db: DB, exp: int | None = None, sig: str | None = None, range: str | None = Header(None)):
    media = _signed_media(db, media_id, exp, sig)
    try:
        body = storage_service.open_media(db, media, range)
    except StorageError as exc:
        raise http_error(exc)
    headers = {**body.headers, "Accept-Ranges": "bytes", "Cache-Control": "private, no-store",
               "X-Content-Type-Options": "nosniff"}
    headers.setdefault("content-type", media.mime_type or "video/mp4")
    return StreamingResponse(body.iter(), status_code=body.status, headers=headers)


@router.get("/{media_id}/thumbnail", include_in_schema=False)
def thumbnail(media_id: str, db: DB, exp: int | None = None, sig: str | None = None):
    media = _signed_media(db, media_id, exp, sig)
    cover = db.get(MediaThumbnail, media.id)
    if cover is not None:
        return Response(cover.content, media_type=cover.mime_type, headers={"Cache-Control": "private, max-age=86400"})
    from app.storage_providers import provider
    try:
        found = provider(media.provider, db).thumbnail(media.provider_file_id)
    except StorageError as exc:
        raise http_error(exc)
    if found is None:
        raise fail(404, "THUMBNAIL_NOT_READY", "Chưa có ảnh xem trước")
    return Response(found[0], media_type=found[1], headers={"Cache-Control": "private, max-age=3600"})


@router.put("/{media_id}/thumbnail")
async def put_thumbnail(media_id: str, request: Request, owner: Owner, db: DB,
                        duration_seconds: float | None = Query(None, gt=0, lt=86400),
                        width: int | None = Query(None, gt=0, le=10000), height: int | None = Query(None, gt=0, le=10000)):
    """Cover image captured by the browser (raw JPEG/PNG/WebP body). The browser also reports the video's duration and
    size it read locally; they only fill fields the storage provider has not reported yet."""
    media = owned_media(db, owner["id"], media_id)
    mime = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if mime not in THUMB_TYPES:
        raise fail(415, "THUMBNAIL_TYPE_NOT_ALLOWED", "Ảnh bìa phải là JPEG, PNG hoặc WebP")
    data = bytearray()
    async for piece in request.stream():
        data += piece
        if len(data) > THUMB_MAX_BYTES:
            raise fail(413, "THUMBNAIL_TOO_LARGE", "Ảnh bìa tối đa 512 KB")
    if not bytes(data).startswith(THUMB_TYPES[mime]):
        raise fail(422, "THUMBNAIL_CONTENT_INVALID", "Nội dung không phải ảnh hợp lệ")
    cover = db.get(MediaThumbnail, media.id)
    if cover is None:
        cover = MediaThumbnail(media_id=media.id, owner_id=owner["id"], mime_type=mime, content=bytes(data))
        db.add(cover)
    else:
        cover.mime_type, cover.content, cover.updated_at = mime, bytes(data), now()
    if media.duration_seconds is None and duration_seconds is not None:
        media.duration_seconds = round(duration_seconds, 3)
    if media.width is None and media.height is None and width and height:
        media.width, media.height = width, height
    db.commit()
    return _one(db, media)


class PresenterIds(BaseModel):
    presenter_ids: list[int] = Field(default_factory=list, max_length=200)


@router.put("/{media_id}/presenters")
def set_media_presenters(media_id: str, body: PresenterIds, owner: Owner, db: DB):
    """People appearing in this video (replaces the list)."""
    media = owned_media(db, owner["id"], media_id)
    wanted = list(dict.fromkeys(body.presenter_ids))
    if len(owned_ids(db, Presenter, wanted, owner)) != len(wanted):
        raise fail(422, "PRESENTER_NOT_FOUND", "Có người giới thiệu không thuộc tài khoản này")
    db.execute(delete(MediaItemPresenter).where(MediaItemPresenter.media_id == media.id))
    db.add_all(MediaItemPresenter(media_id=media.id, presenter_id=pid, owner_id=owner["id"]) for pid in wanted)
    db.commit()
    return _one(db, media)


# ----- Product ↔ Video (N:N) ---------------------------------------------------------------------

class ProductIds(BaseModel):
    product_ids: list[int] = Field(default_factory=list, max_length=500)


@router.put("/{media_id}/products")
def set_media_products(media_id: str, body: ProductIds, owner: Owner, db: DB):
    """Which products use this video. New links go to the end of each product's order; others keep theirs."""
    media = owned_media(db, owner["id"], media_id)
    wanted = list(dict.fromkeys(body.product_ids))
    if len(owned_ids(db, Product, wanted, owner)) != len(wanted):
        raise fail(422, "PRODUCT_NOT_FOUND", "Có sản phẩm không thuộc tài khoản này")
    current = set(db.scalars(select(ProductMedia.product_id).where(ProductMedia.media_id == media.id)))
    if set(wanted) - current and media.status != "AVAILABLE":
        raise fail(422, "MEDIA_NOT_AVAILABLE", "Video đã bị xóa, không thể gắn thêm sản phẩm")
    db.execute(delete(ProductMedia).where(ProductMedia.media_id == media.id, ProductMedia.product_id.not_in(wanted)))
    for product_id in wanted:
        if product_id not in current:
            last = db.scalar(select(func.max(ProductMedia.sort_order)).where(ProductMedia.product_id == product_id))
            db.add(ProductMedia(owner_id=owner["id"], product_id=product_id, media_id=media.id, sort_order=(last or 0) + 1))
    db.commit()
    return _one(db, media)


def _owned_product(db: Session, owner: dict, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None or product.owner_id != owner["id"]:
        raise fail(404, "PRODUCT_NOT_FOUND", "Không tìm thấy sản phẩm")
    return product


def product_videos_view(db: Session, product_id: int) -> dict:
    rows = db.execute(select(MediaItem).join(ProductMedia, ProductMedia.media_id == MediaItem.id)
                      .where(ProductMedia.product_id == product_id).order_by(ProductMedia.sort_order, ProductMedia.id)).scalars().all()
    links = _links(db, [m.id for m in rows])
    return {"product_id": product_id, "media_ids": [m.id for m in rows], "items": [media_view(m, links[m.id]) for m in rows],
            "available_count": sum(1 for m in rows if m.status == "AVAILABLE")}


@products_router.get("/{product_id}/videos")
def get_product_videos(product_id: int, owner: Owner, db: DB):
    _owned_product(db, owner, product_id)
    return product_videos_view(db, product_id)


class MediaIds(BaseModel):
    media_ids: list[str] = Field(default_factory=list, max_length=MAX_PRODUCT_VIDEOS)


@products_router.put("/{product_id}/videos")
def put_product_videos(product_id: int, body: MediaIds, owner: Owner, db: DB):
    """Replace the ordered video list of a product (array order = playback order)."""
    _owned_product(db, owner, product_id)
    if len(set(body.media_ids)) != len(body.media_ids):
        raise fail(422, "MEDIA_DUPLICATE", "Một video chỉ xuất hiện một lần trong sản phẩm")
    medias = {m.id: m for m in db.scalars(select(MediaItem).where(MediaItem.id.in_(body.media_ids),
                                                                   MediaItem.owner_id == owner["id"]))}
    if len(medias) != len(body.media_ids):
        raise fail(422, "MEDIA_NOT_FOUND", "Có video không thuộc tài khoản này")
    current = set(db.scalars(select(ProductMedia.media_id).where(ProductMedia.product_id == product_id)))
    if any(medias[mid].status != "AVAILABLE" and mid not in current for mid in body.media_ids):
        raise fail(422, "MEDIA_NOT_AVAILABLE", "Không thể gắn video đã bị xóa")
    db.execute(delete(ProductMedia).where(ProductMedia.product_id == product_id))
    db.add_all(ProductMedia(owner_id=owner["id"], product_id=product_id, media_id=mid, sort_order=i)
               for i, mid in enumerate(body.media_ids, 1))
    db.commit()
    return product_videos_view(db, product_id)
