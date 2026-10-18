"""Index configured Windows MP4 folders without transferring any video bytes.

Besides the path index, each video gets a duration and a small JPEG thumbnail
(OpenCV), cached per (size, mtime) so a rescan only probes new or changed files.
"""
import base64
import hashlib
import json
import threading
import time
from pathlib import Path
from app.database import DATA_DIR
from app.config import settings

INDEX_FILE=DATA_DIR/"agent_local_index.json"
FOLDERS_FILE=DATA_DIR/"agent_media_folders.json"
META_FILE=DATA_DIR/"agent_media_meta.json"
THUMB_DIR=DATA_DIR/"agent_thumbs"
THUMB_WIDTH=320
THUMB_MAX_BYTES=40*1024
# Probing videos is slow; spread a large first scan over several syncs.
META_BUDGET_SECONDS=20
_lock=threading.Lock()

def folders():
    if not FOLDERS_FILE.is_file():return []
    data=json.loads(FOLDERS_FILE.read_text(encoding="utf-8"))
    return [Path(s).expanduser().resolve() for s in data if isinstance(s,str)]

def _load_meta():
    try:return json.loads(META_FILE.read_text(encoding="utf-8"))
    except (OSError,ValueError):return {}

def _save_meta(meta):
    temp=META_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(meta,ensure_ascii=False),encoding="utf-8")
    temp.replace(META_FILE)

def probe(path:Path):
    """Return (duration_seconds|None, jpeg_bytes|None) using OpenCV; never raises."""
    try:
        import cv2
    except ImportError:
        return None,None
    capture=cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():return None,None
        fps=capture.get(cv2.CAP_PROP_FPS) or 0
        frames=capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration=round(frames/fps,3) if fps>0 and frames>0 else None
        # A frame ~10% in (max 5 s) avoids black intro frames.
        if duration:capture.set(cv2.CAP_PROP_POS_MSEC,min(duration*100,5000))
        ok,frame=capture.read()
        if not ok:
            capture.set(cv2.CAP_PROP_POS_FRAMES,0)
            ok,frame=capture.read()
        if not ok or frame is None:return duration,None
        height,width=frame.shape[:2]
        if width>THUMB_WIDTH:
            frame=cv2.resize(frame,(THUMB_WIDTH,max(1,round(height*THUMB_WIDTH/width))),interpolation=cv2.INTER_AREA)
        for quality in (75,60,45,30):
            ok,encoded=cv2.imencode(".jpg",frame,[int(cv2.IMWRITE_JPEG_QUALITY),quality])
            if ok and len(encoded)<=THUMB_MAX_BYTES:return duration,encoded.tobytes()
        return duration,None
    except Exception:
        return None,None
    finally:
        capture.release()

def scan(budget_seconds:float=META_BUDGET_SECONDS):
    """Index the folders; items carry duration_seconds (None until probed)."""
    with _lock:
        result=[];local={}
        seen=set()
        meta=_load_meta();fresh={}
        deadline=time.monotonic()+budget_seconds
        for folder in folders():
            if not folder.is_dir():continue
            for path in folder.rglob("*.mp4"):
                if not path.is_file():continue
                resolved=str(path.resolve())
                if resolved in seen:continue
                seen.add(resolved)
                stat=path.stat()
                # Stable per-path ID within the same Agent, not a file content hash.
                media_id=hashlib.sha256(resolved.casefold().encode("utf-8")).hexdigest()
                local[media_id]=resolved
                entry=meta.get(media_id)
                if not entry or entry.get("size")!=stat.st_size or entry.get("mtime")!=stat.st_mtime_ns:
                    entry=None
                    if time.monotonic()<deadline:
                        duration,jpeg=probe(path)
                        thumb=None
                        if jpeg:
                            THUMB_DIR.mkdir(parents=True,exist_ok=True)
                            (THUMB_DIR/f"{media_id}.jpg").write_bytes(jpeg)
                            thumb=True
                        entry={"size":stat.st_size,"mtime":stat.st_mtime_ns,
                               "duration_seconds":duration,"thumb":bool(thumb)}
                if entry:fresh[media_id]=entry
                result.append({"media_id":media_id,"filename":path.name,
                               "size_bytes":stat.st_size,"modified_ns":stat.st_mtime_ns,
                               "duration_seconds":entry.get("duration_seconds") if entry else None})
                if len(result)>=10000:break
            if len(result)>=10000:break
        INDEX_FILE.write_text(json.dumps(local,ensure_ascii=False),encoding="utf-8")
        _save_meta(fresh)
        for stale in set(meta)-set(fresh):
            try:(THUMB_DIR/f"{stale}.jpg").unlink()
            except OSError:pass
        return result

def thumb_base64(media_id:str):
    """Cached JPEG thumbnail of an indexed video as base64, or None."""
    if len(media_id)!=64 or any(c not in "0123456789abcdef" for c in media_id):return None
    try:return base64.b64encode((THUMB_DIR/f"{media_id}.jpg").read_bytes()).decode("ascii")
    except OSError:return None

_cache={"key":None,"index":{},"folders":[]}

def _index_and_folders():
    """Index + allowed folders, re-read only when either file changes (resolve runs per video)."""
    def stamp(path):
        try:
            stat=path.stat()
            return stat.st_mtime_ns,stat.st_size
        except OSError:return None
    key=(stamp(INDEX_FILE),stamp(FOLDERS_FILE))
    if key!=_cache["key"]:
        index=json.loads(INDEX_FILE.read_text(encoding="utf-8")) if key[0] else {}
        _cache.update(key=key,index=index,folders=folders())
    return _cache["index"],_cache["folders"]

def resolve(media_id:str):
    if not INDEX_FILE.is_file():raise ValueError("Chưa đồng bộ thư viện")
    index,permitted=_index_and_folders()
    path=Path(index.get(media_id) or "")
    if not index.get(media_id) or not path.is_file():raise ValueError("Video không tồn tại trên máy Agent")
    if not any(path.resolve().is_relative_to(root) for root in permitted):
        raise ValueError("Video nằm ngoài các thư mục đã cho phép")
    return path
