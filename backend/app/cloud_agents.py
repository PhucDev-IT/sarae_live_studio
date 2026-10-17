"""Cloud-side registry for Windows Local Agents.

Agent initiates HTTPS connections; no inbound Windows firewall rule is needed.
V1 provides remote observability only; privileged local OBS commands are not exposed.
"""
import hmac
import sqlite3
import json
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from app.database import DATA_DIR
from app.config import settings

router = APIRouter(prefix="/api/agents", tags=["Windows Agents"])
DB = DATA_DIR / "agents.sqlite3"

def connection():
    db=sqlite3.connect(DB, timeout=5)
    db.execute("""CREATE TABLE IF NOT EXISTS agent_status (
       agent_id TEXT PRIMARY KEY, host TEXT NOT NULL, last_seen TEXT NOT NULL,
       status_json TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS agent_commands (
        command_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, kind TEXT NOT NULL,
        payload TEXT NOT NULL, state TEXT NOT NULL, result TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    return db

def authorized(actual, expected):
    return bool(expected) and hmac.compare_digest(actual or "",expected)

class Heartbeat(BaseModel):
    agent_id:str=Field(min_length=1,max_length=80,pattern=r"^[A-Za-z0-9_-]+$")
    hostname:str=Field(min_length=1,max_length=160)
    verification:dict=Field(default_factory=dict)
    obs:dict|None=None

@router.post("/heartbeat")
def heartbeat(body:Heartbeat, x_sarae_agent_key:str|None=Header(None)):
    if not authorized(x_sarae_agent_key, settings.sarae_agent_key):
        raise HTTPException(401,"Agent key không hợp lệ")
    import json
    at=datetime.now(timezone.utc).isoformat()
    sanitized={"verification":body.verification,"obs":body.obs}
    with connection() as db:
        db.execute("""INSERT INTO agent_status(agent_id, host, last_seen, status_json)
                      VALUES(?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET
                      host=excluded.host,last_seen=excluded.last_seen,status_json=excluded.status_json""",
                      (body.agent_id,body.hostname,at,json.dumps(sanitized,ensure_ascii=False)))
    return {"ok":True,"server_time":at}

@router.get("/status")
def get_status(x_sarae_admin_key:str|None=Header(None)):
    if not authorized(x_sarae_admin_key, settings.sarae_admin_key):
        raise HTTPException(401,"Cần X-Sarae-Admin-Key")
    import json
    with connection() as db:
        rows=db.execute("SELECT agent_id, host, last_seen, status_json FROM agent_status ORDER BY last_seen DESC LIMIT 30").fetchall()
    now=datetime.now(timezone.utc)
    agents=[]
    for aid,host,seen,payload in rows:
        age=(now-datetime.fromisoformat(seen)).total_seconds()
        agents.append({"agent_id":aid,"hostname":host,"last_seen":seen,
                       "online":age<35,"seconds_ago":round(age,1),**json.loads(payload)})
    return {"agents":agents}

class CommandBody(BaseModel):
    agent_id:str=Field(min_length=1,max_length=80,pattern=r"^[A-Za-z0-9_-]+$")
    kind:str=Field(pattern=r"^(monitor_start|monitor_stop|monitor_config|obs_status|obs_pause|obs_resume|obs_next|obs_previous|obs_stop|tts_speak)$")
    payload:dict=Field(default_factory=dict)

class AckBody(BaseModel):
    agent_id:str=Field(min_length=1,max_length=80)
    command_id:str=Field(min_length=1,max_length=80)
    success:bool
    result:str=Field(default="",max_length=2000)

@router.post("/commands")
def queue_command(body:CommandBody, x_sarae_admin_key:str|None=Header(None)):
    if not authorized(x_sarae_admin_key, settings.sarae_admin_key):
        raise HTTPException(401,"Cần xác thực quản trị")
    if body.kind=="tts_speak":
        text=body.payload.get("text","")
        if not isinstance(text,str) or not 1<=len(text)<=450:
            raise HTTPException(422,"Nội dung TTS từ 1 đến 450 ký tự")
        if body.payload.get("voice","vi-VN-HoaiMyNeural") not in ("vi-VN-HoaiMyNeural","vi-VN-NamMinhNeural"):
            raise HTTPException(422,"Giọng không được hỗ trợ")
    elif body.kind=="monitor_config":
        from app.verification_monitor import ConfigBody
        body.payload=ConfigBody.model_validate(body.payload).model_dump()
    elif body.payload:
        raise HTTPException(422,"Lệnh này không nhận tham số")
    with connection() as db:
        row=db.execute("SELECT last_seen FROM agent_status WHERE agent_id=?",(body.agent_id,)).fetchone()
        if not row:raise HTTPException(404,"Không tìm thấy Agent")
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(row[0])).total_seconds()
        if age>35:raise HTTPException(409,"Agent đang offline")
        command_id=str(uuid.uuid4())
        now=datetime.now(timezone.utc).isoformat()
        db.execute("INSERT INTO agent_commands VALUES(?,?,?,?,?,?,?,?)",
                   (command_id,body.agent_id,body.kind,json.dumps(body.payload), "pending",None,now,now))
    return {"command_id":command_id,"state":"pending"}

@router.get("/commands/{agent_id}/next")
def next_command(agent_id:str,x_sarae_agent_key:str|None=Header(None)):
    if not authorized(x_sarae_agent_key,settings.sarae_agent_key):
        raise HTTPException(401,"Agent key không hợp lệ")
    with connection() as db:
        # Mark abandoned commands failed rather than re-running potentially
        # non-idempotent OBS/TTS actions after a worker disconnect.
        cutoff=(datetime.now(timezone.utc)-timedelta(seconds=90)).isoformat()
        db.execute("""UPDATE agent_commands SET state='failed',
                       result='Agent mất kết nối hoặc không xác nhận sau 90 giây',
                       updated_at=?
                       WHERE agent_id=? AND state='running' AND updated_at<?""",
                   (datetime.now(timezone.utc).isoformat(),agent_id,cutoff))
        row=db.execute("""SELECT command_id,kind,payload FROM agent_commands
             WHERE agent_id=? AND state='pending' ORDER BY created_at LIMIT 1""",(agent_id,)).fetchone()
        if not row:return {"command":None}
        db.execute("UPDATE agent_commands SET state='running',updated_at=? WHERE command_id=?",
                   (datetime.now(timezone.utc).isoformat(),row[0]))
    return {"command":{"command_id":row[0],"kind":row[1],"payload":json.loads(row[2])}}

@router.post("/commands/ack")
def ack_command(body:AckBody,x_sarae_agent_key:str|None=Header(None)):
    if not authorized(x_sarae_agent_key,settings.sarae_agent_key):
        raise HTTPException(401,"Agent key không hợp lệ")
    with connection() as db:
        count=db.execute("""UPDATE agent_commands SET state=?,result=?,updated_at=?
           WHERE agent_id=? AND command_id=? AND state='running'""",
           ("completed" if body.success else "failed",body.result,
            datetime.now(timezone.utc).isoformat(),body.agent_id,body.command_id)).rowcount
    if not count:raise HTTPException(404,"Lệnh không còn ở trạng thái đang chạy")
    return {"ok":True}

@router.get("/commands")
def list_commands(agent_id:str,x_sarae_admin_key:str|None=Header(None)):
    if not authorized(x_sarae_admin_key,settings.sarae_admin_key):
        raise HTTPException(401,"Cần xác thực quản trị")
    with connection() as db:
        rows=db.execute("""SELECT command_id,kind,state,result,created_at,updated_at FROM agent_commands
           WHERE agent_id=? ORDER BY created_at DESC LIMIT 30""",(agent_id,)).fetchall()
    return {"commands":[dict(zip(("command_id","kind","state","result","created_at","updated_at"),row)) for row in rows]}
