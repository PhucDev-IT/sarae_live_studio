"""Protect Windows Agent credentials with the current Windows user's DPAPI key."""
import base64
import json
import os
from pathlib import Path
from app.database import DATA_DIR

FILE=DATA_DIR/"agent_activation.json"

def save(agent_id:str,credential:str):
    if os.name!="nt":raise RuntimeError("Agent activation chỉ hỗ trợ Windows")
    import win32crypt
    plaintext=json.dumps({"agent_id":agent_id,"agent_credential":credential}).encode("utf-8")
    protected=win32crypt.CryptProtectData(plaintext,"Sarae Agent",None,None,None,0)
    temp=FILE.with_suffix(".tmp")
    temp.write_text(json.dumps({"version":2,"dpapi":base64.b64encode(protected).decode()}),
                    encoding="utf-8")
    os.replace(temp,FILE)

def load():
    if not FILE.is_file():raise RuntimeError("Agent chưa kích hoạt")
    data=json.loads(FILE.read_text(encoding="utf-8"))
    if "dpapi" in data:
        if os.name!="nt":raise RuntimeError("Chỉ Windows user kích hoạt mới có thể mở credential")
        import win32crypt
        _,plaintext=win32crypt.CryptUnprotectData(base64.b64decode(data["dpapi"]),None,None,None,0)
        data=json.loads(plaintext.decode("utf-8"))
    # Legacy plain-text activation is read for migration during the first run.
    # Avoid rewriting without verified network access; re-activation preferred.
    return data
