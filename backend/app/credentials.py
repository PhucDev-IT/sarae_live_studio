"""OWNER credentials for AI providers, kept apart from settings and runtime state.

- Stored encrypted at rest (Fernet) in their own table; the key comes from SARAE_CREDENTIAL_KEY or a file generated in
  backend/data (git-ignored). Losing that key makes stored credentials unreadable (owners enter them again).
- Write-only over the API: responses only say whether a key is configured and its last 4 characters.
- Never logged and never part of the normal AI configuration response.
"""
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from app.config import settings
from app.database import DATA_DIR
from app.identity import db_open

KEY_FILE = DATA_DIR / "credential.key"
PROVIDERS = ("gemini", "openai")
_fernet: Fernet | None = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        key = (getattr(settings, "sarae_credential_key", "") or "").encode()
        if not key:
            try:
                key = KEY_FILE.read_bytes().strip()
            except FileNotFoundError:
                key = Fernet.generate_key()
                KEY_FILE.write_bytes(key)
        _fernet = Fernet(key)
    return _fernet


def encrypt(value: str) -> str:
    """Encrypt a server-side secret (e.g. a Google refresh token) with the same key as the AI credentials."""
    return _cipher().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    """None when empty or when the key changed (the secret must then be obtained again)."""
    if not value:
        return None
    try:
        return _cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        return None


def _table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS owner_credentials (
        owner_id TEXT NOT NULL, provider TEXT NOT NULL, secret TEXT NOT NULL, last4 TEXT NOT NULL,
        updated_at TEXT NOT NULL, PRIMARY KEY(owner_id, provider))""")


def _provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise HTTPException(422, "Nhà cung cấp AI không được hỗ trợ")
    return provider


def save(owner_id: str, provider: str, api_key: str):
    api_key = api_key.strip()
    if not 8 <= len(api_key) <= 400:
        raise HTTPException(422, "API key không hợp lệ")
    with db_open() as db:
        _table(db)
        db.execute("""INSERT INTO owner_credentials(owner_id,provider,secret,last4,updated_at) VALUES(?,?,?,?,?)
            ON CONFLICT(owner_id,provider) DO UPDATE SET secret=excluded.secret,last4=excluded.last4,updated_at=excluded.updated_at""",
                   (owner_id, _provider(provider), _cipher().encrypt(api_key.encode()).decode(), api_key[-4:],
                    datetime.now(timezone.utc).isoformat()))


def delete(owner_id: str, provider: str) -> bool:
    with db_open() as db:
        _table(db)
        return db.execute("DELETE FROM owner_credentials WHERE owner_id=? AND provider=?", (owner_id, _provider(provider))).rowcount > 0


def describe(owner_id: str) -> list[dict]:
    """What the owner may see about their credentials: never the key itself."""
    with db_open() as db:
        _table(db)
        rows = {r["provider"]: r for r in db.execute(
            "SELECT provider,last4,updated_at FROM owner_credentials WHERE owner_id=?", (owner_id,)).fetchall()}
    return [{"provider": p, "configured": p in rows, "last4": rows[p]["last4"] if p in rows else None,
             "updated_at": rows[p]["updated_at"] if p in rows else None} for p in PROVIDERS]


def secret(owner_id: str, provider: str) -> str | None:
    """The decrypted key for server-side use only (AI calls)."""
    with db_open() as db:
        _table(db)
        row = db.execute("SELECT secret FROM owner_credentials WHERE owner_id=? AND provider=?", (owner_id, provider)).fetchone()
    if row is None:
        return None
    try:
        return _cipher().decrypt(row["secret"].encode()).decode()
    except InvalidToken:
        return None
