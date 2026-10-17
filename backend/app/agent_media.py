"""Authenticated Cloud media delivery for the Windows Agent.

Cloud stores canonical files; Windows caches them and OBS opens Windows paths.
This module never gives a Cloud-side filesystem path to OBS.
"""
import hashlib
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from app.catalog import Product
from app.media import Media, MEDIA_DIR
from app.product_videos import ProductLiveVideo
from app.database import SessionLocal
from app.cloud_agents import authorized
from app.config import settings

router = APIRouter(prefix="/api/agent-media", tags=["Agent Media"])

def require_agent(key):
    if not authorized(key, settings.sarae_agent_key):
        raise HTTPException(401, "Agent key không hợp lệ")

def cloud_file(item):
    # Only managed uploads are distributed. Imported absolute paths are local-PC
    # references and cannot be read from a Cloud deployment.
    path=(MEDIA_DIR / Path(item.stored_name).name).resolve()
    if not path.is_relative_to(MEDIA_DIR.resolve()) or not path.is_file():
        raise HTTPException(409, "Video chưa có bản upload trên Cloud")
    return path

@router.get("/products/{product_id}/manifest")
def manifest(product_id:int,x_sarae_agent_key:str|None=Header(None)):
    require_agent(x_sarae_agent_key)
    with SessionLocal() as db:
        if db.get(Product,product_id) is None:
            raise HTTPException(404,"Không tìm thấy sản phẩm")
        rows=db.scalars(select(Media).join(
            ProductLiveVideo,ProductLiveVideo.media_id==Media.id).where(
            ProductLiveVideo.product_id==product_id).order_by(
            ProductLiveVideo.sort_order)).all()
        if not rows:raise HTTPException(409,"Sản phẩm chưa có video")
        videos=[]
        for row in rows:
            path=cloud_file(row)
            digest=hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda:f.read(1024*1024),b""):
                    digest.update(chunk)
            videos.append({"id":row.id,"filename":row.filename,
                           "size_bytes":path.stat().st_size,"sha256":digest.hexdigest()})
        return {"product_id":product_id,"videos":videos}

@router.get("/files/{media_id}")
def file(media_id:int,x_sarae_agent_key:str|None=Header(None)):
    require_agent(x_sarae_agent_key)
    with SessionLocal() as db:
        item=db.get(Media,media_id)
        if item is None:raise HTTPException(404,"Không tìm thấy video")
        path=cloud_file(item)
        return FileResponse(path,filename=Path(item.filename).name,media_type="video/mp4")
