"""Index configured Windows MP4 folders without transferring any video bytes."""
import hashlib
import json
from pathlib import Path
from app.database import DATA_DIR
from app.config import settings

INDEX_FILE=DATA_DIR/"agent_local_index.json"
FOLDERS_FILE=DATA_DIR/"agent_media_folders.json"

def folders():
    if not FOLDERS_FILE.is_file():return []
    data=json.loads(FOLDERS_FILE.read_text(encoding="utf-8"))
    return [Path(s).expanduser().resolve() for s in data if isinstance(s,str)]

def scan():
    result=[];local={}
    seen=set()
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
            result.append({"media_id":media_id,"filename":path.name,
                           "size_bytes":stat.st_size,"modified_ns":stat.st_mtime_ns})
            if len(result)>=10000:break
        if len(result)>=10000:break
    INDEX_FILE.write_text(json.dumps(local,ensure_ascii=False),encoding="utf-8")
    return result

def resolve(media_id:str):
    if not INDEX_FILE.is_file():raise ValueError("Chưa đồng bộ thư viện")
    index=json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    path=Path(index.get(media_id,""))
    if not path.is_file():raise ValueError("Video không tồn tại trên máy Agent")
    permitted=folders()
    if not any(path.resolve().is_relative_to(root) for root in permitted):
        raise ValueError("Video nằm ngoài các thư mục đã cho phép")
    return path
