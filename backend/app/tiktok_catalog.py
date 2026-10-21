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
    """Import seller-sourced products without turning each seller into a LIVE account.

    Legacy catalog products are matched by their old (owner, shop seller, product)
    identity and retain their IDs, video links and session references.
    """
    count=0
    updated=0
    sellers=set()
    with SessionLocal() as db:
        legacy_shops={str(seller):sid for sid,seller in db.execute(
            text("SELECT id,seller_id FROM tiktok_shops WHERE owner_id=:o"),
            {"o":owner_id}).all()}
        for row in products:
            seller=str(row.get("seller_id") or "").strip()
            external=str(row.get("product_id") or "").strip()
            if not seller.isdigit() or not external.isdigit():
                raise ValueError("Thiếu hoặc sai TikTok seller_id/product_id")
            sellers.add(seller)
            name=str(row.get("name") or "").strip()[:200]
            if not name:
                raise ValueError("Thiếu tên sản phẩm TikTok")
            product=db.scalar(select(Product).where(
                Product.owner_id==owner_id,
                Product.tiktok_seller_id==seller,
                Product.tiktok_product_id==external))
            if product is None and seller in legacy_shops:
                # In-place migration of a known legacy product, never match by name.
                product=db.scalar(select(Product).where(
                    Product.owner_id==owner_id,
                    Product.shop_id==legacy_shops[seller],
                    Product.tiktok_product_id==external))
            if product is None:
                # SKU length <= 64, deterministic and collision-resistant for distinct IDs.
                import hashlib
                digest=hashlib.sha256((seller+":"+external).encode()).hexdigest()[:24].upper()
                product=Product(owner_id=owner_id,shop_id=None,
                                tiktok_seller_id=seller,tiktok_product_id=external,
                                sku="TK-"+digest,name=name,description="")
                db.add(product)
                count+=1
            else:
                updated+=1
                product.tiktok_seller_id=seller
            product.name=name
            product.price=price(row.get("price_text"))
            product.origin_price=price(row.get("origin_price_text"))
            product.stock_num=row.get("stock_num")
            product.tiktok_category=(row.get("category_name") or "")[:250]
            if row.get("image_url"):
                product.image_url=row["image_url"]
        db.commit()
    return {"created":count,"updated":updated,"shops":len(sellers)}


@router.get("/accounts")
def accounts(owner=Depends(current_owner)):
    """Actual TikTok LIVE accounts verified from account_info, not seller IDs."""
    with SessionLocal() as db:
        return [dict(r._mapping) for r in db.execute(text("""
            SELECT a.id,a.user_id,a.tt_uid,a.user_name,a.nick_name,a.user_type,
                   a.avatar_url,a.last_verified_at
            FROM tiktok_accounts a JOIN user_tiktok_accounts ua ON ua.account_id=a.id
            WHERE ua.owner_id=:o ORDER BY a.id
        """),{"o":owner["id"]}).all()]


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
