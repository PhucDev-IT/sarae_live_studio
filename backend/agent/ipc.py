"""JSON-lines IPC server for Tauri sidecar. No local HTTP port.

Input: one JSON request per line on stdin, {id,method,params}.
Output: prefixed JSON events on stdout. Only the parent app writes requests.
"""
import json
import logging
import os
import sys
import threading
import time
import platform
import urllib.request
from pathlib import Path

# Pipes from Tauri default to the ANSI code page; Vietnamese text would raise UnicodeEncodeError.
for _stream in (sys.stdin,sys.stdout,sys.stderr):
    try:_stream.reconfigure(encoding="utf-8",errors="replace")
    except (AttributeError,ValueError):pass

from app.config import settings
from agent.credentials import load as load_credentials, save as save_credentials
from agent.local_library import FOLDERS_FILE, scan as scan_media
from agent.main import main as agent_main

PREFIX="SARAE_IPC:"
stop_flag=threading.Event()
worker=None
lock=threading.Lock()
cloud_file=FOLDERS_FILE.parent/"agent_gui_settings.json"
lock_file=FOLDERS_FILE.parent/"agent_core.lock"
_out=sys.stdout

def emit(data):
    with lock:
        _out.write(PREFIX+json.dumps(data,ensure_ascii=False,default=str)+"\n")
        _out.flush()

class LogStream:
    """Turns print() output from Agent threads into whole-line log events so it never splits an IPC line."""
    def __init__(self):self.buffer="";self.guard=threading.Lock()
    def write(self,text):
        with self.guard:
            self.buffer+=text
            *lines,self.buffer=self.buffer.split("\n")
        for line in lines:
            if line.strip():emit({"event":"log","message":line.rstrip("\r")})
        return len(text)
    def flush(self):pass
    def isatty(self):return False

class ShortOBSErrors(logging.Filter):
    """obsws_python logs a full traceback every time OBS is closed; keep one line in the GUI log."""
    def filter(self,record):
        if record.name.startswith("obsws_python"):record.exc_info=None;record.exc_text=None
        return True

def setup_logging():
    handler=logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(ShortOBSErrors())
    logging.basicConfig(level=logging.WARNING,handlers=[handler])

def acquire_single_instance(wait=8.0):
    """One Agent Core per Windows user; a previous core may still be shutting down, so retry briefly."""
    import msvcrt
    handle=open(lock_file,"a+")
    deadline=time.monotonic()+wait
    while True:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            return handle
        except OSError:
            if time.monotonic()>=deadline:
                handle.close()
                return None
            time.sleep(0.3)

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
    except (Exception,SystemExit) as exc:
        emit({"event":"error","message":str(exc)})
    finally:
        emit({"event":"stopped"})

def invoke(method,params):
    global worker
    if method not in {"status","configure_cloud","activate","add_folder","remove_folder","scan","start","stop","shutdown"}:raise ValueError("Lệnh IPC không được hỗ trợ")
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
        if worker and worker.is_alive():
            worker.join(timeout=20)
            if worker.is_alive():raise ValueError("Agent vẫn đang xử lý, chưa thể thoát")
        return {"ok":True}
    raise ValueError("Lệnh IPC không được hỗ trợ")

def serve():
    instance=acquire_single_instance() if os.name=="nt" else True
    if not instance:
        emit({"event":"error","message":"Một Agent Core khác đang chạy trên máy này"})
        return 3
    sys.stdout=LogStream()
    setup_logging()
    emit({"event":"ready","data":status()})
    for line in sys.stdin:
        request_id=None
        try:
            request=json.loads(line.lstrip("﻿"))
            request_id=request.get("id")
            result=invoke(request.get("method"),request.get("params") or {})
            emit({"id":request_id,"ok":True,"data":result})
            if request.get("method")=="shutdown":break
        except Exception as exc:
            emit({"id":request_id,"ok":False,"error":str(exc)})
    stop_flag.set()
    if worker and worker.is_alive():worker.join(timeout=20)
    return 0

if __name__=="__main__":sys.exit(serve())
