"""TikTok LIVE Console product snapshots, scoped to OWNER and Windows Agent."""
import json
import re
import sqlite3
import unicodedata
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

class Snapshot(BaseModel):
    agent_id:str
    products:list[TikTokProduct]=Field(max_length=1000)
    complete:bool=False

def comparison(owner_id,items):
    with SessionLocal() as db:
        own=list(db.scalars(select(Product).where(Product.owner_id==owner_id)).all())
    by_name={}
    for product in own:by_name.setdefault(norm(product.name),[]).append(product)
    seen=set()
    result=[]
    for item in items:
        key=norm(item["name"])
        candidates=by_name.get(key,[])
        seen.add(key)
        result.append({**item,"match_status":"matched" if len(candidates)==1 else "ambiguous" if len(candidates)>1 else "missing_in_sarae",
                       "sarae_product_id":candidates[0].id if len(candidates)==1 else None})
    unmatched=[{"id":p.id,"name":p.name} for p in own if norm(p.name) not in seen]
    return {"products":result,"missing_in_tiktok":unmatched,
            "summary":{"tiktok":len(items),"sarae":len(own),
                       "matched":sum(x["match_status"]=="matched" for x in result),
                       "unmatched_tiktok":sum(x["match_status"]=="missing_in_sarae" for x in result),
                       "ambiguous":sum(x["match_status"]=="ambiguous" for x in result),
                       "missing_in_tiktok":len(unmatched)}}

@router.post("/sync")
def sync(body:Snapshot,x_sarae_agent_key:str|None=Header(None)):
    agent=verify_agent(body.agent_id,x_sarae_agent_key)
    if not body.complete:
        raise HTTPException(422,"Danh sách chưa quét đầy đủ; không ghi đè snapshot")
    names=[norm(x.name) for x in body.products]
    if len(set(names))!=len(names):
        raise HTTPException(422,"Tên sản phẩm TikTok trùng nhau, không thể định danh an toàn")
    data=[x.model_dump() for x in body.products]
    now=datetime.now(timezone.utc).isoformat()
    with connection() as db:
        db.execute("""INSERT INTO snapshots(agent_id,owner_id,scanned_at,complete,products_json)
          VALUES(?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET
          owner_id=excluded.owner_id,scanned_at=excluded.scanned_at,complete=excluded.complete,
          products_json=excluded.products_json""",
          (body.agent_id,agent["owner_id"],now,1,json.dumps(data,ensure_ascii=False)))
    return {"agent_id":body.agent_id,"scanned_at":now,**comparison(agent["owner_id"],data)}

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
