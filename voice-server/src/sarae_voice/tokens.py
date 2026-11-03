"""Per-job read tokens (docs/TTS_SERVER.md 5.1).

token = base64url(json {"j": job_id, "a": reader_agent_id, "exp": unix_seconds, "k": key_id}) "." base64url(HMAC-SHA256)

What a token proves: whoever holds it received it from the orchestrator for THIS job and THIS reader id, before `exp`.
What it does not prove: the identity of the machine presenting it. `X-Sarae-Agent-Id` is a value the client sends; it
only stops a token from being used by mistake under another agent id. Holding the token is the real capability, so
it must travel only inside the authenticated Sarae command channel (Phase 3-4), never in logs or browser responses.
The key is random per process unless SARAE_VOICE_READ_TOKEN_SECRET is set: tokens die with the in-memory jobs on a
restart. Cancelling a job revokes its token (the job, not the token, is checked on every read).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from sarae_voice.errors import ServiceError


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class ReadTokens:
    def __init__(self, secret: str = ""):
        raw = secret.encode() if secret else secrets.token_bytes(32)
        self._key = hashlib.sha256(raw).digest()
        self.key_id = hashlib.sha256(self._key).hexdigest()[:8]

    def issue(self, job_id: str, agent_id: str, exp: float) -> str:
        payload = _b64(json.dumps({"j": job_id, "a": agent_id, "exp": int(exp), "k": self.key_id},
                                  separators=(",", ":")).encode())
        return f"{payload}.{_b64(hmac.new(self._key, payload.encode(), hashlib.sha256).digest())}"

    def verify(self, token: str, job_id: str, agent_header: str | None) -> dict:
        try:
            payload, sig = token.split(".", 1)
            expected = hmac.new(self._key, payload.encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(_unb64(sig), expected):
                raise ValueError("signature")
            claims = json.loads(_unb64(payload))
        except Exception as exc:  # noqa: BLE001 - every malformed token is the same 401
            raise ServiceError("TTS_AUTH", "Invalid read token", 401) from exc
        if claims.get("exp", 0) < time.time():
            raise ServiceError("TOKEN_EXPIRED", "Read token expired", 401)
        if claims.get("j") != job_id:
            raise ServiceError("TOKEN_SCOPE", "Read token is for another job", 403)
        if agent_header is not None and agent_header != claims.get("a"):
            raise ServiceError("TOKEN_SCOPE", "Read token is for another agent", 403)
        return claims
