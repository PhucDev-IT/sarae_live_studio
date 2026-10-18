"""SaaS foundation: two roles, ownership, hashed passwords and activation credentials.

Separate registry from legacy local media DB so existing single-user work stays intact.
"""
import hashlib
import hmac
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from app.database import DATA_DIR

router=APIRouter(prefix="/api/identity",tags=["SaaS identity"])
DB=DATA_DIR/"identity.sqlite3"

def now():
    return datetime.now(timezone.utc).isoformat()

def license_not_expired(expires_at:str|None)->bool:
    if not expires_at:return True
    try:
        expires=datetime.fromisoformat(expires_at.replace("Z","+00:00"))
        if expires.tzinfo is None:expires=expires.replace(tzinfo=timezone.utc)
        return expires>datetime.now(timezone.utc)
    except ValueError:
        return False

def secret_hash(value:str):
    return hashlib.sha256(value.encode()).hexdigest()

def pass_hash(password:str,salt:str):
    return hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(salt),310000).hex()

def db_open():
    db=sqlite3.connect(DB,timeout=10)
    db.row_factory=sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('SUPER_ADMIN','OWNER')),
        active INTEGER NOT NULL DEFAULT 1)""")
    db.execute("""CREATE TABLE IF NOT EXISTS licenses (
        owner_id TEXT PRIMARY KEY,expires_at TEXT,max_agents INTEGER NOT NULL DEFAULT 1,
        enabled INTEGER NOT NULL DEFAULT 1)""")
    db.execute("""CREATE TABLE IF NOT EXISTS activation_keys (
        key_hash TEXT PRIMARY KEY,owner_id TEXT NOT NULL,used_at TEXT,
        created_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS registered_agents (
        id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,hostname TEXT NOT NULL,
        credential_hash TEXT UNIQUE NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL)""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_super_admin ON users(role) WHERE role='SUPER_ADMIN'""")
    db.execute("""CREATE TABLE IF NOT EXISTS user_sessions (
        token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires_at TEXT NOT NULL)""")
    return db

def auth(token:str|None):
    if not token or not token.startswith("Bearer "):
        raise HTTPException(401,"Cần đăng nhập")
    with db_open() as db:
        row=db.execute("""SELECT u.id,u.email,u.role FROM user_sessions s
          JOIN users u ON u.id=s.user_id
          WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""",
          (secret_hash(token[7:]),now())).fetchone()
    if row is None:raise HTTPException(401,"Phiên đăng nhập hết hạn")
    return dict(row)

def allow_owner(token:str|None,owner_id:str):
    user=auth(token)
    if user["role"]!="SUPER_ADMIN" and user["id"]!=owner_id:
        raise HTTPException(403,"Không có quyền với Agent này")
    return user

def require_admin(token):
    user=auth(token)
    if user["role"]!="SUPER_ADMIN":raise HTTPException(403,"Chỉ SUPER_ADMIN")
    return user

def verify_agent(agent_id:str,credential:str|None):
    if not credential:raise HTTPException(401,"Agent chưa kích hoạt")
    with db_open() as db:
        row=db.execute("""SELECT a.id,a.owner_id,l.enabled,l.expires_at
          FROM registered_agents a JOIN licenses l ON l.owner_id=a.owner_id
          JOIN users u ON u.id=a.owner_id
          WHERE a.id=? AND a.credential_hash=? AND a.enabled=1 AND u.active=1""",
          (agent_id,secret_hash(credential))).fetchone()
    if not row or not row["enabled"] or not license_not_expired(row["expires_at"]):
        raise HTTPException(403,"Agent không có license hợp lệ")
    return dict(row)

class Login(BaseModel):
    email:str
    password:str

class CreateOwner(BaseModel):
    email:str=Field(min_length=5,max_length=254)
    password:str=Field(min_length=6,max_length=256)
    max_agents:int=Field(default=1,ge=1,le=100)
    expires_at:str|None=None

class Activate(BaseModel):
    activation_key:str=Field(min_length=20,max_length=200)
    hostname:str=Field(min_length=1,max_length=160)

class AgentCheck(BaseModel):
    agent_id:str
    credential:str

@router.post("/login")
def login(body:Login):
    with db_open() as db:
        row=db.execute("SELECT * FROM users WHERE email=? AND active=1",(body.email.strip().lower(),)).fetchone()
        if row is None or not hmac.compare_digest(row["password_hash"],pass_hash(body.password,row["salt"])):
            raise HTTPException(401,"Sai thông tin đăng nhập")
        token=secrets.token_urlsafe(40)
        expiry=(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat()
        db.execute("INSERT INTO user_sessions VALUES(?,?,?)",(secret_hash(token),row["id"],expiry))
    return {"access_token":token,"token_type":"bearer","expires_at":expiry,"role":row["role"],"user_id":row["id"]}

@router.get("/me")
def me(authorization:str|None=Header(None)):
    return auth(authorization)

@router.post("/logout")
def logout(authorization:str|None=Header(None)):
    auth(authorization)
    with db_open() as db:
        db.execute("DELETE FROM user_sessions WHERE token_hash=?",(secret_hash(authorization[7:]),))
    return {"ok":True}

@router.get("/platform/summary")
def platform_summary(authorization:str|None=Header(None)):
    require_admin(authorization)
    with db_open() as db:
        owners_total=db.execute("SELECT COUNT(*) FROM users WHERE role='OWNER'").fetchone()[0]
        owners_active=db.execute("SELECT COUNT(*) FROM users WHERE role='OWNER' AND active=1").fetchone()[0]
        licenses=db.execute("SELECT enabled,expires_at FROM licenses").fetchall()
        agents=db.execute("SELECT id,enabled FROM registered_agents").fetchall()
    enabled=sum(1 for license in licenses if license["enabled"] and license_not_expired(license["expires_at"]))
    upcoming=datetime.now(timezone.utc)+timedelta(days=7)
    expiring=0
    for license in licenses:
        if license["enabled"] and license_not_expired(license["expires_at"]) and license["expires_at"]:
            expiry=datetime.fromisoformat(license["expires_at"].replace("Z","+00:00"))
            if expiry.tzinfo is None:expiry=expiry.replace(tzinfo=timezone.utc)
            if expiry<=upcoming:expiring+=1
    from app.cloud_agents import connection
    with connection() as db:
        last_seen={row[0]:row[1] for row in db.execute("SELECT agent_id,last_seen FROM agent_status")}
    online=0
    for agent in agents:
        if not agent["enabled"]:continue
        seen=last_seen.get(agent["id"])
        if seen:
            try:
                at=datetime.fromisoformat(seen.replace("Z","+00:00"))
                if (datetime.now(timezone.utc)-at).total_seconds()<35:online+=1
            except (ValueError,TypeError):pass
    active_agents=sum(1 for agent in agents if agent["enabled"])
    return {"owners_total":owners_total,"owners_active":owners_active,
            "licenses_enabled":enabled,"licenses_expiring_7d":expiring,
            "agents_total":active_agents,"agents_online":online,
            "agents_offline":active_agents-online,
            "agents_revoked":len(agents)-active_agents}

@router.post("/owners")
def create_owner(body:CreateOwner,authorization:str|None=Header(None)):
    require_admin(authorization)
    salt=secrets.token_hex(16);uid=str(uuid.uuid4())
    with db_open() as db:
        try:
            db.execute("INSERT INTO users VALUES(?,?,?,?,?,1)",
                       (uid,body.email.strip().lower(),pass_hash(body.password,salt),salt,"OWNER"))
            db.execute("INSERT INTO licenses VALUES(?,?,?,1)",(uid,body.expires_at,body.max_agents))
        except sqlite3.IntegrityError:
            raise HTTPException(409,"Email đã tồn tại")
    return {"id":uid,"role":"OWNER","email":body.email.strip().lower()}

@router.get("/owners")
def owners(authorization:str|None=Header(None)):
    require_admin(authorization)
    with db_open() as db:
        return [dict(r) for r in db.execute("""SELECT u.id,u.email,u.active,
            l.expires_at,l.max_agents,l.enabled FROM users u
            JOIN licenses l ON l.owner_id=u.id ORDER BY u.email""").fetchall()]

@router.post("/owners/{owner_id}/activation-keys")
def issue_key(owner_id:str,authorization:str|None=Header(None)):
    require_admin(authorization)
    key="SARAE-ACT-"+secrets.token_urlsafe(30)
    with db_open() as db:
        if not db.execute("SELECT 1 FROM licenses WHERE owner_id=?",(owner_id,)).fetchone():
            raise HTTPException(404,"Không tìm thấy OWNER")
        db.execute("INSERT INTO activation_keys VALUES(?,?,NULL,?)",(secret_hash(key),owner_id,now()))
    return {"activation_key":key,"note":"Mã chỉ hiện một lần"}

@router.post("/activate")
def activate(body:Activate):
    with db_open() as db:
        db.execute("BEGIN IMMEDIATE")
        row=db.execute("""SELECT k.owner_id,l.max_agents,l.expires_at,l.enabled
          FROM activation_keys k JOIN licenses l ON l.owner_id=k.owner_id
          JOIN users u ON u.id=k.owner_id
          WHERE k.key_hash=? AND k.used_at IS NULL AND u.active=1""",
          (secret_hash(body.activation_key),)).fetchone()
        if row is None:raise HTTPException(403,"Mã kích hoạt không hợp lệ hoặc đã sử dụng")
        if not row["enabled"] or not license_not_expired(row["expires_at"]):
            raise HTTPException(403,"License đã hết hạn hoặc bị khóa")
        count=db.execute("SELECT COUNT(*) FROM registered_agents WHERE owner_id=? AND enabled=1",
                         (row["owner_id"],)).fetchone()[0]
        if count>=row["max_agents"]:raise HTTPException(409,"Đã đạt giới hạn Agent của license")
        aid="agt_"+uuid.uuid4().hex
        credential=secrets.token_urlsafe(40)
        db.execute("INSERT INTO registered_agents VALUES(?,?,?,?,1,?)",
                   (aid,row["owner_id"],body.hostname,secret_hash(credential),now()))
        db.execute("UPDATE activation_keys SET used_at=? WHERE key_hash=?",
                   (now(),secret_hash(body.activation_key)))
    return {"agent_id":aid,"agent_credential":credential,"owner_id":row["owner_id"]}

@router.get("/agents")
def my_agents(authorization:str|None=Header(None)):
    user=auth(authorization)
    with db_open() as db:
        if user["role"]=="SUPER_ADMIN":
            rows=db.execute("SELECT id,owner_id,hostname,enabled,created_at FROM registered_agents").fetchall()
        else:
            rows=db.execute("SELECT id,owner_id,hostname,enabled,created_at FROM registered_agents WHERE owner_id=?",
                            (user["id"],)).fetchall()
        return [dict(r) for r in rows]

def bootstrap_admin(email:str,password:str):
    with db_open() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM users WHERE role='SUPER_ADMIN'").fetchone():
            raise RuntimeError("SUPER_ADMIN đã tồn tại")
        salt=secrets.token_hex(16)
        db.execute("INSERT INTO users VALUES(?,?,?,?,?,1)",
                   (str(uuid.uuid4()),email.strip().lower(),pass_hash(password,salt),salt,"SUPER_ADMIN"))

class LicenseUpdate(BaseModel):
    max_agents:int=Field(ge=1,le=100)
    expires_at:str|None=None
    enabled:bool=True

class AgentRename(BaseModel):
    hostname:str=Field(min_length=1,max_length=160)

@router.put("/owners/{owner_id}/license")
def update_license(owner_id:str,body:LicenseUpdate,authorization:str|None=Header(None)):
    require_admin(authorization)
    with db_open() as db:
        count=db.execute("UPDATE licenses SET max_agents=?,expires_at=?,enabled=? WHERE owner_id=?",
                         (body.max_agents,body.expires_at,int(body.enabled),owner_id)).rowcount
    if not count:raise HTTPException(404,"Không tìm thấy license")
    return {"ok":True}

@router.patch("/agents/{agent_id}")
def rename_agent(agent_id:str,body:AgentRename,authorization:str|None=Header(None)):
    with db_open() as db:
        row=db.execute("SELECT owner_id FROM registered_agents WHERE id=?",(agent_id,)).fetchone()
        if row is None:raise HTTPException(404,"Không tìm thấy Agent")
        allow_owner(authorization,row["owner_id"])
        db.execute("UPDATE registered_agents SET hostname=? WHERE id=?",
                   (body.hostname.strip(),agent_id))
    return {"ok":True}

@router.delete("/agents/{agent_id}")
def revoke_agent(agent_id:str,authorization:str|None=Header(None)):
    with db_open() as db:
        row=db.execute("SELECT owner_id FROM registered_agents WHERE id=?",(agent_id,)).fetchone()
        if row is None:raise HTTPException(404,"Không tìm thấy Agent")
        allow_owner(authorization,row["owner_id"])
        db.execute("UPDATE registered_agents SET enabled=0 WHERE id=?",(agent_id,))
    return {"revoked":agent_id}
