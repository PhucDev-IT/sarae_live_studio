"""Cloud-side registry for Windows Local Agents.

Agent initiates HTTPS connections; no inbound Windows firewall rule is needed.
V1 provides remote observability only; privileged local OBS commands are not exposed.
"""
import hmac
import sqlite3
from datetime import datetime, timezone
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
    return db

def authorized(actual, expected):
    return bool(expected) and hmac.compare_digest(actual or "",expected)

class Heartbeat(BaseModel):
    agent_id:str=Field(min_length=1,max_length=80,pattern=r"^[A-Za-z0-9_-]+$")
    hostname:str=Field(min_length=1,max_length=160)
    verification:dict=Field(default_factory=dict)

@router.post("/heartbeat")
def heartbeat(body:Heartbeat, x_sarae_agent_key:str|None=Header(None)):
    if not authorized(x_sarae_agent_key, settings.sarae_agent_key):
        raise HTTPException(401,"Agent key không hợp lệ")
    import json
    at=datetime.now(timezone.utc).isoformat()
    sanitized={"verification":body.verification}
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
