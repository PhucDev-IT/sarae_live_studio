"""SaaS foundation: two roles, ownership, hashed passwords and activation credentials.

Separate registry from legacy local media DB so existing single-user work stays intact.
"""
import hashlib
import json
import hmac
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException
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

@contextmanager
def db_open():
    """Connection that commits (or rolls back) and is always closed; sqlite3's own `with` never closes."""
    db=sqlite3.connect(DB,timeout=10)
    try:
        with db:
            yield _prepare(db)
    finally:
        db.close()

def _prepare(db):
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
    columns={row[1] for row in db.execute("PRAGMA table_info(registered_agents)")}
    if "platform" not in columns:
        db.execute("ALTER TABLE registered_agents ADD COLUMN platform TEXT NOT NULL DEFAULT 'WINDOWS'")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_super_admin ON users(role) WHERE role='SUPER_ADMIN'""")
    db.execute("""CREATE TABLE IF NOT EXISTS user_sessions (
        token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires_at TEXT NOT NULL)""")
    # Per-OWNER preferences (JSON, nothing secret): AI configuration, AI batching… (never which Agent is in use)
    db.execute("""CREATE TABLE IF NOT EXISTS owner_settings (
        owner_id TEXT PRIMARY KEY,data TEXT NOT NULL DEFAULT '{}')""")
    return db

def owner_settings(owner_id:str)->dict:
    with db_open() as db:
        row=db.execute("SELECT data FROM owner_settings WHERE owner_id=?",(owner_id,)).fetchone()
    return json.loads(row["data"]) if row else {}

def update_owner_settings(owner_id:str,**values)->dict:
    """Merge `values` into the owner's settings (a None value removes the key)."""
    with db_open() as db:
        row=db.execute("SELECT data FROM owner_settings WHERE owner_id=?",(owner_id,)).fetchone()
        data=json.loads(row["data"]) if row else {}
        for key,value in values.items():
            if value is None:data.pop(key,None)
            else:data[key]=value
        db.execute("""INSERT INTO owner_settings(owner_id,data) VALUES(?,?)
            ON CONFLICT(owner_id) DO UPDATE SET data=excluded.data""",(owner_id,json.dumps(data,ensure_ascii=False)))
    return data

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

def current_owner(authorization:str|None=Header(None))->dict:
    """FastAPI dependency for customer data (products, videos, LIVE…): the logged-in OWNER.

    SUPER_ADMIN manages accounts, licenses and Agents only; it never reads or changes an OWNER's content.
    Every query on customer tables must filter by the returned user's "id".
    """
    user=auth(authorization)
    if user["role"]!="OWNER":raise HTTPException(403,"Chức năng này dành cho tài khoản OWNER")
    return user

def allow_owner(token:str|None,owner_id:str):
    user=auth(token)
    if user["role"]!="SUPER_ADMIN" and user["id"]!=owner_id:
        raise HTTPException(403,"Không có quyền với Agent này")
    return user

def require_admin(token):
    user=auth(token)
    if user["role"]!="SUPER_ADMIN":raise HTTPException(403,"Chỉ SUPER_ADMIN")
    return user

def agent_error(status:int,detail:str,code:str)->HTTPException:
    """Error for Agent-facing endpoints: `detail` stays human text, X-Sarae-Error carries a stable code the Agent
    (agent-core cloud/client.py) acts on, e.g. AGENT_REVOKED → stop and ask to re-activate."""
    return HTTPException(status,detail,headers={"X-Sarae-Error":code})

def verify_agent(agent_id:str,credential:str|None):
    if not credential:raise agent_error(401,"Agent chưa kích hoạt","AGENT_NOT_ACTIVATED")
    with db_open() as db:
        row=db.execute("""SELECT a.id,a.owner_id,a.enabled AS agent_enabled,u.active AS owner_active,
          l.enabled,l.expires_at
          FROM registered_agents a JOIN licenses l ON l.owner_id=a.owner_id
          JOIN users u ON u.id=a.owner_id
          WHERE a.id=? AND a.credential_hash=?""",
          (agent_id,secret_hash(credential))).fetchone()
    if not row:code="AGENT_UNAUTHORIZED"
    elif not row["agent_enabled"]:code="AGENT_REVOKED"
    # A locked OWNER account blocks its Agents like a locked license.
    elif not row["owner_active"] or not row["enabled"] or not license_not_expired(row["expires_at"]):
        code="LICENSE_EXPIRED"
    else:
        return {k:row[k] for k in ("id","owner_id","enabled","expires_at")}
    raise agent_error(403,"Agent không có license hợp lệ",code)

def agent_platform(agent_id:str)->str|None:
    """Read platform after verify_agent, without changing the legacy verification result shape."""
    with db_open() as db:
        row=db.execute("SELECT platform FROM registered_agents WHERE id=?",(agent_id,)).fetchone()
    return row["platform"] if row else None

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
    platform:Literal["WINDOWS","ANDROID"]="WINDOWS"

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

@router.get("/account")
def my_account(owner:dict=Depends(current_owner),authorization:str|None=Header(None)):
    """The OWNER's own account: license, device slots used and sign-in sessions (read-only; SUPER_ADMIN edits licenses)."""
    with db_open() as db:
        lic=db.execute("SELECT max_agents,expires_at,enabled FROM licenses WHERE owner_id=?",(owner["id"],)).fetchone()
        used=db.execute("SELECT COUNT(*) FROM registered_agents WHERE owner_id=? AND enabled=1",(owner["id"],)).fetchone()[0]
        sessions=db.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id=? AND expires_at>?",(owner["id"],now())).fetchone()[0]
        current=db.execute("SELECT expires_at FROM user_sessions WHERE token_hash=?",(secret_hash(authorization[7:]),)).fetchone()
    license_={"max_agents":lic["max_agents"],"expires_at":lic["expires_at"],"enabled":bool(lic["enabled"]),
              "valid":bool(lic["enabled"]) and license_not_expired(lic["expires_at"])} if lic else None
    return {"id":owner["id"],"email":owner["email"],"role":owner["role"],"license":license_,"agents_used":used,
            "sessions":sessions,"session_expires_at":current["expires_at"] if current else None}

class PasswordChange(BaseModel):
    current_password:str=Field(min_length=1,max_length=256)
    new_password:str=Field(min_length=8,max_length=256)

@router.post("/password")
def change_password(body:PasswordChange,authorization:str|None=Header(None)):
    """Change one's own password; every other sign-in of the account is signed out, this one stays."""
    user=auth(authorization)
    with db_open() as db:
        row=db.execute("SELECT password_hash,salt FROM users WHERE id=?",(user["id"],)).fetchone()
        if not hmac.compare_digest(row["password_hash"],pass_hash(body.current_password,row["salt"])):
            raise HTTPException(400,"Mật khẩu hiện tại không đúng")
        if body.new_password==body.current_password:
            raise HTTPException(422,"Mật khẩu mới phải khác mật khẩu hiện tại")
        salt=secrets.token_hex(16)
        db.execute("UPDATE users SET password_hash=?,salt=? WHERE id=?",(pass_hash(body.new_password,salt),salt,user["id"]))
        db.execute("DELETE FROM user_sessions WHERE user_id=? AND token_hash<>?",(user["id"],secret_hash(authorization[7:])))
    return {"ok":True}

@router.post("/sessions/revoke-others")
def revoke_other_sessions(authorization:str|None=Header(None)):
    """Sign out every other browser / device of the account (this one stays signed in)."""
    user=auth(authorization)
    with db_open() as db:
        count=db.execute("DELETE FROM user_sessions WHERE user_id=? AND token_hash<>?",
                         (user["id"],secret_hash(authorization[7:]))).rowcount
    return {"revoked":count}

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
        row=db.execute("""SELECT k.owner_id,k.used_at,l.max_agents,l.expires_at,l.enabled
          FROM activation_keys k JOIN licenses l ON l.owner_id=k.owner_id
          JOIN users u ON u.id=k.owner_id
          WHERE k.key_hash=? AND u.active=1""",
          (secret_hash(body.activation_key),)).fetchone()
        if row is None or row["used_at"] is not None:
            raise agent_error(403,"Mã kích hoạt không hợp lệ hoặc đã sử dụng",
                              "ACTIVATION_KEY_INVALID" if row is None else "ACTIVATION_KEY_USED")
        if not row["enabled"] or not license_not_expired(row["expires_at"]):
            raise agent_error(403,"License đã hết hạn hoặc bị khóa","LICENSE_EXPIRED")
        count=db.execute("SELECT COUNT(*) FROM registered_agents WHERE owner_id=? AND enabled=1",
                         (row["owner_id"],)).fetchone()[0]
        if count>=row["max_agents"]:raise agent_error(409,"Đã đạt giới hạn Agent của license","AGENT_LIMIT_REACHED")
        aid="agt_"+uuid.uuid4().hex
        credential=secrets.token_urlsafe(40)
        db.execute("""INSERT INTO registered_agents(id,owner_id,hostname,credential_hash,enabled,created_at,platform)
                    VALUES(?,?,?,?,1,?,?)""",
                   (aid,row["owner_id"],body.hostname,secret_hash(credential),now(),body.platform))
        db.execute("UPDATE activation_keys SET used_at=? WHERE key_hash=?",
                   (now(),secret_hash(body.activation_key)))
    return {"agent_id":aid,"agent_credential":credential,"owner_id":row["owner_id"],"platform":body.platform}

@router.get("/agents")
def my_agents(authorization:str|None=Header(None)):
    user=auth(authorization)
    with db_open() as db:
        if user["role"]=="SUPER_ADMIN":
            rows=db.execute("SELECT id,owner_id,hostname,enabled,created_at,platform FROM registered_agents").fetchall()
        else:
            rows=db.execute("SELECT id,owner_id,hostname,enabled,created_at,platform FROM registered_agents WHERE owner_id=?",
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
    from app.runtime import release_agent
    release_agent(row["owner_id"],agent_id)
    return {"revoked":agent_id}
