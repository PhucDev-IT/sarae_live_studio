"""Selected and ordered videos prepared for each product LIVE, per Windows Agent."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, delete
from sqlalchemy.orm import Mapped, mapped_column

from app.catalog import Product
from app.database import Base, SessionLocal
from app.identity import current_owner
from app.media import library, product_media
from app.owner_agents import AgentId, owned_agent
from app.ownership import Owner, owned

router = APIRouter(prefix="/api/product-videos", tags=["Product LIVE setup"], dependencies=[Depends(current_owner)])


class ProductLiveVideo(Base):
    __tablename__ = "product_live_videos"
    __table_args__ = (UniqueConstraint("product_id", "agent_id", "media_id", name="uq_product_live_video"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    # Videos live on one PC: the same product can be prepared differently on each Agent.
    agent_id: Mapped[str] = mapped_column(String(80))
    media_id: Mapped[str] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(Integer)


class VideoSetup(BaseModel):
    media_ids: list[str] = Field(max_length=500)


@router.get("/{product_id}")
def read_setup(product_id: int, owner: Owner, agent_id: AgentId):
    with SessionLocal() as db:
        owned(db, Product, product_id, owner, "Không tìm thấy sản phẩm")
        agent = owned_agent(owner, agent_id)
        return {"product_id": product_id, "agent_id": agent["id"],
                "media_ids": product_media(db, owner["id"], agent["id"], product_id, available_only=False)}


@router.put("/{product_id}")
def save_setup(product_id: int, body: VideoSetup, owner: Owner, agent_id: AgentId):
    if len(body.media_ids) != len(set(body.media_ids)):
        raise HTTPException(422, "Không được thêm trùng video trong cùng một sản phẩm")
    with SessionLocal() as db:
        owned(db, Product, product_id, owner, "Không tìm thấy sản phẩm")
        agent = owned_agent(owner, agent_id)
        if not set(body.media_ids) <= set(library(agent["id"])):
            raise HTTPException(422, "Danh sách chứa video không có trên Agent đang dùng")
        db.execute(delete(ProductLiveVideo).where(ProductLiveVideo.owner_id == owner["id"], ProductLiveVideo.agent_id == agent["id"],
                                                  ProductLiveVideo.product_id == product_id))
        db.add_all([ProductLiveVideo(owner_id=owner["id"], agent_id=agent["id"], product_id=product_id, media_id=mid, sort_order=i)
                    for i, mid in enumerate(body.media_ids)])
        db.commit()
    return {"product_id": product_id, "agent_id": agent_id, "media_ids": body.media_ids}
