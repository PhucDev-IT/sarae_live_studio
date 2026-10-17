"""Windows-only, opt-in CAPTCHA presence monitor. Never solves or interacts with challenges."""
import base64
import re
import io
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib import request, parse
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.database import DATA_DIR
from app.config import settings

router=APIRouter(prefix="/api/verification-monitor",tags=["Verification Monitor"])
ROOT=DATA_DIR/"verification_monitor"
ROOT.mkdir(parents=True,exist_ok=True)
events=deque(maxlen=100)
guard=threading.RLock()
stop_signal=threading.Event()
worker=None
state={"running":False,"window_found":False,"window_title":"","window_visible":False,"status":"idle","last_scan":None,"error":None,"events_count":0}
config={"interval_seconds":2.0,"threshold":0.82,"telegram_enabled":False,"telegram_chat_id":"","template_loaded":False}
template_dir=ROOT/"templates"
template_dir.mkdir(parents=True,exist_ok=True)
template_file=ROOT/"captcha_reference.png"
def all_templates():
    result=sorted(template_dir.glob("*.png"))
    if template_file.exists():result.append(template_file)
    return result
last_detection=False
positive_count=0
negative_count=0

class ConfigBody(BaseModel):
    interval_seconds:float=Field(default=2.0,ge=0.5,le=15)
    threshold:float=Field(default=0.82,ge=0.65,le=0.99)
    telegram_enabled:bool=False
    telegram_chat_id:str=Field(default="",max_length=50)
class TemplateBody(BaseModel):
    png_base64:str=Field(min_length=20,max_length=5_000_000)

def emit(kind,detail,image=None):
    event={"id":int(time.time()*1000000),"at":datetime.now(timezone.utc).isoformat(),"kind":kind,"detail":detail,"image":image}
    with guard:
        events.append(event)
        state["events_count"]+=1
    if config["telegram_enabled"] and kind in ("captcha_detected","captcha_cleared","monitor_error"):
        threading.Thread(target=notify,args=(event,),daemon=True).start()

def notify(event):
    token=settings.sarae_telegram_bot_token.strip()
    chat=config["telegram_chat_id"].strip()
    if not token or not chat:return
    message=f"Sarae LIVE | {event['kind']}\n{event['detail']}\n{event['at']}"
    try:
        req=request.Request("https://api.telegram.org/bot"+token+"/sendMessage",
                            data=parse.urlencode({"chat_id":chat,"text":message}).encode(),method="POST")
        request.urlopen(req,timeout=8).close()
    except Exception as exc:
        with guard:state["error"]="Telegram: "+str(exc)[:160]

def dependencies():
    if os.name!="nt":raise RuntimeError("Monitor cần Windows Desktop")
    try:
        import cv2
        import numpy
        import mss
        import win32gui
        return cv2,numpy,mss,win32gui
    except ImportError as exc:
        raise RuntimeError("Thiếu thư viện: pip install opencv-python numpy mss pywin32") from exc

def find_window(win32gui):
    matches=[]
    def callback(hwnd,_):
        if not win32gui.IsWindowVisible(hwnd):return
        title=win32gui.GetWindowText(hwnd)
        if "tiktok" in title.lower() and ("live studio" in title.lower() or "live" in title.lower()):
            matches.append((hwnd,title))
    win32gui.EnumWindows(callback,None)
    return matches[0] if matches else (None,"")

def match_reference(cv2, np, frame_gray, reference_gray):
    """Scale-tolerant edge matching inspired by the upstream detector.

    Only detects UI presence. It never extracts, solves or submits a challenge.
    """
    screen_edges = cv2.Canny(frame_gray, 70, 160)
    reference_edges = cv2.Canny(reference_gray, 70, 160)
    if cv2.countNonZero(reference_edges) < 25:
        raise ValueError("Ảnh mẫu thiếu chi tiết cạnh; hãy cắt một vùng tiêu đề CAPTCHA rõ nét")
    best_score, best_scale = -1.0, 1.0
    for scale in (0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2):
        w = max(1, round(reference_gray.shape[1] * scale))
        h = max(1, round(reference_gray.shape[0] * scale))
        if w > frame_gray.shape[1] or h > frame_gray.shape[0]:
            continue
        template = cv2.resize(reference_edges, (w, h), interpolation=cv2.INTER_LINEAR)
        if cv2.countNonZero(template) < 20:
            continue
        score = float(cv2.matchTemplate(screen_edges, template, cv2.TM_CCOEFF_NORMED).max())
        if np.isfinite(score) and score > best_score:
            best_score, best_scale = score, scale
    if best_score < 0:
        raise ValueError("Không thể so khớp ảnh mẫu với màn hình")
    return best_score, best_scale


def loop():
    global last_detection,positive_count,negative_count
    try:
        cv2,np,mss,win32gui=dependencies()
        while not stop_signal.is_set():
            try:
                hwnd,title=find_window(win32gui)
                with guard:
                    state.update(window_found=hwnd is not None,window_title=title,window_visible=bool(hwnd and not win32gui.IsIconic(hwnd)),last_scan=datetime.now(timezone.utc).isoformat(),error=None)
                if not hwnd or win32gui.IsIconic(hwnd):
                    with guard:state["status"]="window_unavailable"
                    positive_count=negative_count=0
                    stop_signal.wait(config["interval_seconds"]);continue
                bounds=win32gui.GetWindowRect(hwnd)
                left,top,right,bottom=bounds
                if right<=left or bottom<=top:raise ValueError("Kích thước cửa sổ không hợp lệ")
                import ctypes
                screen_w=ctypes.windll.user32.GetSystemMetrics(0)
                screen_h=ctypes.windll.user32.GetSystemMetrics(1)
                left=max(0,left);top=max(0,top);right=min(screen_w,right);bottom=min(screen_h,bottom)
                if right-left<120 or bottom-top<100:raise ValueError("Cửa sổ nằm ngoài màn hình chính")
                with mss.mss() as capture:
                    shot=np.array(capture.grab({"left":left,"top":top,"width":right-left,"height":bottom-top}))
                gray=cv2.cvtColor(shot,cv2.COLOR_BGRA2GRAY)
                scores=[]
                for candidate in all_templates():
                    reference=cv2.imread(str(candidate),cv2.IMREAD_GRAYSCALE)
                    if reference is None or reference.shape[0]>gray.shape[0] or reference.shape[1]>gray.shape[1]:
                        continue
                    try:
                        value,scale=match_reference(cv2,np,gray,reference)
                        scores.append((value,scale,candidate.name))
                    except ValueError:
                        continue
                if not scores:
                    with guard:state["status"]="template_missing_or_invalid"
                    stop_signal.wait(config["interval_seconds"]);continue
                score,matched_scale,matched_name=max(scores,key=lambda item:item[0])
                detected=score>=config["threshold"]
                positive_count=positive_count+1 if detected else 0
                negative_count=negative_count+1 if not detected else 0
                with guard:state.update(status="captcha_detected" if last_detection else "watching",match_score=round(score,3),match_scale=matched_scale,matched_template=matched_name)
                if not last_detection and positive_count>=2:
                    last_detection=True
                    image_name="detected_"+datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")+".png"
                    cv2.imwrite(str(ROOT/image_name),shot)
                    with guard:state["status"]="captcha_detected"
                    emit("captcha_detected","Khớp mẫu CAPTCHA, điểm "+str(round(score,3)),image_name)
                elif last_detection and negative_count>=2:
                    last_detection=False
                    with guard:state["status"]="watching"
                    emit("captcha_cleared","Không còn thấy mẫu CAPTCHA; chưa xác minh đã giải thành công.")
            except Exception as exc:
                with guard:state.update(status="error",error=str(exc)[:250])
                stop_signal.wait(config["interval_seconds"])
                continue
            stop_signal.wait(config["interval_seconds"])
    except Exception as exc:
        with guard:state.update(status="error",error=str(exc),running=False)
        emit("monitor_error",str(exc))
    finally:
        with guard:state["running"]=False

@router.get("/status")
def status():
    with guard:return {"state":dict(state),"config":dict(config),"events":list(reversed(events))[:30]}

@router.put("/config")
def configure(body:ConfigBody):
    with guard:
        config.update(body.model_dump())
        config["template_loaded"]=bool(all_templates())
        return dict(config)

@router.post("/template")
def upload_template(body:TemplateBody):
    try:
        raw=base64.b64decode(body.png_base64.split(",")[-1],validate=True)
        if len(raw)>3_000_000:raise ValueError("Ảnh quá lớn")
        cv2,np,*_=dependencies()
        image=cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)
        if image is None or min(image.shape[:2])<20 or max(image.shape[:2])>1800:raise ValueError("Ảnh phải là PNG/JPEG hợp lệ, 20-1800px")
        ok,encoded=cv2.imencode(".png",image)
        if not ok:raise ValueError("Không lưu được ảnh")
        filename="custom_"+datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")+".png"
        (template_dir/filename).write_bytes(encoded.tobytes())
        with guard:config["template_loaded"]=True
        return {"saved":True,"name":filename,"width":int(image.shape[1]),"height":int(image.shape[0])}
    except RuntimeError as exc:raise HTTPException(503,str(exc))
    except Exception as exc:raise HTTPException(400,str(exc))

@router.post("/start")
def start():
    global worker,last_detection,positive_count,negative_count
    with guard:
        if state["running"]:return {"running":True}
        try:dependencies()
        except Exception as exc:raise HTTPException(503,str(exc))
        if not all_templates():raise HTTPException(400,"Cần ít nhất một ảnh mẫu trong thư viện")
        stop_signal.clear();last_detection=False;positive_count=negative_count=0
        state.update(running=True,status="starting",error=None)
        worker=threading.Thread(target=loop,daemon=True,name="sarae-verification-monitor")
        worker.start()
    emit("monitor_started","Đã bắt đầu giám sát")
    return {"running":True}

@router.post("/stop")
def stop():
    stop_signal.set()
    with guard:state.update(running=False,status="stopped")
    emit("monitor_stopped","Đã yêu cầu dừng giám sát")
    return {"running":False}

@router.get("/templates")
def list_templates():
    return {"templates":[{"name":p.name,"bytes":p.stat().st_size} for p in all_templates()]}

@router.delete("/templates/{name}")
def delete_template(name:str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}\.png",name):
        raise HTTPException(400,"Tên ảnh không hợp lệ")
    target=template_dir/name
    if not target.is_file():raise HTTPException(404,"Không tìm thấy ảnh")
    target.unlink()
    with guard:config["template_loaded"]=bool(all_templates())
    return {"deleted":name}
