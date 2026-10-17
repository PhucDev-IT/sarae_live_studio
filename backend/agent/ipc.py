"""JSON-lines IPC server for Tauri sidecar. No local HTTP port.

Input: one JSON request per line on stdin, {id,method,params}.
Output: prefixed JSON events on stdout. Only the parent app writes requests.
"""
import json
import sys
import threading
import platform
import urllib.request
from pathlib import Path
from app.config import settings
from agent.credentials import load as load_credentials, save as save_credentials
from agent.local_library import FOLDERS_FILE, scan as scan_media
from agent.main import main as agent_main

PREFIX="SARAE_IPC:"
stop_flag=threading.Event()
worker=None
lock=threading.Lock()
cloud_file=FOLDERS_FILE.parent/"agent_gui_settings.json"

def emit(data):
    with lock:
        sys.stdout.write(PREFIX+json.dumps(data,ensure_ascii=False,default=str)+"\n")
        sys.stdout.flush()

def cloud_url():
    try:
        data=json.loads(cloud_file.read_text(encoding="utf-8"))
        return data.get("cloud_url") or settings.sarae_cloud_api_url
    except (ValueError,OSError):
        return settings.sarae_cloud_api_url

def validate_url(url):
    from urllib.parse import urlparse
    parsed=urlparse(url.strip())
    if parsed.scheme=="https" and parsed.hostname and not parsed.username and not parsed.password:
        pass
    elif parsed.scheme=="http" and parsed.hostname in ("localhost","127.0.0.1") and not parsed.username and not parsed.password:
        pass
    else:
        raise ValueError("Cloud URL phải là HTTPS hoặc HTTP localhost")
    if parsed.query or parsed.fragment or parsed.path.rstrip("/")!="/api":
        raise ValueError("Cloud URL phải kết thúc bằng /api")
    return url.strip().rstrip("/")

def status():
    try:
        agent=load_credentials()["agent_id"]
    except Exception:agent=None
    try:folders=json.loads(FOLDERS_FILE.read_text(encoding="utf-8"))
    except (OSError,ValueError):folders=[]
    return {"agent_id":agent,"running":bool(worker and worker.is_alive()),
            "cloud_url":cloud_url(),"folders":folders}

def run_agent():
    try:
        agent_main(stop_flag)
    except Exception as exc:
        emit({"event":"error","message":str(exc)})
    finally:
        emit({"event":"stopped"})

def invoke(method,params):
    global worker
    if method=="status":return status()
    if method=="configure_cloud":
        url=validate_url(str(params.get("url","")))
        cloud_file.write_text(json.dumps({"cloud_url":url},ensure_ascii=False),encoding="utf-8")
        settings.sarae_cloud_api_url=url
        return status()
    if method=="activate":
        if worker and worker.is_alive():raise ValueError("Dừng Agent trước khi kích hoạt")
        url=validate_url(cloud_url())
        key=str(params.get("key","")).strip()
        if not key:raise ValueError("Thiếu mã kích hoạt")
        body=json.dumps({"activation_key":key,"hostname":platform.node()}).encode()
        req=urllib.request.Request(url+"/identity/activate",data=body,method="POST",
                                   headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=20) as response:
            result=json.load(response)
        save_credentials(result["agent_id"],result["agent_credential"])
        return status()
    if method=="add_folder":
        folder=Path(str(params.get("path",""))).expanduser().resolve()
        if not folder.is_dir():raise ValueError("Thư mục không tồn tại")
        folders=status()["folders"]
        if str(folder) not in folders:folders.append(str(folder))
        FOLDERS_FILE.write_text(json.dumps(folders,ensure_ascii=False,indent=2),encoding="utf-8")
        return status()
    if method=="remove_folder":
        folder=str(params.get("path",""))
        folders=[p for p in status()["folders"] if p!=folder]
        FOLDERS_FILE.write_text(json.dumps(folders,ensure_ascii=False,indent=2),encoding="utf-8")
        return status()
    if method=="scan":
        videos=scan_media()
        return {"count":len(videos),"items":videos[:30]}
    if method=="start":
        if worker and worker.is_alive():return status()
        if not status()["agent_id"]:raise ValueError("Cần kích hoạt Agent trước")
        settings.sarae_cloud_api_url=validate_url(cloud_url())
        stop_flag.clear()
        worker=threading.Thread(target=run_agent,daemon=False,name="SaraeAgentWorker")
        worker.start()
        emit({"event":"started"})
        return status()
    if method=="stop":
        stop_flag.set()
        return status()
    if method=="shutdown":
        stop_flag.set()
        if worker and worker.is_alive():worker.join(timeout=18)
        return {"ok":True}
    raise ValueError("Lệnh IPC không được hỗ trợ")

def serve():
    emit({"event":"ready","data":status()})
    for line in sys.stdin:
        try:
            request=json.loads(line)
            request_id=request.get("id")
            result=invoke(request.get("method"),request.get("params") or {})
            emit({"id":request_id,"ok":True,"data":result})
            if request.get("method")=="shutdown":break
        except Exception as exc:
            emit({"id":locals().get("request_id"),"ok":False,"error":str(exc)})
    stop_flag.set()
    if worker and worker.is_alive():worker.join(timeout=18)

if __name__=="__main__":serve()
