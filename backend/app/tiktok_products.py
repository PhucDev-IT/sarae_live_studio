"""TikTok LIVE Console product snapshots, scoped to OWNER and Windows Agent."""
import json
import unicodedata
import sqlite3
from datetime import datetime,timezone

from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel,Field
from sqlalchemy import select
from app.database import DATA_DIR,SessionLocal
from app.catalog import Product
from app.identity import verify_agent,current_owner,db_open

router=APIRouter(prefix="/api/tiktok-products",tags=["TikTok LIVE Product Sync"])
DB=DATA_DIR/"tiktok_product_snapshots.sqlite3"

def norm(s):
    return " ".join(unicodedata.normalize("NFKC",s).casefold().split())

def connection():
    db=sqlite3.connect(DB,timeout=10)
    db.row_factory=sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS snapshots (
       agent_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,scanned_at TEXT NOT NULL,
       complete INTEGER NOT NULL,products_json TEXT NOT NULL)""")
    return db

class TikTokProduct(BaseModel):
    name:str=Field(min_length=1,max_length=500)
    position:int|None=Field(default=None,ge=1,le=100000)
    image_url:str|None=Field(default=None,max_length=2000)
    price_text:str|None=Field(default=None,max_length=100)
    stock_text:str|None=Field(default=None,max_length=100)
    seller_id:str|None=None
    product_id:str|None=None
    stock_num:int|None=None
    origin_price_text:str|None=None
    category_name:str|None=None

class AccountProfile(BaseModel):
    user_id:str=Field(pattern=r"^[0-9]{1,80}$")
    tt_uid:str=""
    user_name:str=""
    nick_name:str=""
    user_type:int|None=None
    avatar_url:str=""

class Snapshot(BaseModel):
    agent_id:str
    account:AccountProfile
    products:list[TikTokProduct]=Field(max_length=1000)
    complete:bool=False

def comparison(owner_id,items):
    """Show whether imported source products belong to the OWNER's persisted catalog.

    Match by TikTok seller and product IDs only, never by mutable display names.
    Legacy DOM-only snapshots are reported as unresolved, not silently merged.
    """
    from sqlalchemy import text
    with SessionLocal() as db:
        shop_rows=db.execute(text("SELECT id,seller_id FROM tiktok_shops WHERE owner_id=:o"),
                             {"o":owner_id}).all()
        shops={str(seller):sid for sid,seller in shop_rows}
        catalog=list(db.scalars(select(Product).where(Product.owner_id==owner_id)).all())
    existing={}
    for p in catalog:
        if p.tiktok_product_id:
            seller=p.tiktok_seller_id or next((s for s,sid in shops.items() if sid==p.shop_id),None)
            if seller:
                existing[(str(seller),str(p.tiktok_product_id))]=p
    result=[];seen=set()
    for row in items:
        key=(str(row.get("seller_id") or ""),str(row.get("product_id") or ""))
        target=existing.get(key)
        if target:seen.add(target.id)
        result.append({**row,"match_status":"matched" if target else "missing_in_sarae",
                       "sarae_product_id":target.id if target else None})
    unmatched=[{"id":p.id,"name":p.name} for p in catalog if p.id not in seen]
    return {"products":result,"missing_in_tiktok":unmatched,
            "summary":{"tiktok":len(items),"sarae":len(catalog),
                       "matched":sum(x["match_status"]=="matched" for x in result),
                       "unmatched_tiktok":sum(x["match_status"]=="missing_in_sarae" for x in result),
                       "ambiguous":0,"missing_in_tiktok":len(unmatched)}}

@router.post("/sync")
def sync(body:Snapshot,x_sarae_agent_key:str|None=Header(None)):
    agent=verify_agent(body.agent_id,x_sarae_agent_key)
    if not body.complete or not body.products:
        raise HTTPException(422,"Danh sách chưa quét đầy đủ hoặc đang rỗng; không ghi đè snapshot")
    data=[x.model_dump() for x in body.products]
    keys=[(x.get("seller_id"),x.get("product_id")) for x in data]
    if len(set(keys))!=len(keys):
        raise HTTPException(422,"TikTok trả về Product ID trùng trong cùng một lần quét")
    if not all(x.get("seller_id") and x.get("product_id") for x in data):
        raise HTTPException(422,"Thiếu TikTok seller_id/product_id; vui lòng quét lại bằng Agent mới")
    from sqlalchemy import text
    from app.tiktok_catalog import import_snapshot
    account=body.account
    try:
        with SessionLocal() as db:
            db.execute(text("""
                INSERT INTO tiktok_accounts(user_id,tt_uid,user_name,nick_name,user_type,avatar_url,last_verified_at)
                VALUES(:user_id,:tt_uid,:user_name,:nick_name,:user_type,:avatar_url,:last_verified_at)
                ON CONFLICT(user_id) DO UPDATE SET
                    tt_uid=excluded.tt_uid,user_name=excluded.user_name,
                    nick_name=excluded.nick_name,user_type=excluded.user_type,
                    avatar_url=excluded.avatar_url,last_verified_at=excluded.last_verified_at
            """),{**account.model_dump(),"last_verified_at":datetime.now(timezone.utc).isoformat()})
            account_id=db.execute(text("SELECT id FROM tiktok_accounts WHERE user_id=:u"),
                                  {"u":account.user_id}).scalar_one()
            db.execute(text("""
                INSERT INTO user_tiktok_accounts(owner_id,account_id)
                VALUES(:owner,:account) ON CONFLICT(owner_id,account_id) DO NOTHING
            """),{"owner":agent["owner_id"],"account":account_id})
            db.commit()
        imported=import_snapshot(agent["owner_id"],data)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    now=datetime.now(timezone.utc).isoformat()
    with connection() as db:
        db.execute("""INSERT INTO snapshots(agent_id,owner_id,scanned_at,complete,products_json)
          VALUES(?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET
          owner_id=excluded.owner_id,scanned_at=excluded.scanned_at,complete=excluded.complete,
          products_json=excluded.products_json""",
          (body.agent_id,agent["owner_id"],now,1,json.dumps(data,ensure_ascii=False)))
    return {"agent_id":body.agent_id,"account":account.model_dump(),"scanned_at":now,"imported":imported,**comparison(agent["owner_id"],data)}

@router.get("/{agent_id}")
def get_snapshot(agent_id:str,owner=Depends(current_owner)):
    with db_open() as registry:
        agent=registry.execute("SELECT owner_id FROM registered_agents WHERE id=? AND enabled=1",(agent_id,)).fetchone()
    if agent is None or agent["owner_id"]!=owner["id"]:
        raise HTTPException(404,"Không tìm thấy Agent")
    with connection() as db:
        snap=db.execute("SELECT * FROM snapshots WHERE agent_id=? AND owner_id=?",(agent_id,owner["id"])).fetchone()
    if snap is None:
        return {"agent_id":agent_id,"scanned_at":None,"products":[],
                **comparison(owner["id"],[])}
    return {"agent_id":agent_id,"scanned_at":snap["scanned_at"],
            **comparison(owner["id"],json.loads(snap["products_json"]))}
