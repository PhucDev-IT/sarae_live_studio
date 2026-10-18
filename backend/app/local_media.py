"""Register MP4 files from local directories without copying.

Paths are interpreted on the FastAPI host, which must be the OBS machine.
This endpoint is for trusted localhost development only.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.identity import current_owner
from app.ownership import Owner
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.database import SessionLocal
from app.media import Media, probe

router = APIRouter(prefix="/api/media/local", tags=["Media / Local folder"], dependencies=[Depends(current_owner)])


class FolderRequest(BaseModel):
    folder: str = Field(min_length=1, max_length=4096)
    recursive: bool = False


def resolve_media_path(item: Media) -> Path:
    from app.media import MEDIA_DIR
    return Path(item.local_path) if item.local_path else MEDIA_DIR / item.stored_name


@router.post("/scan")
def scan_folder(body: FolderRequest, owner: Owner):
    # On a shared server this would let any OWNER index and stream the host's files.
    if settings.sarae_saas_mode:
        raise HTTPException(403, "Quét thư mục trên máy chủ bị tắt ở chế độ SaaS; video được quét trên Windows Agent")
    folder = Path(body.folder).expanduser().resolve()
    if not folder.is_dir():
        raise HTTPException(422, "Thư mục không tồn tại trên máy chạy Backend")
    # Deliberately avoid scanning root or huge directory trees unintentionally.
    if folder == Path(folder.anchor):
        raise HTTPException(422, "Hãy chọn thư mục video cụ thể, không quét ổ đĩa gốc")
    results = []
    try:
        candidates = folder.rglob("*") if body.recursive else folder.iterdir()
        for path in candidates:
            if path.is_file() and path.suffix.lower() == ".mp4":
                results.append(path)
                if len(results) > 2000:
                    raise HTTPException(422, "Thư mục có hơn 2000 video; hãy chia nhỏ")
    except OSError as exc:
        raise HTTPException(422, f"Không đọc được thư mục: {exc}") from exc
    created = 0
    existing = 0
    with SessionLocal() as db:
        indexed = set(db.scalars(select(Media.local_path).where(Media.local_path.is_not(None), Media.owner_id == owner["id"])).all())
        for path in results:
            canonical = str(path.resolve())
            if canonical in indexed:
                existing += 1
                continue
            try:
                stat = path.stat()
                if stat.st_size == 0:
                    continue
                properties = probe(path)
                item = Media(
                    filename=path.name[:255], stored_name="local-" + __import__("uuid").uuid4().hex + ".mp4",
                    local_path=canonical, size_bytes=stat.st_size, owner_id=owner["id"], **properties
                )
                db.add(item)
                indexed.add(canonical)
                created += 1
            except OSError:
                continue
        db.commit()
    return {"folder": str(folder), "found": len(results), "created": created, "existing": existing}
