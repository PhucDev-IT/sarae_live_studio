"""An OWNER's video libraries: the MP4 files on each of their Windows Agents (every call names the Agent).

The Agent syncs metadata (and a small thumbnail) to `agent_library`; video bytes never leave the PC. The server keeps
what the OWNER adds on top — tags and links to products / presenters — keyed by (agent_id, media_id), where media_id is
the Agent's 64-hex id of the file.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import String, Text, delete, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.catalog import Presenter, Product
from app.database import Base, SessionLocal
from app.identity import current_owner
from app.media_access import thumb_url
from app.owner_agents import AgentId, owned_agent
from app.ownership import Owner, owned_ids

router = APIRouter(prefix="/api/media", tags=["Media"], dependencies=[Depends(current_owner)])


class VideoTag(Base):
    __tablename__ = "video_tags"
    agent_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    media_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    tags: Mapped[str] = mapped_column(Text, default="")


class MediaUpdate(BaseModel):
    tags: str = Field(default="", max_length=1000)


class MediaProducts(BaseModel):
    product_ids: list[int] = Field(default_factory=list, max_length=500)


class MediaPresenters(BaseModel):
    presenter_ids: list[int] = Field(default_factory=list, max_length=200)


def get_db():
    with SessionLocal() as db:
        yield db


DB = Annotated[Session, Depends(get_db)]


def library(agent_id: str) -> dict[str, dict]:
    """Videos the Agent reported, by media_id (unavailable ones included, flagged)."""
    from app.agent_library import connection
    db = connection()
    try:
        rows = db.execute("""SELECT media_id,filename,size_bytes,modified_ns,available,duration_seconds,thumb_name
            FROM agent_media WHERE agent_id=? ORDER BY filename LIMIT 10000""", (agent_id,)).fetchall()
    finally:
        db.close()
    return {r["media_id"]: dict(r) for r in rows}


def video_or_404(owner: dict, agent_id: str, media_id: str) -> tuple[dict, dict]:
    """A video of one of the owner's Agents; ids from another Agent (even the owner's) are a 404."""
    agent = owned_agent(owner, agent_id)
    row = library(agent["id"]).get(media_id)
    if row is None:
        raise HTTPException(404, "Không tìm thấy video trên Windows Agent này")
    return agent, row


def product_media(db: Session, owner_id: str, agent_id: str, product_id: int, available_only: bool = True) -> list[str]:
    """Ordered LIVE videos of a product on one Agent (those still present on the PC by default)."""
    from app.product_videos import ProductLiveVideo
    ids = list(db.scalars(select(ProductLiveVideo.media_id).where(
        ProductLiveVideo.owner_id == owner_id, ProductLiveVideo.agent_id == agent_id,
        ProductLiveVideo.product_id == product_id).order_by(ProductLiveVideo.sort_order)).all())
    if not available_only:
        return ids
    present = {mid for mid, row in library(agent_id).items() if row["available"]}
    return [mid for mid in ids if mid in present]


def output(db: Session, owner_id: str, agent_id: str, row: dict, tags: str | None = None) -> dict:
    from app.media_presenters import MediaPresenter
    from app.product_videos import ProductLiveVideo
    if tags is None:
        tag = db.get(VideoTag, (agent_id, row["media_id"]))
        tags = tag.tags if tag and tag.owner_id == owner_id else ""

    def linked(model, column):
        return sorted(db.scalars(select(column).where(model.owner_id == owner_id, model.agent_id == agent_id,
                                                      model.media_id == row["media_id"])).all())
    return {"id": row["media_id"], "agent_id": agent_id, "filename": row["filename"], "size_bytes": row["size_bytes"],
            "duration_seconds": row["duration_seconds"], "available": bool(row["available"]),
            "thumb_url": thumb_url(row["thumb_name"], owner_id),
            "tags": tags, "product_ids": linked(ProductLiveVideo, ProductLiveVideo.product_id),
            "presenter_ids": linked(MediaPresenter, MediaPresenter.presenter_id)}


@router.get("")
def list_media(db: DB, owner: Owner, agent_id: AgentId, q: str = ""):
    agent = owned_agent(owner, agent_id)
    tags = {t.media_id: t.tags for t in db.scalars(select(VideoTag).where(
        VideoTag.owner_id == owner["id"], VideoTag.agent_id == agent["id"])).all()}
    term = q.strip().lower()
    return [output(db, owner["id"], agent["id"], row, tags.get(mid, "")) for mid, row in library(agent["id"]).items()
            if not term or term in row["filename"].lower() or term in tags.get(mid, "").lower()]


@router.post("/rescan")
def rescan(owner: Owner, agent_id: AgentId):
    """Ask the Agent to rescan its media folders and sync now (otherwise it syncs about once a minute)."""
    from app.cloud_agents import rpc
    agent = owned_agent(owner, agent_id, online=True)
    return rpc(agent["id"], owner["id"], "media_rescan", timeout=90)


@router.get("/{media_id}")
def get_media(media_id: str, db: DB, owner: Owner, agent_id: AgentId):
    agent, row = video_or_404(owner, agent_id, media_id)
    return output(db, owner["id"], agent["id"], row)


@router.patch("/{media_id}")
def update_media(media_id: str, body: MediaUpdate, db: DB, owner: Owner, agent_id: AgentId):
    agent, row = video_or_404(owner, agent_id, media_id)
    tag = db.get(VideoTag, (agent["id"], media_id))
    if tag is None:
        tag = VideoTag(agent_id=agent["id"], media_id=media_id, owner_id=owner["id"])
        db.add(tag)
    tag.tags = body.tags
    db.commit()
    return output(db, owner["id"], agent["id"], row)


@router.put("/{media_id}/products")
def set_media_products(media_id: str, body: MediaProducts, db: DB, owner: Owner, agent_id: AgentId):
    """Replace the products whose LIVE playlist contains this video, in one transaction.

    Newly linked products get the video appended to the end of their playlist; the order of
    existing entries is kept. Reordering within a product stays on PUT /api/product-videos/{id}.
    """
    from app.product_videos import ProductLiveVideo
    agent, row = video_or_404(owner, agent_id, media_id)
    wanted = list(dict.fromkeys(body.product_ids))
    if owned_ids(db, Product, wanted, owner) != set(wanted):
        raise HTTPException(422, "Danh sách chứa sản phẩm không tồn tại")
    scope = (ProductLiveVideo.owner_id == owner["id"], ProductLiveVideo.agent_id == agent["id"])
    current = set(db.scalars(select(ProductLiveVideo.product_id).where(*scope, ProductLiveVideo.media_id == media_id)).all())
    db.execute(delete(ProductLiveVideo).where(*scope, ProductLiveVideo.media_id == media_id,
                                              ProductLiveVideo.product_id.not_in(wanted)))
    for product_id in wanted:
        if product_id in current:
            continue
        last = db.scalar(select(func.max(ProductLiveVideo.sort_order)).where(*scope, ProductLiveVideo.product_id == product_id))
        db.add(ProductLiveVideo(owner_id=owner["id"], agent_id=agent["id"], product_id=product_id, media_id=media_id,
                                sort_order=0 if last is None else last + 1))
    db.commit()
    return output(db, owner["id"], agent["id"], row)


@router.put("/{media_id}/presenters")
def set_media_presenters(media_id: str, body: MediaPresenters, db: DB, owner: Owner, agent_id: AgentId):
    """Replace the people who appear in this video (many-to-many, unordered)."""
    from app.media_presenters import MediaPresenter
    agent, row = video_or_404(owner, agent_id, media_id)
    wanted = list(dict.fromkeys(body.presenter_ids))
    if owned_ids(db, Presenter, wanted, owner) != set(wanted):
        raise HTTPException(422, "Danh sách chứa người giới thiệu không tồn tại")
    scope = (MediaPresenter.owner_id == owner["id"], MediaPresenter.agent_id == agent["id"], MediaPresenter.media_id == media_id)
    current = set(db.scalars(select(MediaPresenter.presenter_id).where(*scope)).all())
    db.execute(delete(MediaPresenter).where(*scope, MediaPresenter.presenter_id.not_in(wanted)))
    db.add_all([MediaPresenter(owner_id=owner["id"], agent_id=agent["id"], media_id=media_id, presenter_id=pid)
                for pid in wanted if pid not in current])
    db.commit()
    return output(db, owner["id"], agent["id"], row)
