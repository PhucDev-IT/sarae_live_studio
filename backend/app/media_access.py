"""Access control for private images loaded by <img> tags (which cannot send a Bearer token).

Product/presenter photos and Agent video thumbnails belong to one OWNER. They are served only through short-lived
signed URLs: the API that returns a photo (already authenticated and owner-scoped) signs its path with an HMAC over
(kind, name, owner, expiry). The image endpoint recomputes the signature with the image's REAL owner from the server's
own records, so a URL signed for one owner can never open another owner's image, and an expired or edited URL is 403.
Records are kept per image (owner_images) or come from the Agent library (thumbnail → Agent → owner, Agent still
enabled). The URL never reveals file paths on the Agent's PC.
"""
import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import DateTime, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.config import settings
from app.database import Base, DATA_DIR

URL_TTL_SECONDS = 3600
KEY_FILE = DATA_DIR / "url_signing.key"
# Server-relative path of an uploaded photo, the stable reference stored in products/presenters.
IMAGE_REF = re.compile(r"/api/(products|presenters)/images/([0-9a-f]{32}\.(?:jpg|png|webp|gif))")
KINDS = {"products": "product", "presenters": "presenter"}


class OwnerImage(Base):
    """Who owns an uploaded photo (kind: product / presenter)."""
    __tablename__ = "owner_images"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


_secret: bytes | None = None


def secret() -> bytes:
    """Signing key: SARAE_URL_SIGNING_SECRET when set (several servers share it), else one generated and kept on disk."""
    global _secret
    if _secret is None:
        configured = getattr(settings, "sarae_url_signing_secret", "") or ""
        if configured:
            _secret = configured.encode()
        else:
            try:
                _secret = KEY_FILE.read_bytes()
            except FileNotFoundError:
                _secret = secrets.token_bytes(32)
                KEY_FILE.write_bytes(_secret)
    return _secret


def _signature(kind: str, name: str, owner_id: str, expires: int) -> str:
    digest = hmac.new(secret(), f"{kind}\n{name}\n{owner_id}\n{expires}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:20]).decode().rstrip("=")


def signed(path: str, kind: str, name: str, owner_id: str, ttl: int = URL_TTL_SECONDS) -> str:
    expires = int(time.time()) + ttl
    return f"{path}?exp={expires}&sig={_signature(kind, name, owner_id, expires)}"


def check(kind: str, name: str, owner_id: str | None, exp: int | None, sig: str | None):
    """403 unless (exp, sig) is a valid, unexpired signature for this image and its real owner."""
    if owner_id is None or exp is None or not sig:
        raise HTTPException(403, "Liên kết ảnh không hợp lệ")
    if exp < time.time():
        raise HTTPException(403, "Liên kết ảnh đã hết hạn")
    if not hmac.compare_digest(_signature(kind, name, owner_id, exp), sig):
        raise HTTPException(403, "Liên kết ảnh không hợp lệ")


# ----- product / presenter photos ---------------------------------------------------------------

def image_name(value: str | None) -> tuple[str, str] | None:
    """(folder, name) when `value` points to one of our uploaded photos (any host, signed or not)."""
    if not value:
        return None
    match = IMAGE_REF.search(urlsplit(value).path)
    return (match.group(1), match.group(2)) if match else None


def stored_ref(db: Session, owner: dict, value: str, folder: str) -> str:
    """What to store for an image_url sent by the client: our photos become their stable path (after checking the
    caller owns them); external URLs are kept as given."""
    found = image_name(value)
    if found is None:
        return value
    if found[0] != folder or db.get(OwnerImage, found[1]) is None or db.get(OwnerImage, found[1]).owner_id != owner["id"]:
        raise HTTPException(422, "Ảnh không thuộc tài khoản này")
    return f"/api/{folder}/images/{found[1]}"


def public_url(value: str | None, owner_id: str) -> str:
    """The URL a page can load now: our photos are signed for their owner, external URLs are returned as they are."""
    found = image_name(value)
    if found is None:
        return value or ""
    folder, name = found
    return signed(f"/api/{folder}/images/{name}", KINDS[folder], name, owner_id)


def register(db: Session, owner: dict, name: str, folder: str):
    db.add(OwnerImage(name=name, owner_id=owner["id"], kind=KINDS[folder]))
    db.commit()


def image_owner(db: Session, name: str, folder: str) -> str | None:
    row = db.get(OwnerImage, name)
    return row.owner_id if row is not None and row.kind == KINDS[folder] else None


def release(db: Session, value: str | None, directory, still_used) -> bool:
    """Delete an uploaded photo once nothing references it any more. `still_used(ref)` checks the remaining rows."""
    found = image_name(value)
    if found is None:
        return False
    ref = f"/api/{found[0]}/images/{found[1]}"
    if still_used(ref):
        return False
    row = db.get(OwnerImage, found[1])
    if row is not None:
        db.delete(row)
        db.commit()
    (directory / found[1]).unlink(missing_ok=True)
    return True


# ----- Agent video thumbnails ------------------------------------------------------------------

def thumb_url(thumb_name: str | None, owner_id: str) -> str | None:
    return signed(f"/api/agent-library/thumbs/{thumb_name}", "thumb", thumb_name, owner_id) if thumb_name else None


def thumb_owner(thumb_name: str) -> str | None:
    """Owner of the Agent whose library has this thumbnail, if that Agent is still enabled."""
    from app.agent_library import connection
    from app.identity import db_open
    db = connection()
    try:
        row = db.execute("SELECT agent_id FROM agent_media WHERE thumb_name=? LIMIT 1", (thumb_name,)).fetchone()
    finally:
        db.close()
    if row is None:
        return None
    with db_open() as registry:
        agent = registry.execute("SELECT owner_id FROM registered_agents WHERE id=? AND enabled=1", (row["agent_id"],)).fetchone()
    return agent["owner_id"] if agent else None


def owned_image_names(db: Session, owner_id: str) -> set[str]:
    return set(db.scalars(select(OwnerImage.name).where(OwnerImage.owner_id == owner_id)).all())
