"""Customer accounts, licenses and activated devices (docs/HYBRID_STUDIO_SERVER.md).

SQLite at DATA_DIR/identity.sqlite3. A customer (users row, role OWNER) has one license (device limit, expiry,
enabled) and one-time activation keys; activating a key registers a device with its own credential. Only hashes of
keys and credentials are stored. Customers never sign in with a password: the random one exists only because the
column is required.

Used by the gateway (activation, device auth), the admin page and the CLI tools.studio_admin.
"""
import hashlib
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.database import DATA_DIR

DB = DATA_DIR / "identity.sqlite3"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def license_not_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return True
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > datetime.now(timezone.utc)
    except ValueError:
        return False


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def pass_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310000).hex()


@contextmanager
def db_open():
    """Connection that commits (or rolls back) and is always closed; sqlite3's own `with` never closes."""
    db = sqlite3.connect(DB, timeout=10)
    try:
        with db:
            yield _prepare(db)
    finally:
        db.close()


def _prepare(db):
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
        salt TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('SUPER_ADMIN','OWNER')),
        active INTEGER NOT NULL DEFAULT 1)""")
    db.execute("""CREATE TABLE IF NOT EXISTS licenses (
        owner_id TEXT PRIMARY KEY, expires_at TEXT, max_agents INTEGER NOT NULL DEFAULT 1,
        enabled INTEGER NOT NULL DEFAULT 1)""")
    db.execute("""CREATE TABLE IF NOT EXISTS activation_keys (
        key_hash TEXT PRIMARY KEY, owner_id TEXT NOT NULL, used_at TEXT, created_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS registered_agents (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, hostname TEXT NOT NULL,
        credential_hash TEXT UNIQUE NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'WINDOWS')""")
    return db


def agent_error(status: int, detail: str, code: str) -> HTTPException:
    """Device-facing error: `detail` stays human text, X-Sarae-Error carries the stable code the Studio acts on
    (agent-core studio/account.py), e.g. AGENT_REVOKED -> forget the credential and ask for a new key."""
    return HTTPException(status, detail, headers={"X-Sarae-Error": code})


def verify_agent(agent_id: str, credential: str | None) -> dict:
    if not credential:
        raise agent_error(401, "Máy chưa kích hoạt", "AGENT_NOT_ACTIVATED")
    with db_open() as db:
        row = db.execute("""SELECT a.id, a.owner_id, a.enabled AS agent_enabled, u.active AS owner_active,
            l.enabled, l.expires_at
            FROM registered_agents a JOIN licenses l ON l.owner_id=a.owner_id
            JOIN users u ON u.id=a.owner_id
            WHERE a.id=? AND a.credential_hash=?""", (agent_id, secret_hash(credential))).fetchone()
    if not row:
        code = "AGENT_UNAUTHORIZED"
    elif not row["agent_enabled"]:
        code = "AGENT_REVOKED"
    elif not row["owner_active"] or not row["enabled"] or not license_not_expired(row["expires_at"]):
        code = "LICENSE_EXPIRED"     # a locked account blocks its devices like a locked license
    else:
        return {k: row[k] for k in ("id", "owner_id", "enabled", "expires_at")}
    raise agent_error(403, "Máy không có license hợp lệ", code)


class Activate(BaseModel):
    activation_key: str = Field(min_length=20, max_length=200)
    hostname: str = Field(min_length=1, max_length=160)
    platform: Literal["WINDOWS"] = "WINDOWS"


def activate(body: Activate) -> dict:
    """One-time key -> a new device id and credential (the credential is returned once, only its hash is kept)."""
    with db_open() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT k.owner_id, k.used_at, l.max_agents, l.expires_at, l.enabled
            FROM activation_keys k JOIN licenses l ON l.owner_id=k.owner_id
            JOIN users u ON u.id=k.owner_id
            WHERE k.key_hash=? AND u.active=1""", (secret_hash(body.activation_key),)).fetchone()
        if row is None or row["used_at"] is not None:
            raise agent_error(403, "Mã kích hoạt không hợp lệ hoặc đã sử dụng",
                              "ACTIVATION_KEY_INVALID" if row is None else "ACTIVATION_KEY_USED")
        if not row["enabled"] or not license_not_expired(row["expires_at"]):
            raise agent_error(403, "License đã hết hạn hoặc bị khóa", "LICENSE_EXPIRED")
        count = db.execute("SELECT COUNT(*) FROM registered_agents WHERE owner_id=? AND enabled=1",
                           (row["owner_id"],)).fetchone()[0]
        if count >= row["max_agents"]:
            raise agent_error(409, "Đã đạt giới hạn số máy của license", "AGENT_LIMIT_REACHED")
        agent_id = "agt_" + uuid.uuid4().hex
        credential = secrets.token_urlsafe(40)
        db.execute("""INSERT INTO registered_agents(id, owner_id, hostname, credential_hash, enabled, created_at, platform)
            VALUES(?,?,?,?,1,?,?)""", (agent_id, row["owner_id"], body.hostname, secret_hash(credential), now(),
                                        body.platform))
        db.execute("UPDATE activation_keys SET used_at=? WHERE key_hash=?", (now(), secret_hash(body.activation_key)))
    return {"agent_id": agent_id, "agent_credential": credential, "owner_id": row["owner_id"], "platform": body.platform}
