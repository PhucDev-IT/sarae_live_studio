"""Local media ingestion, metadata and catalog relationships."""
import json
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.catalog import Product, Presenter
from app.database import Base, DATA_DIR, SessionLocal

router = APIRouter(prefix="/api/media", tags=["Media"])
MEDIA_DIR = DATA_DIR / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


class Media(Base):
    __tablename__ = "media"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(nullable=True)
    video_codec: Mapped[str] = mapped_column(String(40), default="")
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    presenter_id: Mapped[int | None] = mapped_column(ForeignKey("presenters.id", ondelete="SET NULL"), nullable=True)
    tags: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MediaUpdate(BaseModel):
    product_id: int | None = None
    presenter_id: int | None = None
    tags: str = Field(default="", max_length=1000)


class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filename: str
    size_bytes: int
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str
    product_id: int | None
    presenter_id: int | None
    tags: str
    created_at: datetime


def get_db():
    with SessionLocal() as db:
        yield db


DB = Annotated[Session, Depends(get_db)]


def media_or_404(db: Session, media_id: int) -> Media:
    media = db.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Không tìm thấy video")
    return media


def validate_links(db: Session, product_id: int | None, presenter_id: int | None):
    if product_id is not None and db.get(Product, product_id) is None:
        raise HTTPException(422, "Sản phẩm không tồn tại")
    if presenter_id is not None and db.get(Presenter, presenter_id) is None:
        raise HTTPException(422, "Người giới thiệu không tồn tại")


def probe(path: Path) -> dict:
    if shutil.which("ffprobe") is None:
        return {}
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=True
        )
        data = json.loads(result.stdout)
        video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        duration = data.get("format", {}).get("duration")
        rate = video.get("avg_frame_rate", "0/0")
        numerator, denominator = (rate.split("/") + ["1"])[:2] if "/" in rate else (rate, "1")
        return dict(
            duration_seconds=float(duration) if duration is not None else None,
            width=video.get("width"), height=video.get("height"),
            fps=float(numerator) / float(denominator) if float(denominator) else None,
            video_codec=str(video.get("codec_name", ""))[:40]
        )
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}


@router.get("", response_model=list[MediaOut])
def list_media(db: DB, q: str = "", limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    stmt = select(Media)
    if q.strip():
        stmt = stmt.where(Media.filename.ilike(f"%{q.strip()}%"))
    return db.scalars(stmt.order_by(Media.id.desc()).offset(offset).limit(limit)).all()


@router.post("/upload", response_model=MediaOut, status_code=201)
def upload_media(
    db: DB, file: UploadFile = File(...),
    product_id: int | None = Form(None), presenter_id: int | None = Form(None),
    tags: str = Form("")
):
    validate_links(db, product_id, presenter_id)
    name = Path((file.filename or "").replace("\\", "/")).name
    if not name.lower().endswith(".mp4"):
        raise HTTPException(415, "Hiện chỉ hỗ trợ file MP4")
    if len(tags) > 1000:
        raise HTTPException(422, "Tags vượt quá giới hạn")
    stored_name = f"{uuid.uuid4().hex}.mp4"
    path = MEDIA_DIR / stored_name
    size = 0
    try:
        with path.open("xb") as out:
            while chunk := file.file.read(CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "File vượt quá 500 MB")
                out.write(chunk)
        if size == 0:
            raise HTTPException(422, "File rỗng")
        properties = probe(path)
        # When ffprobe is present, reject inputs that do not contain a video stream.
        if shutil.which("ffprobe") and not properties.get("video_codec"):
            raise HTTPException(422, "MP4 không chứa video hợp lệ")
        item = Media(filename=name[:255], stored_name=stored_name, size_bytes=size,
                     product_id=product_id, presenter_id=presenter_id, tags=tags, **properties)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()


@router.get("/{media_id}", response_model=MediaOut)
def get_media(media_id: int, db: DB):
    return media_or_404(db, media_id)


@router.get("/{media_id}/file")
def stream_media(media_id: int, db: DB):
    media = media_or_404(db, media_id)
    path = MEDIA_DIR / media.stored_name
    if not path.is_file():
        raise HTTPException(404, "File video không còn trên ổ đĩa")
    return FileResponse(path, media_type="video/mp4", filename=media.filename, content_disposition_type="inline")


@router.patch("/{media_id}", response_model=MediaOut)
def update_media(media_id: int, body: MediaUpdate, db: DB):
    item = media_or_404(db, media_id)
    validate_links(db, body.product_id, body.presenter_id)
    for key, value in body.model_dump().items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{media_id}", status_code=204)
def delete_media(media_id: int, db: DB):
    item = media_or_404(db, media_id)
    path = MEDIA_DIR / item.stored_name
    db.delete(item)
    db.commit()
    path.unlink(missing_ok=True)
