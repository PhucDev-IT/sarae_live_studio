"""Read-only LIVE preparation checks, scoped to the selected OWNER and Agent.

Catalog records are durable. TikTok snapshots are observational: never mutate the catalog or
the LIVE lineup when the browser list changes. No writes or network calls in this module.
"""
from __future__ import annotations

import json
from datetime import datetime,timezone
from sqlalchemy import select
from app.catalog import Product
from app.product_videos import ProductLiveVideo
from app.agent_library import connection as library_connection
from app.tiktok_products import connection as snapshot_connection,norm

def assess(db,owner_id:str,agent_id:str,product_ids:list[int])->dict:
    products={p.id:p for p in db.scalars(select(Product).where(Product.owner_id==owner_id,
                        Product.id.in_(product_ids))).all()} if product_ids else {}
    linked={}
    if product_ids:
        pairs=db.execute(select(ProductLiveVideo.product_id,ProductLiveVideo.media_id).where(
            ProductLiveVideo.owner_id==owner_id,ProductLiveVideo.agent_id==agent_id,
            ProductLiveVideo.product_id.in_(product_ids))).all()
        for pid,mid in pairs:linked.setdefault(pid,set()).add(mid)
    with library_connection() as library_db:
        present={r["media_id"] for r in library_db.execute(
            "SELECT media_id FROM agent_media WHERE agent_id=? AND available=1",(agent_id,))}
    rows=[]
    for pid in product_ids:
        product=products.get(pid)
        reasons=[]
        if product is None: reasons.append("PRODUCT_MISSING")
        else:
            if product.status!="ACTIVE":reasons.append("PRODUCT_INACTIVE")
            if not product.name.strip():reasons.append("NAME_MISSING")
        available=len(linked.get(pid,set())&present)
        if available==0: reasons.append("VIDEO_UNAVAILABLE")
        rows.append({"product_id":pid,"name":product.name if product else None,
                     "available_video_count":available,"ready":not reasons,"reasons":reasons})
    return {"products":rows,"ready":all(p["ready"] for p in rows)}

def reconcile(db,owner_id:str,agent_id:str,selected:list[int])->dict:
    """Compare selected LIVE products against the latest TikTok browser snapshot.

    Legacy snapshots contain names but no TikTok IDs, so those matches are provisional.
    A missing/stale snapshot is UNKNOWN, never interpreted as an empty TikTok lineup.
    """
    readiness=assess(db,owner_id,agent_id,selected)
    with snapshot_connection() as conn:
        snap=conn.execute("SELECT scanned_at,products_json FROM snapshots WHERE owner_id=? AND agent_id=?",
                          (owner_id,agent_id)).fetchone()
    if not snap:
        return {**readiness,"snapshot_at":None,"snapshot_state":"UNKNOWN",
                "missing_on_tiktok":[],"unselected_on_sarae":[],"ambiguous":[]}
    source=json.loads(snap["products_json"])
    seen={}
    for row in source:seen.setdefault(norm(row["name"]),[]).append(row)
    selected_products={p.id:p for p in db.scalars(select(Product).where(
        Product.owner_id==owner_id,Product.id.in_(selected))).all()} if selected else {}
    missing=[];ambiguous=[]
    chosen_names=set()
    for pid in selected:
        p=selected_products.get(pid)
        if not p:continue
        key=norm(p.name);chosen_names.add(key)
        matches=seen.get(key,[])
        if not matches:missing.append({"product_id":pid,"name":p.name})
        elif len(matches)>1:ambiguous.append({"product_id":pid,"name":p.name,"reason":"DUPLICATE_TIKTOK_NAME"})
    unselected=[{"name":row["name"],"product_id":row.get("product_id")}
                for row in source if norm(row["name"]) not in chosen_names]
    try:
        ts=datetime.fromisoformat(snap["scanned_at"].replace("Z","+00:00"))
        if ts.tzinfo is None:ts=ts.replace(tzinfo=timezone.utc)
        recent=(datetime.now(timezone.utc)-ts).total_seconds()<=600
    except (ValueError,TypeError):
        recent=False
    return {**readiness,"snapshot_at":snap["scanned_at"],
            "snapshot_state":"FRESH" if recent else "STALE",
            "matching_basis":"NAME_ONLY_UNTIL_TIKTOK_ID_LINKED",
            "missing_on_tiktok":missing,"unselected_on_sarae":unselected,"ambiguous":ambiguous}
