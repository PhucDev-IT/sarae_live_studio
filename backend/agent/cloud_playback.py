"""Windows-local product media cache and OBS playback for Cloud-based catalogs.

One Agent process owns this playback state. Downloads are verified before use.
"""
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from urllib.request import Request, urlopen
from app.config import settings
from app.database import DATA_DIR
from app.live import state as obs_state

CACHE=DATA_DIR/"agent_media"
CACHE.mkdir(parents=True,exist_ok=True)

class CloudPlayback:
    def __init__(self):
        self.lock=RLock()
        self.product_id=None
        self.files=[]
        self.index=0
        self.active=False

    def _get_json(self,url):
        req=Request(url,headers={"X-Sarae-Agent-Key":settings.sarae_agent_key})
        with urlopen(req,timeout=30) as response:return json.load(response)

    def prepare(self,product_id:int):
        if product_id<=0:raise ValueError("product_id phải lớn hơn 0")
        base=settings.sarae_cloud_api_url.rstrip("/")
        manifest=self._get_json(f"{base}/agent-media/products/{product_id}/manifest")
        planned=[]
        for item in manifest["videos"]:
            digest=item["sha256"]
            if len(digest)!=64 or not all(c in "0123456789abcdef" for c in digest):
                raise ValueError("SHA256 không hợp lệ")
            dest=CACHE/(digest+".mp4")
            if not dest.is_file() or self._checksum(dest)!=digest:
                temp=CACHE/(digest+".part")
                req=Request(f"{base}/agent-media/files/{int(item['id'])}",
                            headers={"X-Sarae-Agent-Key":settings.sarae_agent_key})
                try:
                    with urlopen(req,timeout=120) as response,temp.open("wb") as output:
                        import shutil
                        shutil.copyfileobj(response,output,length=1024*1024)
                    if temp.stat().st_size!=int(item["size_bytes"]) or self._checksum(temp)!=digest:
                        raise ValueError("Video tải về không khớp kích thước/SHA256")
                    os.replace(temp,dest)
                finally:
                    temp.unlink(missing_ok=True)
            planned.append(str(dest))
        if not planned:raise ValueError("Danh sách video trống")
        return planned

    @staticmethod
    def _checksum(path):
        sha=hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):sha.update(chunk)
        return sha.hexdigest()

    def play(self,product_id:int):
        # Do not compete with legacy local LIVE session if it owns the source.
        with obs_state.lock:
            if obs_state.active:
                raise ValueError("Một phiên phát local đang chạy. Hãy dừng phiên cũ trước")
        files=self.prepare(product_id)
        with self.lock:
            self.files=files
            self.product_id=product_id
            self.index=0
            self._load_current()
            self.active=True
            return self.status()

    def _load_current(self):
        with obs_state.lock:
            client=obs_state.client()
            try:
                name=obs_state.config.input_name
                client.get_input_settings(name)
                client.set_input_settings(name,{"local_file":self.files[self.index],
                    "is_local_file":True,"looping":False,"close_when_inactive":False},overlay=True)
                client.trigger_media_input_action(name,"OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
            finally:
                client.base_client.ws.close()

    def next(self):
        with self.lock:
            if not self.active:raise ValueError("Chưa phát video Cloud")
            self.index=(self.index+1)%len(self.files)
            self._load_current()
            return self.status()

    def stop(self):
        with self.lock:
            self.active=False
            return self.status()

    def status(self):
        with self.lock:
            return {"active":self.active,"product_id":self.product_id,
                    "index":self.index,"count":len(self.files),"source":obs_state.config.input_name}

cloud_playback=CloudPlayback()
