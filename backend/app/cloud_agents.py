"""Cloud-side registry and command channel for Windows Agents.

Agents initiate HTTPS connections (no inbound firewall rule). OBS/LIVE control runs on the Agent
(app.playback_engine); the server creates commands and tracks each one through a lifecycle:

    PENDING ──claim──► RUNNING ──ack──► SUCCESS | FAILED
       │                  │
       │ expires_at       │ lease_until passed without ack
       ▼                  ▼
    TIMEOUT        retryable kind & attempts left & not expired → PENDING again (re-delivered)
    CANCELLED      otherwise → TIMEOUT (never re-delivered: the action may already have happened)

- Claim is atomic (BEGIN IMMEDIATE) and per lane: at most one RUNNING command per Agent in the "live" lane (OBS
  playback) and one in the "background" lane (TTS, library rescan, CAPTCHA monitor), so commands of a lane run in
  order and a long TTS never blocks a product switch.
- Each claim gets a claim_token; an ack must carry a token issued for that command. A late ack (after TIMEOUT, or from an
  earlier claim of a re-delivered retryable command) is recorded with late_ack=1 because the action did happen.
- Idempotency-Key: the same key for the same Agent returns the existing command instead of creating a second one.
- The Agent also keeps a journal of executed command ids (agent/main.py) and re-acks instead of running one twice.

Server-side API (used by the web facade): create_command(), wait_command(), run_command(), rpc(), cancel_commands(),
command_view(), pop_events(). Agent side:
    GET  /api/agents/commands/{agent_id}/next?wait=0..25   long-poll, {"command": {..., claim_token}|None}
    POST /api/agents/commands/ack                          {agent_id, command_id, claim_token, success, result}
    POST /api/agents/heartbeat, POST /api/agents/events
Web side (OWNER):
    POST /api/agents/{agent_id}/commands                   {kind, payload, idempotency_key?, wait?}
    GET  /api/agents/{agent_id}/commands[/{command_id}]
    POST /api/agents/{agent_id}/commands/{command_id}/cancel
"""
import asyncio
import hmac
import logging
import sqlite3
import json
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Literal
from urllib import parse, request
from fastapi import APIRouter, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from app.config import settings
from app.database import DATA_DIR
from app.identity import auth, allow_owner, verify_agent, agent_platform, db_open, license_not_expired

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agents", tags=["Windows Agents"])


DB = DATA_DIR / "agents.sqlite3"
ONLINE_SECONDS = 35
MAX_WAIT_SECONDS = 25
FINAL = ("SUCCESS", "FAILED", "TIMEOUT", "CANCELLED")

# ----- command policy --------------------------------------------------------------------------------

# Safe to deliver again when an ack is lost: running them twice leaves the same end state.
RETRYABLE_KINDS = {"timeline_set", "overlay_check", "overlay_preview", "GET_PIN_STATE", "SET_AUTO_PIN", "obs_status", "obs_inputs", "obs_configure", "tiktok_products_sync", "media_prepare", "monitor_config", "monitor_start",
                   "monitor_stop", "playback_pause", "playback_resume", "playback_stop", "playback_cancel_pending",
                   "obs_pause", "obs_resume", "obs_stop", "local_media_stop", "android_pause", "android_resume",
                   "android_stop", "android_next", "android_previous", "android_queue_set", "android_rotation_set",
                   "android_mock_live_start", "android_mock_live_stop"}
BACKGROUND_KINDS = {"PIN_PRODUCT", "UNPIN_PRODUCT", "GET_PIN_STATE", "SET_AUTO_PIN", "tts_speak", "tts_prepare", "tiktok_products_sync", "media_prepare", "monitor_config", "monitor_start", "monitor_stop"}
MAX_ATTEMPTS = 3
DEFAULT_TTL_SECONDS = 30          # a LIVE control not picked up within 30 s is stale: TIMEOUT, never played late
SLOW_TTL_SECONDS = 180
DEFAULT_LEASE_SECONDS = 30        # time the Agent has to ack after claiming
SLOW_LEASE_SECONDS = 150
SLOW_KINDS = {"tiktok_products_sync", "tts_speak", "obs_configure", "obs_inputs", "overlay_check"}


# TikTok LIVE chat messages (Playwright on the Console) run in their own lane: never behind a TTS, never in front.
CHAT_KINDS = {"chat_send", "chat_probe"}
CHAT_TTL_SECONDS = 120


def lane(kind: str) -> str:
    if kind in CHAT_KINDS:
        return "chat"
    return "background" if kind in BACKGROUND_KINDS else "live"


def ttl_seconds(kind: str) -> int:
    if kind in CHAT_KINDS:
        return CHAT_TTL_SECONDS
    return SLOW_TTL_SECONDS if kind in SLOW_KINDS or kind in BACKGROUND_KINDS else DEFAULT_TTL_SECONDS


def lease_seconds(kind: str) -> int:
    return SLOW_LEASE_SECONDS if kind in SLOW_KINDS or kind in CHAT_KINDS else DEFAULT_LEASE_SECONDS


# ----- storage ---------------------------------------------------------------------------------------

def connection():
    db=sqlite3.connect(DB, timeout=5)
    db.execute("""CREATE TABLE IF NOT EXISTS agent_status (
       agent_id TEXT PRIMARY KEY, host TEXT NOT NULL, last_seen TEXT NOT NULL,
       status_json TEXT NOT NULL)""")
    if "captcha_alert" not in {row[1] for row in db.execute("PRAGMA table_info(agent_status)")}:
        db.execute("ALTER TABLE agent_status ADD COLUMN captcha_alert TEXT")
    columns={row[1] for row in db.execute("PRAGMA table_info(agent_commands)")}
    if columns and "status" not in columns:
        # Pre-production schema without a lifecycle: nothing in it is worth keeping.
        db.execute("DROP TABLE agent_commands")
    db.execute("""CREATE TABLE IF NOT EXISTS agent_commands (
        command_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, agent_id TEXT NOT NULL, kind TEXT NOT NULL,
        lane TEXT NOT NULL, payload TEXT NOT NULL, context TEXT, status TEXT NOT NULL,
        retryable INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, claim_tokens TEXT NOT NULL DEFAULT '[]',
        lease_until TEXT, idempotency_key TEXT, session_id INTEGER, result TEXT, error TEXT,
        late_ack INTEGER NOT NULL DEFAULT 0, applied_status TEXT, created_at TEXT NOT NULL, started_at TEXT,
        completed_at TEXT, expires_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_commands_idempotency
        ON agent_commands(agent_id, idempotency_key) WHERE idempotency_key IS NOT NULL""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_agent_commands_queue ON agent_commands(agent_id, status, created_at)")
    db.execute("""CREATE TABLE IF NOT EXISTS agent_events (
        agent_id TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
        created_at TEXT NOT NULL, processed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(agent_id, seq))""")
    return db

@contextmanager
def _db():
    """Like ``with connection()`` (commit/rollback) but also closes the handle."""
    db=connection()
    db.row_factory=sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()

def _now():
    return datetime.now(timezone.utc).isoformat()

def _at(seconds:float)->str:
    return (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()

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
    # Media cache job (ids, revisions, progress; never file paths): see agent_media.media_readiness.
    media_cache:dict|None=None
    # VoiceService.status() on the Agent: queue, sentence being spoken, last result.
    voice:dict|None=None
    pin:dict|None=None

def _media_cache(report:dict|None)->dict|None:
    """Keep only what readiness needs from the Agent's cache report, with bounded sizes."""
    if not isinstance(report,dict):
        return None
    def items(key,fields,limit):
        rows=report.get(key) if isinstance(report.get(key),list) else []
        return [{f:str(r.get(f))[:200] if r.get(f) is not None else None for f in fields} for r in rows[:limit] if isinstance(r,dict)]
    number=lambda v:int(v) if isinstance(v,(int,float)) and v>=0 else None
    session=report.get("session_id")
    return {"session_id":session if isinstance(session,int) else None,"state":str(report.get("state") or "IDLE")[:20],
            "total":number(report.get("total")),"bytes_total":number(report.get("bytes_total")),
            "bytes_done":number(report.get("bytes_done")),
            "ready":items("ready",("media_id","revision"),300),"failed":items("failed",("media_id","error"),50)}

def _voice(report:dict|None)->dict|None:
    """Bounded view of the Agent's voice queue for the web."""
    if not isinstance(report,dict):
        return None
    number=lambda v:int(v) if isinstance(v,(int,float)) and not isinstance(v,bool) and v>=0 else 0
    last=report.get("last") if isinstance(report.get("last"),dict) else None
    caps=report.get("capabilities") if isinstance(report.get("capabilities"),dict) else None
    out={"version":str(report.get("version", ""))[:40],"queue":number(report.get("queue")),"prepare_pending":number(report.get("prepare_pending")),
            "speaking":str(report["speaking"])[:120] if report.get("speaking") else None,
            "ducked":bool(report.get("ducked")),
            "stats":{str(k)[:30]:number(v) for k,v in list((report.get("stats") or {}).items())[:12]} if isinstance(report.get("stats"),dict) else {},
            "last":{k:str(last[k])[:200] for k in ("status","code","message","provider","model","voice","duration_ms")
                    if last.get(k) is not None} if last else None}
    if caps is not None:
        # Phase 4 Agents announce what they can play (docs/TTS_SERVER.md 17.6); older Agents send nothing.
        words=lambda v:[str(x)[:20] for x in v[:10] if isinstance(x,str)] if isinstance(v,list) else []
        out["capabilities"]={"providers":words(caps.get("providers")),"tts_job":number(caps.get("tts_job")),
                             "audio_path":words(caps.get("audio_path"))}
    return out

def _without_paths(verification:dict)->dict:
    """Drop absolute file paths of the Agent's PC before storing (the watched program's name is enough)."""
    state=dict(verification.get("state") or {})
    info=state.get("window_info")
    if isinstance(info,dict) and info.get("exe_path"):
        state["window_info"]={**info,"exe_path":str(info["exe_path"]).replace("\\","/").rsplit("/",1)[-1]}
    return {**verification,"state":state} if verification else verification

# ----- CAPTCHA alert --------------------------------------------------------------------------------
# The Agent never holds the Telegram bot token: the Cloud sends the alert when a heartbeat shows the monitor's status
# change. agent_status.captcha_alert keeps the last alert sent ("detected"/"cleared"), so a heartbeat in between
# (error, window_unavailable) does not send the same alert again.

def captcha_transition(previous:str|None,verification:dict)->tuple[str|None,str|None]:
    """Return (alert state to store, alert kind to send now or None) for one heartbeat."""
    state=verification.get("state") if isinstance(verification,dict) else None
    state=state if isinstance(state,dict) else {}
    status=state.get("status")
    if status=="captcha_detected":
        return ("detected",None) if previous=="detected" else ("detected","captcha_detected")
    if status=="watching" and previous=="detected":
        return "cleared","captcha_cleared"
    if not state.get("running",True) or status in ("idle","stopped","starting"):
        return None,None    # monitor stopped: a CAPTCHA seen after the next start is a new one
    return previous,None

def captcha_message(kind:str,hostname:str,verification:dict,at:str)->str:
    events=verification.get("events") if isinstance(verification.get("events"),list) else []
    detail=next((e.get("detail") for e in events if isinstance(e,dict) and e.get("kind")==kind and e.get("detail")),
                None) or ("Phát hiện CAPTCHA trên TikTok LIVE Studio" if kind=="captcha_detected"
                          else "Không còn thấy mẫu CAPTCHA; chưa xác minh đã giải thành công.")
    return f"Sarae LIVE | {kind}\nMáy: {hostname}\n{detail}\n{at}"

def send_telegram(chat_id:str,text:str):
    token=settings.sarae_telegram_bot_token.strip()
    req=request.Request("https://api.telegram.org/bot"+token+"/sendMessage",
                        data=parse.urlencode({"chat_id":chat_id,"text":text}).encode(),method="POST")
    request.urlopen(req,timeout=8).close()

def _alert(agent_id:str,chat_id:str,text:str):
    try:
        send_telegram(chat_id,text)
    except Exception as exc:  # best effort: a Telegram outage must never affect the Agent channel
        log.warning("Telegram CAPTCHA alert for %s failed: %s",agent_id,str(exc)[:160])

def alert_captcha(agent_id:str,kind:str,hostname:str,verification:dict,at:str)->bool:
    """Send the alert in the background when the OWNER enabled Telegram in the monitor config; True if started."""
    config=verification.get("config") if isinstance(verification.get("config"),dict) else {}
    chat_id=str(config.get("telegram_chat_id") or "").strip()
    if config.get("telegram_enabled") is not True or not chat_id or not settings.sarae_telegram_bot_token.strip():
        return False
    threading.Thread(target=_alert,args=(agent_id,chat_id,captcha_message(kind,hostname,verification,at)),
                     daemon=True,name="sarae-captcha-alert").start()
    return True

@router.post("/heartbeat")
def heartbeat(body:Heartbeat, x_sarae_agent_key:str|None=Header(None)):
    agent=verify_agent(body.agent_id, x_sarae_agent_key)
    at=_now()
    verification=_without_paths(body.verification)
    sanitized={"verification":verification,"obs":body.obs,"local_playback":body.local_playback,
               "playback":body.playback,"media_cache":_media_cache(body.media_cache),"voice":_voice(body.voice),
               "pin": {k:body.pin.get(k) for k in ("status","product_id","card_name","action","error","auto_enabled","browser_open")}
                      if isinstance(body.pin,dict) else None}
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT captcha_alert FROM agent_status WHERE agent_id=?",(body.agent_id,)).fetchone()
        captcha_alert,alert_kind=captcha_transition(row["captcha_alert"] if row else None,verification)
        db.execute("""INSERT INTO agent_status(agent_id, host, last_seen, status_json, captcha_alert)
                      VALUES(?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET
                      host=excluded.host,last_seen=excluded.last_seen,status_json=excluded.status_json,
                      captcha_alert=excluded.captcha_alert""",
                      (body.agent_id,body.hostname,at,json.dumps(sanitized,ensure_ascii=False),captcha_alert))
    if alert_kind:
        alert_captcha(body.agent_id,alert_kind,body.hostname,verification,at)
    # Compare what the Agent really plays with the Cloud's LIVE sessions (e.g. after a reconnect or restart).
    from app.live_sessions import reconcile_playback
    reconcile_playback(agent["owner_id"],body.agent_id,body.playback or {})
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
                owned=registry.execute("SELECT owner_id,hostname,platform FROM registered_agents WHERE id=? AND enabled=1",(aid,)).fetchone()
            if owned is None or (user["role"]!="SUPER_ADMIN" and owned["owner_id"]!=user["id"]):
                continue
        except Exception:
            continue
        age=(now-datetime.fromisoformat(seen)).total_seconds()
        agents.append({"agent_id":aid,"hostname":owned["hostname"],"platform":owned["platform"],"reported_hostname":host,"last_seen":seen,
                       "online":age<ONLINE_SECONDS,"seconds_ago":round(age,1),**json.loads(payload)})
    return {"agents":agents}

# ----- command validation --------------------------------------------------------------------------

NO_PARAM_KINDS={"monitor_start","monitor_stop","obs_status","obs_pause","obs_resume","obs_next","obs_previous",
    "obs_stop","local_media_next","local_media_stop","playback_cancel_pending","playback_pause",
    "playback_resume","playback_next","playback_previous","playback_stop","tiktok_products_sync","media_prepare",
    "tts_clear"}
COMMAND_KINDS=NO_PARAM_KINDS|{"chat_send","chat_probe","PIN_PRODUCT","UNPIN_PRODUCT","GET_PIN_STATE","SET_AUTO_PIN","monitor_config","tts_speak","tts_prepare","playback_policy","local_media_play","local_playlist_start",
    "product_play","video_play","timeline_set","overlay_check","overlay_preview","playback_select","playback_seek","obs_configure","obs_inputs",
    "android_play","android_switch","android_pause","android_resume","android_next","android_previous",
    "android_stop","android_queue_set","android_rotation_set","android_mock_live_start","android_mock_live_stop"}
# Payload fields that must not stay in the command log once the command is final.
SECRET_FIELDS={"password"}

class CommandBody(BaseModel):
    agent_id:str=Field(min_length=1,max_length=80,pattern=r"^[A-Za-z0-9_-]+$")
    kind:str=Field(pattern=r"^("+"|".join(sorted(COMMAND_KINDS))+r")$")
    payload:dict=Field(default_factory=dict)

class AckBody(BaseModel):
    agent_id:str=Field(min_length=1,max_length=80)
    command_id:str=Field(min_length=1,max_length=80)
    claim_token:str=Field(default="",max_length=80)
    success:bool
    # Results carry JSON (e.g. playback status, OBS input list), so allow more than a log line.
    result:str=Field(default="",max_length=65000)

def _media_id(value)->bool:
    """A Sarae Media Library id (32 hex): the Agent plays it from its local media cache."""
    return isinstance(value,str) and len(value)==32 and all(c in "0123456789abcdef" for c in value)

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

_TTS_ID=r"^[A-Za-z0-9_-]{3,64}$"

def _tts_ref(value,name:str)->dict:
    """{utterance_id, job_id} of a G-OmniVoice clip (ids only: the Agent fetches audio through its own channel)."""
    import re
    if not isinstance(value,dict) or not isinstance(value.get("utterance_id"),str) or not isinstance(value.get("job_id"),str) \
            or not re.match(r"^[a-f0-9]{32}$",value["utterance_id"]) or not re.match(_TTS_ID,value["job_id"]):
        raise HTTPException(422,f"{name} không hợp lệ")
    return {"utterance_id":value["utterance_id"],"job_id":value["job_id"]}

def _omnivoice(payload:dict,kind:str)->dict:
    """Phase 4 contract (docs/TTS_SERVER.md 17.6). Never contains a token or a URL."""
    if kind=="tts_speak":
        tts=payload.get("tts")
        if not isinstance(tts,dict):
            raise HTTPException(422,"Thiếu thông tin job G-OmniVoice")
        out={**_tts_ref(tts,"tts"),"voice_rev":_int(tts.get("voice_rev"),"voice_rev",1,10**9)}
        deadline=tts.get("deadline_at")
        try:
            datetime.fromisoformat(deadline)
        except (TypeError,ValueError) as exc:
            raise HTTPException(422,"deadline_at không hợp lệ") from exc
        out["deadline_at"]=deadline
        if tts.get("audio_path") not in ("auto","relay"):
            raise HTTPException(422,"audio_path không hợp lệ")
        out["audio_path"]=tts["audio_path"]
        if payload.get("intro") is not None:
            out["intro"]=_tts_ref(tts.get("intro"),"tts.intro")
        result = {"tts":out}
        result.update(_tts_fallback(payload))
        return result
    refs=payload.get("tts_jobs")
    if not isinstance(refs,list) or len(refs)!=len(payload.get("texts") or []):
        raise HTTPException(422,"tts_jobs phải khớp từng câu chuẩn bị")
    return {"tts_jobs":[_tts_ref(r,"tts_jobs") for r in refs], **_tts_fallback(payload)}

def _tts_fallback(payload):
    result = {}
    policy = payload.get("fallback")
    if policy:
        if not isinstance(policy, dict) or policy.get("mode") not in ("none", "provider"):
            raise HTTPException(422, "fallback không hợp lệ")
        result["fallback"] = {"mode": policy["mode"]}
        if policy["mode"] == "provider":
            spec = _tts_spec(policy.get("provider") or {})
            if spec["provider"] == "omnivoice":
                raise HTTPException(422, "Không fallback sang omnivoice")
            result["fallback"]["provider"] = {**spec, "rate": _int((policy.get("provider") or {}).get("rate", 0), "rate", -30, 50)}
    return result

def _tts_spec(payload:dict)->dict:
    provider=payload.get("provider","edge")
    if provider=="omnivoice":
        import re
        if payload.get("model")!="demo_default" or payload.get("settings") or not isinstance(payload.get("voice"),str) \
                or not re.match(r"^[a-f0-9]{32}$",payload["voice"]):
            raise HTTPException(422,"Payload G-OmniVoice không hợp lệ")
        return {"voice":payload["voice"],"provider":"omnivoice","model":"demo_default","settings":{}}
    if provider != "edge":
        raise HTTPException(422,"Nhà cung cấp TTS không được hỗ trợ")
    voice=payload.get("voice","vi-VN-HoaiMyNeural")
    model=payload.get("model") or "edge-neural"
    if not isinstance(voice,str) or not 1<=len(voice)<=100 or not isinstance(model,str) or not 1<=len(model)<=100:
        raise HTTPException(422,"Voice ID hoặc model ID không hợp lệ")
    if provider=="edge":
        if voice not in ("vi-VN-HoaiMyNeural","vi-VN-NamMinhNeural") or model!="edge-neural":
            raise HTTPException(422,"Giọng Edge TTS không được hỗ trợ")
    options=payload.get("settings") or {}
    if not isinstance(options,dict) or options:
        raise HTTPException(422,"Edge TTS không nhận voice settings")
    settings={}
    out={"voice":voice}
    for name,value in (("provider",provider),("model",model),("settings",settings)):
        if name in payload:
            out[name]=value
    return out

def _pin_target(target:dict)->dict:
    if not isinstance(target,dict):
        raise HTTPException(422,"Thiếu product mapping")
    product_id=target.get("tiktok_product_id")
    if product_id is not None and (not isinstance(product_id,str) or not product_id.isdigit() or len(product_id)>80):
        raise HTTPException(422,"TikTok Product ID không hợp lệ")
    expected_user_id=target.get("expected_user_id")
    if not isinstance(expected_user_id,str) or not expected_user_id.isdigit() or len(expected_user_id)>80:
        raise HTTPException(422,"expected_user_id không hợp lệ")
    image_url=target.get("image_url") or ""
    price_text=target.get("price_text") or ""
    if not isinstance(image_url,str) or len(image_url)>1000:
        raise HTTPException(422,"image_url không hợp lệ")
    if not isinstance(price_text,str) or len(price_text)>80:
        raise HTTPException(422,"price_text không hợp lệ")
    return {"tiktok_product_id":product_id,"name":_text(target.get("name"),"name",1,200),
            "image_url":image_url,"price_text":price_text,"expected_user_id":expected_user_id}

def validate_payload(kind:str,payload:dict|None)->dict:
    """Return the normalized payload for ``kind`` or raise HTTPException(422)."""
    payload=payload or {}
    if kind not in COMMAND_KINDS:raise HTTPException(422,"Lệnh không được hỗ trợ")
    if kind in {"PIN_PRODUCT", "GET_PIN_STATE"}:
        if kind == "GET_PIN_STATE" and not payload:
            return {}
        out = {"target": _pin_target(payload.get("target"))}
        if kind == "PIN_PRODUCT":
            out["session_id"] = _int(payload.get("session_id"),"session_id",1,2**63-1)
            if payload.get("confirmed_token") is not None:
                token = _text(payload["confirmed_token"],"confirmed_token",1,80)
                if not all(c in "0123456789abcdef-" for c in token.lower()):
                    raise HTTPException(422,"confirmed_token không hợp lệ")
                out["confirmed_token"] = token
        return out
    if kind == "UNPIN_PRODUCT":
        uid = payload.get("expected_user_id")
        if not isinstance(uid,str) or not uid.isdigit() or len(uid)>80:
            raise HTTPException(422,"expected_user_id không hợp lệ")
        return {"expected_user_id":uid,"session_id":_int(payload.get("session_id"),"session_id",1,2**63-1)}
    if kind == "SET_AUTO_PIN":
        enabled = _bool(payload.get("enabled"),"enabled")
        session_id = _int(payload.get("session_id"),"session_id",1,2**63-1) if enabled else None
        products = payload.get("products", [])
        if not isinstance(products, list) or len(products)>300:
            raise HTTPException(422,"Danh sách sản phẩm không hợp lệ")
        normalized = []
        for row in products:
            if not isinstance(row, dict): raise HTTPException(422,"Product mapping không hợp lệ")
            normalized.append({"sarae_product_id": _int(row.get("sarae_product_id"),"sarae_product_id",1,2**63-1),
                               **_pin_target(row)})
        return {"enabled": enabled, "session_id": session_id, "products": normalized}
    if kind=="tiktok_products_sync":
        uid=payload.get("expected_user_id")
        if uid is None:
            # Manual Agent-side synchronization is a separate explicit IPC operation.
            raise HTTPException(422,"Thiếu user_id TikTok Account cần xác minh")
        if not isinstance(uid,str) or not uid.isdigit() or len(uid)>80:
            raise HTTPException(422,"expected_user_id không hợp lệ")
        return {"expected_user_id":uid}
    if kind=="tts_speak":
        text=payload.get("text","")
        if not isinstance(text,str) or not 1<=len(text)<=450:
            raise HTTPException(422,"Nội dung TTS từ 1 đến 450 ký tự")
        out={"text":text,**_tts_spec(payload)}
        # Optional (AI voice): speed in percent, seconds after which the Agent no longer speaks it, video ducking.
        if "rate" in payload:out["rate"]=_int(payload["rate"],"rate",-30,50)
        if "ttl_seconds" in payload:
            ttl=payload["ttl_seconds"]
            if isinstance(ttl,bool) or not isinstance(ttl,(int,float)) or not 3<=ttl<=300:
                raise HTTPException(422,"ttl_seconds từ 3 đến 300")
            out["ttl_seconds"]=float(ttl)
        if "duck" in payload:out["duck"]=_bool(payload["duck"],"duck")
        if payload.get("intro") is not None:out["intro"]=_text(payload["intro"],"intro",1,120)
        if out.get("provider")=="omnivoice":
            if out.get("rate",0)!=0:raise HTTPException(422,"G-OmniVoice không nhận rate")
            out.update(_omnivoice(payload,kind))
        return out
    if kind=="chat_send":
        from app.chat_payload import validate_chat_send
        return validate_chat_send(payload)
    if kind=="chat_probe":
        return {}
    if kind=="playback_policy":
        from app.playback_policy import validate_command
        return validate_command(payload)
    if kind=="tts_prepare":
        texts=payload.get("texts")
        if not isinstance(texts,list) or not 1<=len(texts)<=40 or any(not isinstance(t,str) or not 1<=len(t)<=450 for t in texts):
            raise HTTPException(422,"texts: 1-40 câu, mỗi câu 1-450 ký tự")
        out={"texts":texts,**_tts_spec(payload),"rate":_int(payload.get("rate",0),"rate",-30,50)}
        if out.get("provider")=="omnivoice":
            out.update(_omnivoice(payload,kind))
        return out
    if kind=="local_playlist_start":
        return {"media_ids":_media_ids(payload.get("media_ids")),"loop":_bool(payload.get("loop",True),"loop")}
    if kind=="local_media_play":
        if not _media_id(payload.get("media_id")):raise HTTPException(422,"media_id không hợp lệ")
        return {"media_id":payload["media_id"]}
    if kind=="monitor_config":
        from app.verification_monitor import ConfigBody
        return ConfigBody.model_validate(payload).model_dump()
    if kind in ("media_prepare","overlay_check"):
        return {"session_id":_int(payload.get("session_id"),"session_id",1,2**31-1)}
    if kind=="overlay_preview":
        out={"session_id":_int(payload.get("session_id"),"session_id",1,2**31-1),"stop":_bool(payload.get("stop",False),"stop")}
        if out["stop"]:
            return out
        if not _media_id(payload.get("media_id")):raise HTTPException(422,"media_id không hợp lệ")
        return {**out,"timeline_id":_int(payload.get("timeline_id"),"timeline_id",1,2**31-1),"media_id":payload["media_id"],
                "position_ms":_int(payload.get("position_ms",0),"position_ms",0,24*3600*1000),
                "playing":_bool(payload.get("playing",False),"playing"),"seconds":_int(payload.get("seconds",120),"seconds",10,600)}
    if kind=="product_play":
        mode=payload.get("mode","immediate")
        if mode not in ("immediate","after_current"):
            raise HTTPException(422,"mode phải là immediate hoặc after_current")
        return {"session_id":_int(payload.get("session_id"),"session_id",1,2**63-1),
                "product_id":_int(payload.get("product_id"),"product_id",1,2**63-1),
                "media_ids":_media_ids(payload.get("media_ids")),"mode":mode}
    if kind in ("video_play","timeline_set"):
        from app.live_timeline import validate_command as validate_timeline_command
        return validate_timeline_command(kind,payload)
    if kind=="playback_select":
        return {"index":_int(payload.get("index"),"index",0,9999)}
    if kind=="playback_seek":
        return {"ms":_int(payload.get("ms"),"ms",0,24*3600*1000)}
    if kind in ("android_play", "android_switch"):
        if not _media_id(payload.get("media_id")):raise HTTPException(422,"media_id không hợp lệ")
        if kind == "android_play":return {"media_id":payload["media_id"]}
        mode=payload.get("mode","immediate")
        if mode not in ("immediate","after_current"):
            raise HTTPException(422,"mode phải là immediate hoặc after_current")
        return {"media_id":payload["media_id"],"mode":mode}
    if kind=="android_queue_set":
        return {"media_ids":_media_ids(payload.get("media_ids")),"loop":_bool(payload.get("loop",True),"loop"),
                "auto_rotate":_bool(payload.get("auto_rotate",True),"auto_rotate")}
    if kind=="android_rotation_set":
        return {"enabled":_bool(payload.get("enabled"),"enabled")}
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

# ----- lifecycle -------------------------------------------------------------------------------------

def _owner_row(agent_id:str):
    with db_open() as registry:
        row=registry.execute("""SELECT a.owner_id,a.platform,l.enabled AS license_enabled,l.expires_at
            FROM registered_agents a JOIN licenses l ON l.owner_id=a.owner_id
            WHERE a.id=? AND a.enabled=1""",(agent_id,)).fetchone()
    if row is None:raise HTTPException(404,"Không tìm thấy Agent")
    if not row["license_enabled"] or not license_not_expired(row["expires_at"]):
        raise HTTPException(403,"License đã hết hạn hoặc bị khóa")
    return row

def _mask(payload_text:str)->str:
    payload=json.loads(payload_text)
    return json.dumps({k:("***" if k in SECRET_FIELDS and v else v) for k,v in payload.items()},ensure_ascii=False)

def _sweep(db,agent_id:str):
    """Apply time-based transitions for one Agent (call inside a write transaction)."""
    now=_now()
    db.execute("""UPDATE agent_commands SET status='TIMEOUT',error='Agent không nhận lệnh trước khi hết hạn',
        completed_at=?,updated_at=? WHERE agent_id=? AND status='PENDING' AND expires_at<?""",(now,now,agent_id,now))
    for row in db.execute("""SELECT command_id,retryable,attempts,expires_at FROM agent_commands
            WHERE agent_id=? AND status='RUNNING' AND lease_until<?""",(agent_id,now)).fetchall():
        if row["retryable"] and row["attempts"]<MAX_ATTEMPTS and row["expires_at"]>now:
            db.execute("""UPDATE agent_commands SET status='PENDING',lease_until=NULL,updated_at=?
                WHERE command_id=?""",(now,row["command_id"]))
        else:
            db.execute("""UPDATE agent_commands SET status='TIMEOUT',updated_at=?,completed_at=?,
                error='Không nhận được xác nhận từ Agent; lệnh không được gửi lại tự động' WHERE command_id=?""",
                       (now,now,row["command_id"]))
    # Never keep secrets in final commands.
    for row in db.execute("""SELECT command_id,payload FROM agent_commands WHERE agent_id=? AND status IN
            ('SUCCESS','FAILED','TIMEOUT','CANCELLED') AND payload LIKE '%password%'""",(agent_id,)).fetchall():
        db.execute("UPDATE agent_commands SET payload=? WHERE command_id=?",(_mask(row["payload"]),row["command_id"]))

def command_view(row)->dict:
    result=row["result"]
    try:result=json.loads(result) if result else None
    except ValueError:pass
    return {"command_id":row["command_id"],"agent_id":row["agent_id"],"kind":row["kind"],"status":row["status"],
            "attempts":row["attempts"],"result":result,"error":row["error"],"late_ack":bool(row["late_ack"]),
            "session_id":row["session_id"],"created_at":row["created_at"],"started_at":row["started_at"],
            "completed_at":row["completed_at"],"expires_at":row["expires_at"]}

def get_command(command_id:str,agent_id:str|None=None)->dict:
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT * FROM agent_commands WHERE command_id=?",(command_id,)).fetchone()
        if row is None or (agent_id is not None and row["agent_id"]!=agent_id):
            raise HTTPException(404,"Không tìm thấy lệnh")
        _sweep(db,row["agent_id"])
        row=db.execute("SELECT * FROM agent_commands WHERE command_id=?",(command_id,)).fetchone()
        return command_view(row)

def create_command(agent_id:str,owner_id:str,kind:str,payload:dict|None=None,*,idempotency_key:str|None=None,
                   context:dict|None=None,session_id:int|None=None,require_online:bool=True)->dict:
    """Create a command for an Agent owned by `owner_id` (the caller has checked the owner) and return its view.

    With `idempotency_key`, a second call for the same Agent and key returns the first command unchanged.
    `context` is kept server-side (never sent to the Agent) for whoever applies the outcome.
    """
    agent=_owner_row(agent_id)
    if agent["owner_id"]!=owner_id:raise HTTPException(404,"Không tìm thấy Agent")
    if (agent["platform"] == "ANDROID") != kind.startswith("android_"):
        raise HTTPException(422,"Lệnh không tương thích với loại Agent")
    payload=validate_payload(kind,payload)
    if kind in ("tts_speak","tts_prepare") and payload.get("provider")=="omnivoice":
        # Older Agents reject unknown providers and fields (extra="forbid"): never send them this payload.
        from app.tts_orchestrator import supports_omnivoice
        if not supports_omnivoice(agent_id):
            raise HTTPException(409,"Windows Agent chưa hỗ trợ G-OmniVoice (cần cập nhật Agent)",
                                headers={"X-Sarae-Error":"AGENT_NO_OMNIVOICE"})
    if idempotency_key is not None and not 1<=len(idempotency_key)<=120:
        raise HTTPException(422,"Idempotency-Key dài 1-120 ký tự")
    now=_now()
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        if idempotency_key:
            existing=db.execute("SELECT * FROM agent_commands WHERE agent_id=? AND idempotency_key=?",
                                (agent_id,idempotency_key)).fetchone()
            if existing is not None:
                if existing["kind"]!=kind:raise HTTPException(409,"Idempotency-Key đã dùng cho lệnh khác")
                if kind in {"PIN_PRODUCT","UNPIN_PRODUCT","SET_AUTO_PIN"} and (
                    json.loads(existing["payload"])!=payload or existing["session_id"]!=session_id
                ):
                    raise HTTPException(409,"Idempotency-Key đã dùng cho sản phẩm hoặc phiên LIVE khác")
                return command_view(existing)
        if require_online:
            seen=db.execute("SELECT last_seen FROM agent_status WHERE agent_id=?",(agent_id,)).fetchone()
            if not seen or (datetime.now(timezone.utc)-datetime.fromisoformat(seen["last_seen"])).total_seconds()>ONLINE_SECONDS:
                raise HTTPException(409,"Agent đang offline")
        command_id=str(uuid.uuid4())
        db.execute("""INSERT INTO agent_commands(command_id,owner_id,agent_id,kind,lane,payload,context,status,retryable,
            idempotency_key,session_id,created_at,expires_at,updated_at) VALUES(?,?,?,?,?,?,?,'PENDING',?,?,?,?,?,?)""",
            (command_id,owner_id,agent_id,kind,lane(kind),json.dumps(payload,ensure_ascii=False),
             json.dumps(context,ensure_ascii=False) if context else None,int(kind in RETRYABLE_KINDS),
             idempotency_key,session_id,now,_at(ttl_seconds(kind)),now))
        row=db.execute("SELECT * FROM agent_commands WHERE command_id=?",(command_id,)).fetchone()
    _notify(agent_id)
    return command_view(row)

def wait_command(command_id:str,agent_id:str,seconds:float)->dict:
    """Wait up to `seconds` for the command to become final; returns its view either way (never raises on timeout)."""
    entry=_signal(agent_id)
    deadline=time.monotonic()+max(0.0,seconds)
    while True:
        view=get_command(command_id,agent_id)
        if view["status"] in FINAL or time.monotonic()>=deadline:
            apply_outcomes(agent_id)
            return get_command(command_id,agent_id) if view["status"] in FINAL else view
        with entry[0]:
            seen=entry[1]
            entry[0].wait_for(lambda:entry[1]!=seen,timeout=min(0.5,max(0.0,deadline-time.monotonic())))

def run_command(agent_id:str,owner_id:str,kind:str,payload:dict|None=None,wait:float=8,**options)->dict:
    """Create a command and wait briefly for its outcome; the caller decides what a non-final status means."""
    view=create_command(agent_id,owner_id,kind,payload,**options)
    return view if view["status"] in FINAL else wait_command(view["command_id"],agent_id,wait)

def rpc(agent_id:str, owner_id:str, kind:str, payload:dict|None=None, timeout:float=10, **options):
    """For calls whose answer is needed now (OBS input list, status): the Agent's result, or HTTPException —
    502 Agent failure (detail = its message), 504 not done in `timeout` (the command keeps its own lifecycle)."""
    view=run_command(agent_id,owner_id,kind,payload,wait=timeout,**options)
    if view["status"]=="SUCCESS":return view["result"]
    if view["status"]=="FAILED":raise HTTPException(502,view["error"] or "Agent báo lỗi khi thực thi lệnh")
    if view["status"]=="CANCELLED":raise HTTPException(409,"Lệnh đã bị hủy")
    raise HTTPException(504,f"Agent chưa phản hồi kịp (lệnh {view['command_id']}: {view['status']})")

def respond(view:dict,**extra):
    """HTTP answer for a command-backed action: 200 when final, 202 while it is still on its way (poll the command)."""
    if view["status"]=="FAILED":raise HTTPException(502,view["error"] or "Agent báo lỗi khi thực thi lệnh")
    if view["status"]=="TIMEOUT":raise HTTPException(504,view["error"] or "Agent không phản hồi")
    if view["status"]=="CANCELLED":raise HTTPException(409,"Lệnh đã bị hủy")
    body={"command":view,**extra}
    return body if view["status"]=="SUCCESS" else JSONResponse(status_code=202,content=json.loads(json.dumps(body,default=str)))

def cancel_commands(agent_id:str,session_id:int|None=None,command_id:str|None=None)->int:
    """Cancel commands not yet delivered (PENDING) — e.g. when a LIVE session ends, its queued commands are stale."""
    query="UPDATE agent_commands SET status='CANCELLED',completed_at=?,updated_at=? WHERE agent_id=? AND status='PENDING'"
    params=[_now(),_now(),agent_id]
    if session_id is not None:query+=" AND session_id=?";params.append(session_id)
    if command_id is not None:query+=" AND command_id=?";params.append(command_id)
    with _db() as db:
        return db.execute(query,params).rowcount

def _claim(agent_id:str):
    """Atomically hand the oldest deliverable command to the Agent (one RUNNING per lane)."""
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        _sweep(db,agent_id)
        busy={r["lane"] for r in db.execute("SELECT DISTINCT lane FROM agent_commands WHERE agent_id=? AND status='RUNNING'",(agent_id,))}
        row=None
        for candidate in db.execute("""SELECT * FROM agent_commands WHERE agent_id=? AND status='PENDING'
                ORDER BY created_at""",(agent_id,)).fetchall():
            if candidate["lane"] not in busy:
                row=candidate;break
        if row is None:return None
        token=uuid.uuid4().hex
        tokens=json.loads(row["claim_tokens"])+[token]
        now=_now()
        db.execute("""UPDATE agent_commands SET status='RUNNING',attempts=attempts+1,claim_tokens=?,lease_until=?,
            started_at=COALESCE(started_at,?),updated_at=? WHERE command_id=?""",
            (json.dumps(tokens),_at(lease_seconds(row["kind"])),now,now,row["command_id"]))
    return {"command_id":row["command_id"],"kind":row["kind"],"payload":json.loads(row["payload"]),
            "session_id":row["session_id"], "claim_token":token,"attempt":row["attempts"]+1,"expires_at":row["expires_at"]}

@router.get("/commands/{agent_id}/next")
async def next_command(agent_id:str,wait:int=Query(0,ge=0,le=MAX_WAIT_SECONDS),
                       x_sarae_agent_key:str|None=Header(None)):
    """Return the next deliverable command; with ``wait`` > 0, hold the request up to that many
    seconds until one is queued. Waiting costs no worker thread (async sleep on a counter)."""
    await run_in_threadpool(verify_agent,agent_id,x_sarae_agent_key)
    if await run_in_threadpool(agent_platform,agent_id)!="WINDOWS":
        raise HTTPException(403,"Android Agent dùng WebSocket command channel")
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


@router.websocket("/ws/{agent_id}")
async def android_command_socket(websocket: WebSocket, agent_id: str):
    """Persistent command stream for Android Agents; uses the same claim/ack lifecycle as REST Agents."""
    credential=websocket.headers.get("x-sarae-agent-key")
    try:
        agent=await run_in_threadpool(verify_agent,agent_id,credential)
    except HTTPException as exc:
        await websocket.close(code=4401 if exc.status_code==401 else 4403,reason=str(exc.detail)[:120])
        return
    if await run_in_threadpool(agent_platform, agent_id)!="ANDROID":
        await websocket.close(code=4403,reason="ANDROID_AGENT_REQUIRED")
        return
    await websocket.accept()
    next_ping=time.monotonic()
    try:
        while True:
            command=await run_in_threadpool(_claim,agent_id)
            if command:
                await websocket.send_json({"type":"command","command":command})
                while True:
                    message=json.loads(await websocket.receive_text())
                    if message.get("type")=="ack":
                        body=AckBody(agent_id=agent_id,command_id=message.get("command_id",""),
                                     claim_token=message.get("claim_token",""),
                                     success=message.get("success") is True,
                                     result=str(message.get("result", ""))[:65000])
                        await run_in_threadpool(ack_command,body,credential)
                        break
                    if message.get("type")=="pong":continue
            else:
                if time.monotonic()>=next_ping:
                    await websocket.send_json({"type":"ping","server_time":_now()})
                    next_ping=time.monotonic()+15
                try:
                    message=json.loads(await asyncio.wait_for(websocket.receive_text(),timeout=1.0))
                    if message.get("type")=="pong":continue
                except asyncio.TimeoutError:
                    pass
    except WebSocketDisconnect:
        return

@router.post("/commands/ack")
def ack_command(body:AckBody,x_sarae_agent_key:str|None=Header(None)):
    verify_agent(body.agent_id,x_sarae_agent_key)
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("SELECT * FROM agent_commands WHERE command_id=? AND agent_id=?",
                       (body.command_id,body.agent_id)).fetchone()
        if row is None:raise HTTPException(404,"Không tìm thấy lệnh của Agent này")
        if body.claim_token not in json.loads(row["claim_tokens"]):
            raise HTTPException(409,"Claim token không hợp lệ cho lệnh này")
        status="SUCCESS" if body.success else "FAILED"
        if row["status"] in ("SUCCESS","FAILED"):
            # Duplicate ack (network retry): the first outcome stands.
            return {"ok":True,"status":row["status"],"duplicate":True}
        if row["status"]=="CANCELLED":raise HTTPException(409,"Lệnh đã bị hủy")
        current=json.loads(row["claim_tokens"])[-1]==body.claim_token
        late=row["status"]=="TIMEOUT" or (row["status"] in ("PENDING","RUNNING") and not current)
        now=_now()
        db.execute("""UPDATE agent_commands SET status=?,result=?,error=?,late_ack=?,lease_until=NULL,completed_at=?,
            updated_at=? WHERE command_id=?""",
            (status,body.result if body.success else None,None if body.success else (body.result or "Agent báo lỗi")[:1900],
             int(late),now,now,body.command_id))
        _sweep(db,body.agent_id)
        final=db.execute("SELECT * FROM agent_commands WHERE command_id=?",(body.command_id,)).fetchone()
    del final
    _notify(body.agent_id)
    apply_outcomes(body.agent_id)
    return {"ok":True,"status":status,"late":late}

def apply_outcomes(agent_id:str)->int:
    """Let the module that created a command apply its final outcome (e.g. a LIVE session becomes RUNNING) — exactly
    once per final status, including TIMEOUTs found by the sweep and late acks that turn a TIMEOUT into SUCCESS."""
    from app.live_sessions import apply_command_outcome
    from app.comment_reply import apply_outcome as apply_comment_outcome
    applied=0
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        _sweep(db,agent_id)
        rows=db.execute("""SELECT * FROM agent_commands WHERE agent_id=? AND context IS NOT NULL AND status IN
            ('SUCCESS','FAILED','TIMEOUT','CANCELLED') AND (applied_status IS NULL OR applied_status!=status)
            ORDER BY completed_at""",(agent_id,)).fetchall()
        for row in rows:
            db.execute("UPDATE agent_commands SET applied_status=? WHERE command_id=?",(row["status"],row["command_id"]))
    for row in rows:
        context=json.loads(row["context"])
        if context.get("module")=="ai_comment":
            apply_comment_outcome(command_view(row),context)
        else:
            apply_command_outcome(command_view(row),context)
        applied+=1
    return applied

# ----- OWNER-facing command API --------------------------------------------------------------------

class OwnerCommandBody(BaseModel):
    kind:str=Field(pattern=r"^("+"|".join(sorted(COMMAND_KINDS))+r")$")
    payload:dict=Field(default_factory=dict)
    idempotency_key:str|None=Field(default=None,min_length=1,max_length=120)
    wait:float=Field(default=0,ge=0,le=10)

def _owner_agent(authorization:str|None,agent_id:str)->dict:
    user=auth(authorization)
    if user["role"]=="SUPER_ADMIN":
        raise HTTPException(403,"SUPER_ADMIN không trực tiếp điều khiển LIVE khách hàng")
    from app.owner_agents import owned_agent
    owned_agent(user,agent_id)
    return user

@router.post("/{agent_id}/commands")
def owner_create_command(agent_id:str,body:OwnerCommandBody,authorization:str|None=Header(None),
                         idempotency_key:str|None=Header(None,alias="Idempotency-Key")):
    user=_owner_agent(authorization,agent_id)
    view=run_command(agent_id,user["id"],body.kind,body.payload,wait=body.wait,
                     idempotency_key=body.idempotency_key or idempotency_key)
    return view if view["status"] in FINAL else JSONResponse(status_code=202,content=view)

@router.get("/{agent_id}/commands/{command_id}")
def owner_get_command(agent_id:str,command_id:str,authorization:str|None=Header(None)):
    _owner_agent(authorization,agent_id)
    return get_command(command_id,agent_id)

@router.post("/{agent_id}/commands/{command_id}/cancel")
def owner_cancel_command(agent_id:str,command_id:str,authorization:str|None=Header(None)):
    _owner_agent(authorization,agent_id)
    if not cancel_commands(agent_id,command_id=command_id):
        raise HTTPException(409,"Chỉ hủy được lệnh chưa được Agent nhận")
    return get_command(command_id,agent_id)

@router.get("/{agent_id}/commands")
def owner_list_commands(agent_id:str,authorization:str|None=Header(None)):
    _owner_agent(authorization,agent_id)
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        _sweep(db,agent_id)
        rows=db.execute("SELECT * FROM agent_commands WHERE agent_id=? ORDER BY created_at DESC LIMIT 30",(agent_id,)).fetchall()
    return {"commands":[command_view(r) for r in rows]}

# Older Agent page endpoints (AgentCloudStatus.tsx), kept on top of the lifecycle API.
@router.post("/commands")
def queue_command(body:CommandBody, authorization:str|None=Header(None)):
    user=_owner_agent(authorization,body.agent_id)
    view=create_command(body.agent_id,user["id"],body.kind,body.payload)
    return {"command_id":view["command_id"],"state":view["status"].lower(),"status":view["status"]}

@router.get("/commands")
def list_commands(agent_id:str,authorization:str|None=Header(None)):
    user=auth(authorization)
    with db_open() as registry:
        row=registry.execute("SELECT owner_id FROM registered_agents WHERE id=? AND enabled=1",(agent_id,)).fetchone()
    if row is None:raise HTTPException(404,"Không tìm thấy Agent")
    allow_owner(authorization,row["owner_id"])
    del user
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        _sweep(db,agent_id)
        rows=db.execute("SELECT * FROM agent_commands WHERE agent_id=? ORDER BY created_at DESC LIMIT 30",(agent_id,)).fetchall()
    return {"commands":[{**command_view(r),"state":r["status"].lower(),"updated_at":r["updated_at"]} for r in rows]}

# ----- Agent events --------------------------------------------------------------------------------

class AgentEvent(BaseModel):
    seq:int=Field(ge=0)
    type:Literal["tts_playback","PRODUCT_SWITCHED","PLAYBACK_ENDED","ERROR",
                 # Single Video Timeline pin scheduler (agent-core sarae_agent/timeline/scheduler.py)
                 "TIMELINE_LOADED","TIMELINE_STOPPED","TIMELINE_RESUMED","PIN_APPLIED","PIN_FAILED",
                 "PIN_DRIFT","MANUAL_OVERRIDE","LIVE_GATE_CHANGED"]
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
    for event in body.events:
        if event.type == "tts_playback":
            from app.tts_orchestrator import playback_event
            playback_event(body.agent_id, event.detail)
    return {"accepted":accepted,"last_seq":max((e.seq for e in body.events),default=None)}

def pop_events(agent_id:str,limit:int=500)->list[dict]:
    """Unprocessed events of ``agent_id`` in seq order; they are marked processed atomically."""
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        rows=db.execute("""SELECT seq,payload,created_at FROM agent_events
            WHERE agent_id=? AND processed=0 ORDER BY seq LIMIT ?""",(agent_id,limit)).fetchall()
        if rows:
            db.executemany("UPDATE agent_events SET processed=1 WHERE agent_id=? AND seq=?",
                           [(agent_id,r["seq"]) for r in rows])
    return [{**json.loads(r["payload"]),"seq":r["seq"],"created_at":r["created_at"]} for r in rows]
