"""Selected and ordered videos prepared for each product LIVE."""
from fastapi import APIRouter, Depends, HTTPException

from app.identity import current_owner
from app.ownership import Owner, owned, owned_ids
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, select, delete
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, SessionLocal
from app.catalog import Product
from app.media import Media

router = APIRouter(prefix="/api/product-videos", tags=["Product LIVE setup"], dependencies=[Depends(current_owner)])


class ProductLiveVideo(Base):
    __tablename__ = "product_live_videos"
    __table_args__ = (UniqueConstraint("product_id", "media_id", name="uq_product_live_video"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    sort_order: Mapped[int] = mapped_column(Integer)

class VideoSetup(BaseModel):
    media_ids: list[int] = Field(max_length=500)

@router.get("/{product_id}")
def read_setup(product_id: int, owner: Owner):
    with SessionLocal() as db:
        owned(db, Product, product_id, owner, "Không tìm thấy sản phẩm")
        # Join skips rows left behind by videos deleted before playlists were unlinked on delete.
        ids = db.scalars(select(ProductLiveVideo.media_id)
                         .join(Media, Media.id == ProductLiveVideo.media_id)
                         .where(ProductLiveVideo.product_id == product_id)
                         .order_by(ProductLiveVideo.sort_order)).all()
        return {"product_id": product_id, "media_ids": list(ids)}

@router.put("/{product_id}")
def save_setup(product_id: int, body: VideoSetup, owner: Owner):
    if len(body.media_ids) != len(set(body.media_ids)):
        raise HTTPException(422, "Không được thêm trùng video trong cùng một sản phẩm")
    with SessionLocal() as db:
        owned(db, Product, product_id, owner, "Không tìm thấy sản phẩm")
        if body.media_ids:
            if owned_ids(db, Media, body.media_ids, owner) != set(body.media_ids):
                raise HTTPException(422, "Danh sách chứa video không tồn tại")
        db.execute(delete(ProductLiveVideo).where(ProductLiveVideo.product_id == product_id))
        db.add_all([ProductLiveVideo(product_id=product_id, media_id=mid, sort_order=i, owner_id=owner["id"])
                    for i, mid in enumerate(body.media_ids)])
        db.commit()
    return {"product_id": product_id, "media_ids": body.media_ids}
