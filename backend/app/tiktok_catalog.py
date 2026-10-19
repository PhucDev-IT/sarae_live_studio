"""TikTok shop catalog: atomic upsert by owner/shop/product identity.

Do not alter Sarae AI description or product-video/session relationships.
"""
from __future__ import annotations
from sqlalchemy import text,select
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from app.database import SessionLocal
from app.catalog import Product
from app.identity import current_owner

router=APIRouter(prefix="/api/tiktok-catalog",tags=["TikTok Catalog"],dependencies=[Depends(current_owner)])

def price(text_value):
    if text_value is None:return None
    digits="".join(c for c in str(text_value) if c.isdigit())
    return float(digits) if digits else None

def import_snapshot(owner_id,products):
    """Only import products with verified seller and product IDs; no name-only linking."""
    count=0;updated=0;shops=set()
    with SessionLocal() as db:
        for row in products:
            seller=str(row.get("seller_id") or "").strip()
            external=str(row.get("product_id") or "").strip()
            if not seller or not external:raise ValueError("Thiếu TikTok seller_id/product_id")
            if not seller.isdigit() or not external.isdigit():raise ValueError("TikTok ID không hợp lệ")
            shop=db.execute(text("SELECT id FROM tiktok_shops WHERE owner_id=:o AND seller_id=:s"),
                            {"o":owner_id,"s":seller}).scalar_one_or_none()
            if shop is None:
                db.execute(text("INSERT OR IGNORE INTO tiktok_shops(owner_id,seller_id,display_name) VALUES(:o,:s,'')"),
                           {"o":owner_id,"s":seller})
                shop=db.execute(text("SELECT id FROM tiktok_shops WHERE owner_id=:o AND seller_id=:s"),
                                {"o":owner_id,"s":seller}).scalar_one()
            shops.add(shop)
            product=db.scalar(select(Product).where(Product.owner_id==owner_id,
                              Product.shop_id==shop,Product.tiktok_product_id==external))
            name=str(row.get("name") or "").strip()[:200]
            if not name:raise ValueError("Thiếu tên sản phẩm TikTok")
            if product is None:
                # Existing handmade products are never guessed/merged by name.
                product=Product(owner_id=owner_id,shop_id=shop,tiktok_product_id=external,
                                sku="TK-"+str(shop)+"-"+external,name=name,description="")
                db.add(product);count+=1
            else:updated+=1
            product.name=name
            product.price=price(row.get("price_text"))
            product.origin_price=price(row.get("origin_price_text"))
            product.stock_num=row.get("stock_num")
            product.tiktok_category=(row.get("category_name") or "")[:250]
            if row.get("image_url"):product.image_url=row["image_url"]
            # Preserve description, attached videos, existing session selections.
        db.commit()
    return {"created":count,"updated":updated,"shops":len(shops)}

@router.get("/shops")
def shops(owner=Depends(current_owner)):
    with SessionLocal() as db:
        return [dict(r._mapping) for r in db.execute(text(
            "SELECT id,seller_id,display_name FROM tiktok_shops WHERE owner_id=:o ORDER BY id"),
            {"o":owner["id"]}).all()]

class DescriptionUpdate(BaseModel):
    description:str=Field(max_length=10000)

@router.put("/products/{product_id}/description")
def update_description(product_id:int,body:DescriptionUpdate,owner=Depends(current_owner)):
    with SessionLocal() as db:
        p=db.scalar(select(Product).where(Product.id==product_id,Product.owner_id==owner["id"]))
        if not p:raise HTTPException(404,"Không tìm thấy sản phẩm")
        p.description=body.description
        db.commit()
        return {"id":p.id,"description":p.description}
