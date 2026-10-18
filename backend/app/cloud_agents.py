"""Cloud-side registry and command channel for Windows Local Agents.

Agent initiates HTTPS connections; no inbound Windows firewall rule is needed.
OBS/LIVE control runs on the Agent (app.playback_engine); the VPS queues commands.

Contract used by the cloud LIVE facade (keep stable):
    rpc(agent_id, owner_id, kind, payload, timeout=10) -> Agent result (dict/list/str)
        409 Agent offline, 504 no ack in time, 502 Agent reported failure (detail = its message).
    pop_events(agent_id, limit=500) -> [{seq,type,session_id,product_id,detail,created_at}]
        Returns unprocessed Agent events in seq order and marks them processed.

Agent side:
    GET  /api/agents/commands/{agent_id}/next?wait=0..25  long-poll, {"command": {...}|None}
    POST /api/agents/commands/ack                         {agent_id,command_id,success,result}
    POST /api/agents/events                               {agent_id,events:[{seq,type,session_id,product_id,detail}]}
"""
import asyncio
import hmac
import sqlite3
import json
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Literal
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from app.database import DATA_DIR
from app.config import settings
from app.identity import auth, allow_owner, verify_agent, db_open, license_not_expired

router = APIRouter(prefix="/api/agents", tags=["Windows Agents"])
DB = DATA_DIR / "agents.sqlite3"
ONLINE_SECONDS = 35
MAX_WAIT_SECONDS = 25

def connection():
    db=sqlite3.connect(DB, timeout=5)
    db.execute("""CREATE TABLE IF NOT EXISTS agent_status (
       agent_id TEXT PRIMARY KEY, host TEXT NOT NULL, last_seen TEXT NOT NULL,
       status_json TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS agent_commands (
        command_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, kind TEXT NOT NULL,
        payload TEXT NOT NULL, state TEXT NOT NULL, result TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS agent_events (
        agent_id TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
        created_at TEXT NOT NULL, processed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(agent_id, seq))""")
    return db

@contextmanager
def _db():
    """Like ``with connection()`` (commit/rollback) but also closes the handle."""
    db=connection()
    try:
        with db:
            yield db
    finally:
        db.close()

def _now():
    return datetime.now(timezone.utc).isoformat()

def authorized(actual, expected):
    return bool(expected) and hmac.compare_digest(actual or "",expected)

# ----- in-process wake-ups -------------------------------------------------------------------------
# One Condition per Agent; its counter changes whenever a command is queued or acknowledged. Waiters
# also re-check the DB periodically, so several uvicorn workers still work (just with more latency).
_signals_lock=threading.Lock()
_signals:dict[str,list]={}

def _signal(agent_id:str):
    with _signals_lock:
        entry=_signals.get(agent_id)
        if entry is None:
            entry=_signals[agent_id]=[threading.Condition(),0]
        return entry

def _notify(agent_id:str):
    entry=_signal(agent_id)
    with entry[0]:
        entry[1]+=1
        entry[0].notify_all()

# ----- heartbeat / status --------------------------------------------------------------------------

class Heartbeat(BaseModel):
    agent_id:str=Field(min_length=1,max_length=80,pattern=r"^[A-Za-z0-9_-]+$")
    hostname:str=Field(min_length=1,max_length=160)
    verification:dict=Field(default_factory=dict)
    obs:dict|None=None
    local_playback:dict|None=None
    # PlaybackEngine.status() on the Agent: same shape as GET /api/live/status.
    playback:dict|None=None

@router.post("/heartbeat")
def heartbeat(body:Heartbeat, x_sarae_agent_key:str|None=Header(None)):
    verify_agent(body.agent_id, x_sarae_agent_key)
    at=_now()
    sanitized={"verification":body.verification,"obs":body.obs,"local_playback":body.local_playback,
               "playback":body.playback}
    with _db() as db:
        db.execute("""INSERT INTO agent_status(agent_id, host, last_seen, status_json)
                      VALUES(?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET
                      host=excluded.host,last_seen=excluded.last_seen,status_json=excluded.status_json""",
                      (body.agent_id,body.hostname,at,json.dumps(sanitized,ensure_ascii=False)))
    return {"ok":True,"server_time":at}

@router.get("/status")
def get_status(authorization:str|None=Header(None)):
    user=auth(authorization)
    with _db() as db:
        rows=db.execute("SELECT agent_id, host, last_seen, status_json FROM agent_status ORDER BY last_seen DESC LIMIT 1000").fetchall()
    now=datetime.now(timezone.utc)
    agents=[]
    for aid,host,seen,payload in rows:
        try:
            with db_open() as registry:
                owned=registry.execute("SELECT owner_id,hostname FROM registered_agents WHERE id=? AND enabled=1",(aid,)).fetchone()
            if owned is None or (user["role"]!="SUPER_ADMIN" and owned["owner_id"]!=user["id"]):
                continue
        except Exception:
            continue
        age=(now-datetime.fromisoformat(seen)).total_seconds()
        agents.append({"agent_id":aid,"hostname":owned["hostname"],"reported_hostname":host,"last_seen":seen,
                       "online":age<ONLINE_SECONDS,"seconds_ago":round(age,1),**json.loads(payload)})
    return {"agents":agents}

# ----- command validation --------------------------------------------------------------------------

NO_PARAM_KINDS={"monitor_start","monitor_stop","obs_status","obs_pause","obs_resume","obs_next","obs_previous",
    "obs_stop","local_media_next","local_media_stop","playback_cancel_pending","playback_pause",
    "playback_resume","playback_next","playback_previous","playback_stop","media_rescan"}
COMMAND_KINDS=NO_PARAM_KINDS|{"monitor_config","tts_speak","local_media_play","local_playlist_start",
    "product_play","playback_select","playback_seek","obs_configure","obs_inputs"}
# Payload fields that must not stay in the command log once the Agent has fetched them.
SECRET_FIELDS={"password"}

class CommandBody(BaseModel):
    agent_id:str=Field(min_length=1,max_length=80,pattern=r"^[A-Za-z0-9_-]+$")
    kind:str=Field(pattern=r"^("+"|".join(sorted(COMMAND_KINDS))+r")$")
    payload:dict=Field(default_factory=dict)

class AckBody(BaseModel):
    agent_id:str=Field(min_length=1,max_length=80)
    command_id:str=Field(min_length=1,max_length=80)
    success:bool
    # Results carry JSON (e.g. playback status, OBS input list), so allow more than a log line.
    result:str=Field(default="",max_length=65000)

def _media_id(value)->bool:
    return isinstance(value,str) and len(value)==64 and all(c in "0123456789abcdef" for c in value)

def _int(value,name,low,high):
    if not isinstance(value,int) or isinstance(value,bool) or not low<=value<=high:
        raise HTTPException(422,f"{name} phải là số nguyên từ {low} đến {high}")
    return value

def _text(value,name,low,high):
    if not isinstance(value,str) or not low<=len(value.strip())<=high:
        raise HTTPException(422,f"{name} phải dài {low}-{high} ký tự")
    return value.strip()

def _bool(value,name):
    if not isinstance(value,bool):raise HTTPException(422,f"{name} phải là boolean")
    return value

def _media_ids(items):
    if not isinstance(items,list) or not 1<=len(items)<=300 or any(not _media_id(v) for v in items):
        raise HTTPException(422,"Danh sách media_ids không hợp lệ")
    return items

def _obs_connection(payload):
    out={"host":_text(payload.get("host","127.0.0.1"),"host",1,255),
         "port":_int(payload.get("port",4455),"port",1,65535)}
    password=payload.get("password","")
    if password is None:password=""
    if not isinstance(password,str) or len(password)>200:
        raise HTTPException(422,"password không hợp lệ")
    out["password"]=password
    return out

def validate_payload(kind:str,payload:dict|None)->dict:
    """Return the normalized payload for ``kind`` or raise HTTPException(422)."""
    payload=payload or {}
    if kind not in COMMAND_KINDS:raise HTTPException(422,"Lệnh không được hỗ trợ")
    if kind=="tts_speak":
        text=payload.get("text","")
        if not isinstance(text,str) or not 1<=len(text)<=450:
            raise HTTPException(422,"Nội dung TTS từ 1 đến 450 ký tự")
        if payload.get("voice","vi-VN-HoaiMyNeural") not in ("vi-VN-HoaiMyNeural","vi-VN-NamMinhNeural"):
            raise HTTPException(422,"Giọng không được hỗ trợ")
        return payload
    if kind=="local_playlist_start":
        return {"media_ids":_media_ids(payload.get("media_ids")),"loop":_bool(payload.get("loop",True),"loop")}
    if kind=="local_media_play":
        if not _media_id(payload.get("media_id")):raise HTTPException(422,"media_id không hợp lệ")
        return {"media_id":payload["media_id"]}
    if kind=="monitor_config":
        from app.verification_monitor import ConfigBody
        return ConfigBody.model_validate(payload).model_dump()
    if kind=="product_play":
        mode=payload.get("mode","immediate")
        if mode not in ("immediate","after_current"):
            raise HTTPException(422,"mode phải là immediate hoặc after_current")
        return {"session_id":_int(payload.get("session_id"),"session_id",1,2**63-1),
                "product_id":_int(payload.get("product_id"),"product_id",1,2**63-1),
                "media_ids":_media_ids(payload.get("media_ids")),"mode":mode}
    if kind=="playback_select":
        return {"index":_int(payload.get("index"),"index",0,9999)}
    if kind=="playback_seek":
        return {"ms":_int(payload.get("ms"),"ms",0,24*3600*1000)}
    if kind=="obs_inputs":
        return _obs_connection(payload)
    if kind=="obs_configure":
        out=_obs_connection(payload)
        out["input_name"]=_text(payload.get("input_name","Sarae Video"),"input_name",1,200)
        out["dual_mode"]=_bool(payload.get("dual_mode",False),"dual_mode")
        for key in ("source_a","source_b"):
            value=payload.get(key,"")
            if value is None:value=""
            if not isinstance(value,str) or len(value)>200:raise HTTPException(422,f"{key} không hợp lệ")
            out[key]=value.strip()
        return out
    if payload:
        raise HTTPException(422,"Lệnh này không nhận tham số")
    return {}

# ----- queueing ------------------------------------------------------------------------------------

def _owner_row(agent_id:str):
    with db_open() as registry:
        row=registry.execute("""SELECT a.owner_id,l.enabled AS license_enabled,l.expires_at
            FROM registered_agents a JOIN licenses l ON l.owner_id=a.owner_id
            WHERE a.id=? AND a.enabled=1""",(agent_id,)).fetchone()
    if row is None:raise HTTPException(404,"Không tìm thấy Agent")
    if not row["license_enabled"] or not license_not_expired(row["expires_at"]):
        raise HTTPException(403,"License đã hết hạn hoặc bị khóa")
    return row

def _enqueue(agent_id:str,kind:str,payload:dict)->str:
    with _db() as db:
        row=db.execute("SELECT last_seen FROM agent_status WHERE agent_id=?",(agent_id,)).fetchone()
        if not row:raise HTTPException(409,"Agent đang offline")
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(row[0])).total_seconds()
        if age>ONLINE_SECONDS:raise HTTPException(409,"Agent đang offline")
        command_id=str(uuid.uuid4())
        now=_now()
        db.execute("INSERT INTO agent_commands VALUES(?,?,?,?,?,?,?,?)",
                   (command_id,agent_id,kind,json.dumps(payload),"pending",None,now,now))
    _notify(agent_id)
    return command_id

def create_command(agent_id:str, owner_id:str, kind:str, payload:dict|None=None)->dict:
    """Queue a validated command for an Agent the caller already checked belongs to `owner_id`."""
    if _owner_row(agent_id)["owner_id"]!=owner_id:raise HTTPException(404,"Không tìm thấy Agent")
    command_id=_enqueue(agent_id,kind,validate_payload(kind,payload))
    return {"command_id":command_id,"status":"PENDING"}

@router.post("/commands")
def queue_command(body:CommandBody, authorization:str|None=Header(None)):
    row=_owner_row(body.agent_id)
    actor=allow_owner(authorization,row["owner_id"])
    # Global administrators manage licenses/devices, but cannot silently
    # intervene in a customer's active stream.
    if actor["role"]=="SUPER_ADMIN":
        raise HTTPException(403,"SUPER_ADMIN không trực tiếp điều khiển LIVE khách hàng")
    payload=validate_payload(body.kind,body.payload)
    command_id=_enqueue(body.agent_id,body.kind,payload)
    return {"command_id":command_id,"state":"pending"}

def _result_value(text:str|None):
    if not text:return None
    try:return json.loads(text)
    except ValueError:return text

def rpc(agent_id:str, owner_id:str, kind:str, payload:dict|None=None, timeout:float=10):
    """Queue ``kind`` for the Agent owned by ``owner_id`` and wait for its ack.

    Returns the Agent's JSON result. Raises HTTPException: 404/403 unknown agent, wrong owner or
    license, 422 invalid payload, 409 Agent offline, 504 no ack within ``timeout`` seconds (a
    command still pending is cancelled so it never runs late), 502 Agent failure (detail = message).
    """
    row=_owner_row(agent_id)
    if row["owner_id"]!=owner_id:raise HTTPException(403,"Không có quyền với Agent này")
    command_id=_enqueue(agent_id,kind,validate_payload(kind,payload))
    entry=_signal(agent_id)
    deadline=time.monotonic()+max(0.1,timeout)
    while True:
        timed_out=False
        with _db() as db:
            state,result=db.execute("SELECT state,result FROM agent_commands WHERE command_id=?",
                                    (command_id,)).fetchone()
            if state in ("pending","running") and time.monotonic()>=deadline:
                timed_out=True
                db.execute("""UPDATE agent_commands SET state='failed',result=?,updated_at=?
                              WHERE command_id=? AND state='pending'""",
                           ("Hết thời gian chờ Agent nhận lệnh",_now(),command_id))
        if timed_out:raise HTTPException(504,"Agent không phản hồi kịp thời")
        if state=="completed":return _result_value(result)
        if state=="failed":raise HTTPException(502,result or "Agent báo lỗi khi thực thi lệnh")
        with entry[0]:
            seen=entry[1]
            # Wake on ack (same process) or re-check the DB every 0.5 s (other workers).
            entry[0].wait_for(lambda:entry[1]!=seen,timeout=min(0.5,max(0.0,deadline-time.monotonic())))

def _claim(agent_id:str):
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        # Mark abandoned commands failed rather than re-running potentially
        # non-idempotent OBS/TTS actions after a worker disconnect.
        cutoff=(datetime.now(timezone.utc)-timedelta(seconds=90)).isoformat()
        db.execute("""UPDATE agent_commands SET state='failed',
                       result='Agent mất kết nối hoặc không xác nhận sau 90 giây',
                       updated_at=?
                       WHERE agent_id=? AND state='running' AND updated_at<?""",
                   (_now(),agent_id,cutoff))
        row=db.execute("""SELECT command_id,kind,payload FROM agent_commands
             WHERE agent_id=? AND state='pending' ORDER BY created_at LIMIT 1""",(agent_id,)).fetchone()
        if not row:return None
        payload=json.loads(row[2])
        stored=json.dumps({k:("***" if k in SECRET_FIELDS and v else v) for k,v in payload.items()})
        db.execute("UPDATE agent_commands SET state='running',payload=?,updated_at=? WHERE command_id=?",
                   (stored,_now(),row[0]))
    return {"command_id":row[0],"kind":row[1],"payload":payload}

@router.get("/commands/{agent_id}/next")
async def next_command(agent_id:str,wait:int=Query(0,ge=0,le=MAX_WAIT_SECONDS),
                       x_sarae_agent_key:str|None=Header(None)):
    """Return the oldest pending command; with ``wait`` > 0, hold the request up to that many
    seconds until one is queued. Waiting costs no worker thread (async sleep on a counter)."""
    await run_in_threadpool(verify_agent,agent_id,x_sarae_agent_key)
    entry=_signal(agent_id)
    deadline=time.monotonic()+wait
    while True:
        seen=entry[1]
        command=await run_in_threadpool(_claim,agent_id)
        if command or time.monotonic()>=deadline:
            return {"command":command}
        recheck=min(deadline,time.monotonic()+1.0)
        while entry[1]==seen and time.monotonic()<recheck:
            await asyncio.sleep(0.05)

@router.post("/commands/ack")
def ack_command(body:AckBody,x_sarae_agent_key:str|None=Header(None)):
    verify_agent(body.agent_id,x_sarae_agent_key)
    with _db() as db:
        count=db.execute("""UPDATE agent_commands SET state=?,result=?,updated_at=?
           WHERE agent_id=? AND command_id=? AND state='running'""",
           ("completed" if body.success else "failed",body.result,
            _now(),body.agent_id,body.command_id)).rowcount
    if not count:raise HTTPException(404,"Lệnh không còn ở trạng thái đang chạy")
    _notify(body.agent_id)
    return {"ok":True}

@router.get("/commands")
def list_commands(agent_id:str,authorization:str|None=Header(None)):
    with db_open() as registry:
        row=registry.execute("SELECT owner_id FROM registered_agents WHERE id=? AND enabled=1",(agent_id,)).fetchone()
    if row is None:raise HTTPException(404,"Không tìm thấy Agent")
    allow_owner(authorization,row["owner_id"])
    with _db() as db:
        rows=db.execute("""SELECT command_id,kind,state,result,created_at,updated_at FROM agent_commands
           WHERE agent_id=? ORDER BY created_at DESC LIMIT 30""",(agent_id,)).fetchall()
    return {"commands":[dict(zip(("command_id","kind","state","result","created_at","updated_at"),row)) for row in rows]}

# ----- Agent events --------------------------------------------------------------------------------

class AgentEvent(BaseModel):
    seq:int=Field(ge=0)
    type:Literal["PRODUCT_SWITCHED","PLAYBACK_ENDED","ERROR"]
    session_id:int|None=None
    product_id:int|None=None
    detail:dict=Field(default_factory=dict)

class EventBatch(BaseModel):
    agent_id:str=Field(min_length=1,max_length=80,pattern=r"^[A-Za-z0-9_-]+$")
    events:list[AgentEvent]=Field(max_length=200)

@router.post("/events")
def post_events(body:EventBatch,x_sarae_agent_key:str|None=Header(None)):
    """Store playback events; a re-sent seq is ignored, so the Agent can retry safely."""
    verify_agent(body.agent_id,x_sarae_agent_key)
    now=_now()
    with _db() as db:
        accepted=0
        for event in body.events:
            data=event.model_dump()
            accepted+=db.execute("INSERT OR IGNORE INTO agent_events(agent_id,seq,payload,created_at) VALUES(?,?,?,?)",
                                 (body.agent_id,event.seq,json.dumps(data,ensure_ascii=False),now)).rowcount
    return {"accepted":accepted,"last_seq":max((e.seq for e in body.events),default=None)}

def pop_events(agent_id:str,limit:int=500)->list[dict]:
    """Unprocessed events of ``agent_id`` in seq order; they are marked processed atomically."""
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        rows=db.execute("""SELECT seq,payload,created_at FROM agent_events
            WHERE agent_id=? AND processed=0 ORDER BY seq LIMIT ?""",(agent_id,limit)).fetchall()
        if rows:
            db.executemany("UPDATE agent_events SET processed=1 WHERE agent_id=? AND seq=?",
                           [(agent_id,seq) for seq,_,_ in rows])
    return [{**json.loads(payload),"seq":seq,"created_at":created} for seq,payload,created in rows]
