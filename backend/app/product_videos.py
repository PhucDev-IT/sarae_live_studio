"""Selected and ordered videos prepared for each product LIVE."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, Integer, UniqueConstraint, select, delete
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base, SessionLocal
from app.catalog import Product
from app.media import Media

router = APIRouter(prefix="/api/product-videos", tags=["Product LIVE setup"])

class ProductLiveVideo(Base):
    __tablename__ = "product_live_videos"
    __table_args__ = (UniqueConstraint("product_id", "media_id", name="uq_product_live_video"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    sort_order: Mapped[int] = mapped_column(Integer)

class VideoSetup(BaseModel):
    media_ids: list[int] = Field(max_length=500)

@router.get("/{product_id}")
def read_setup(product_id: int):
    with SessionLocal() as db:
        if db.get(Product, product_id) is None:
            raise HTTPException(404, "Không tìm thấy sản phẩm")
        ids = db.scalars(select(ProductLiveVideo.media_id).where(
            ProductLiveVideo.product_id == product_id).order_by(ProductLiveVideo.sort_order)).all()
        return {"product_id": product_id, "media_ids": list(ids)}

@router.put("/{product_id}")
def save_setup(product_id: int, body: VideoSetup):
    if len(body.media_ids) != len(set(body.media_ids)):
        raise HTTPException(422, "Không được thêm trùng video trong cùng một sản phẩm")
    with SessionLocal() as db:
        if db.get(Product, product_id) is None:
            raise HTTPException(404, "Không tìm thấy sản phẩm")
        if body.media_ids:
            found = set(db.scalars(select(Media.id).where(Media.id.in_(body.media_ids))).all())
            if found != set(body.media_ids):
                raise HTTPException(422, "Danh sách chứa video không tồn tại")
        db.execute(delete(ProductLiveVideo).where(ProductLiveVideo.product_id == product_id))
        db.add_all([ProductLiveVideo(product_id=product_id, media_id=mid, sort_order=i)
                    for i, mid in enumerate(body.media_ids)])
        db.commit()
    return {"product_id": product_id, "media_ids": body.media_ids}
