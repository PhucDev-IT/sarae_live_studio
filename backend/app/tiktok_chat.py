"""Read-only TikTok public LIVE chat. No TikTok login, posting or OBS commands.

A LIVE session follows its own chat: the session's TikTok Account handle is read as soon as the session starts
(follow_session, called from app/live_sessions) and released when it ends, so nobody types a username. One reader per
LIVE desk (OWNER + Agent), i.e. per RUNNING session. /api/tiktok-chat/* is kept for older screens.
"""
import asyncio
from collections import deque
from datetime import datetime, timezone
import re
import threading
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException

from app.identity import current_owner
from app.ownership import Owner
from app.owner_agents import AgentId, owned_agent
from app.per_owner import PerOwner, live_key
from pydantic import BaseModel

from app.comment_processing import monitors
from app.database import SessionLocal
from app.ai_live_batches import enqueue

router = APIRouter(prefix="/api/tiktok-chat", tags=["TikTok LIVE Chat POC"], dependencies=[Depends(current_owner)])
session_router = APIRouter(prefix="/api/live/sessions", tags=["LIVE Sessions"], dependencies=[Depends(current_owner)])


class ConnectPayload(BaseModel):
    username: str
    # The Windows Agent whose LIVE this chat belongs to (product context for AI comes from its session).
    agent_id: str


def parse_username(value: str) -> str:
    value = value.strip()
    if value.startswith(("https://", "http://")):
        parsed = urlparse(value)
        if parsed.hostname not in ("www.tiktok.com", "tiktok.com", "m.tiktok.com"):
            raise HTTPException(422, "Chỉ hỗ trợ URL tiktok.com")
        value = parsed.path.strip("/").split("/")[0]
    value = value.lstrip("@")
    if not re.fullmatch(r"[a-zA-Z0-9._]{2,24}", value):
        raise HTTPException(422, "Nhập username TikTok hợp lệ, ví dụ @tenkenh hoặc link tiktok.com/@tenkenh")
    return value


class Reader:
    """Connection to a public TikTok LIVE chat for one LIVE desk (an OWNER's Windows Agent)."""

    def __init__(self, key: str):
        self.key = key
        self.owner_id, self.agent_id = key.split(":", 1)
        self.lock = threading.RLock()
        self.messages = deque(maxlen=300)
        self.seq = 0
        self.username = ""
        self.status = "idle"
        self.error = ""
        self.room_id = None
        self.generation = 0
        self.loop = None
        self.client = None

    def snapshot(self, after=0):
        with self.lock:
            return {
                "username": self.username,
                "status": self.status,
                "error": self.error,
                "room_id": self.room_id,
                "last_id": self.seq,
                "messages": [m for m in self.messages if m["id"] > after],
            }

    def start(self, username):
        with self.lock:
            if self.status in ("connecting", "connected", "stopping"):
                raise HTTPException(409, "Hãy ngắt phiên đọc chat hiện tại trước")
            try:
                from TikTokLive import TikTokLiveClient
            except ImportError as exc:
                raise HTTPException(503, "Backend chưa cài TikTokLive. Cài lại backend/requirements.txt vào đúng môi trường Python đang chạy Backend, rồi khởi động lại.") from exc
            self.generation += 1
            gen = self.generation
            self.username = username
            self.status = "connecting"
            self.error = ""
            self.room_id = None
            self.messages.clear()
            self.seq = 0
            monitors(self.key).reset()
            thread = threading.Thread(target=self._worker, args=(gen, username, TikTokLiveClient), daemon=True)
            thread.start()

    def _worker(self, gen, username, client_type):
        from TikTokLive.events import ConnectEvent, CommentEvent, DisconnectEvent
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = client_type(unique_id="@" + username)
        with self.lock:
            if self.generation != gen:
                loop.close()
                return
            self.client = client
            self.loop = loop

        @client.on(ConnectEvent)
        async def on_connect(event):
            with self.lock:
                if gen == self.generation:
                    self.status = "connected"
                    self.room_id = str(client.room_id) if client.room_id else None

        @client.on(CommentEvent)
        async def on_comment(event):
            with self.lock:
                if gen != self.generation:
                    return
                self.seq += 1
                user = event.user
                message = {
                    "id": self.seq,
                    "username": str(getattr(user, "unique_id", "") or ""),
                    "nickname": str(getattr(user, "nickname", "") or ""),
                    # Stable ids: the viewer is identified by user_id/unique_id, never by the display name.
                    "user_id": str(getattr(user, "id", "") or getattr(user, "user_id", "") or ""),
                    "comment": str(event.comment),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self.messages.append(message)
                event_id = str(getattr(event, "msg_id", None) or getattr(event, "message_id", None) or getattr(event, "id", None) or "")
                message["msg_id"] = event_id
                decision = monitors(self.key).process(message, event_id=event_id or None)
                if decision == 'selected':
                    enqueue(self.owner_id, self.agent_id, message)

        @client.on(DisconnectEvent)
        async def on_disconnect(event):
            with self.lock:
                if gen == self.generation and self.status != "stopping":
                    self.status = "disconnected"

        try:
            loop.run_until_complete(client.connect())
        except Exception as exc:
            with self.lock:
                if gen == self.generation and self.status != "stopping":
                    self.status = "error"
                    self.error = f"{type(exc).__name__}: {exc}"
        finally:
            with self.lock:
                if gen == self.generation:
                    if self.status == "stopping":
                        self.status = "idle"
                    elif self.status in ("connecting", "connected"):
                        self.status = "disconnected"
                    self.client = None
                    self.loop = None
            loop.close()

    def stop(self):
        with self.lock:
            if self.status in ("idle", "error", "disconnected"):
                self.status = "idle"
                return
            self.status = "stopping"
            client, loop = self.client, self.loop
        if client is not None and loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(client.disconnect(), loop)
            except RuntimeError:
                pass


readers = PerOwner(Reader)


@router.post("/connect")
def connect(body: ConnectPayload, owner: Owner):
    reader = readers(live_key(owner["id"], owned_agent(owner, body.agent_id)["id"]))
    reader.start(parse_username(body.username))
    return reader.snapshot()


@router.post("/disconnect")
def disconnect(owner: Owner, agent_id: AgentId):
    reader = readers(live_key(owner["id"], owned_agent(owner, agent_id)["id"]))
    reader.stop()
    return reader.snapshot()


@router.get("/status")
def status(owner: Owner, agent_id: AgentId, after: int = 0):
    return readers(live_key(owner["id"], owned_agent(owner, agent_id)["id"])).snapshot(max(0, after))


@router.get("/processing")
def processing(owner: Owner, agent_id: AgentId, after: int = 0):
    return monitors(live_key(owner["id"], owned_agent(owner, agent_id)["id"])).snapshot(max(0, after))


# ----- the chat of a LIVE session ---------------------------------------------------------------------

def account_handle(account_id: int | None) -> str:
    """TikTok handle (unique id) of a verified TikTok Account, or ""."""
    if not account_id:
        return ""
    from sqlalchemy import text
    with SessionLocal() as db:
        row = db.execute(text("SELECT user_name FROM tiktok_accounts WHERE id=:a"), {"a": account_id}).first()
    handle = (row[0] if row else "") or ""
    handle = handle.strip().lstrip("@")
    return handle if re.fullmatch(r"[a-zA-Z0-9._]{2,24}", handle) else ""


def follow_session(owner_id: str, agent_id: str | None, account_id: int | None) -> Reader | None:
    """Read the chat of the session's TikTok Account on its desk. Never raises: a failure shows in the reader."""
    if not agent_id:
        return None
    reader = readers(live_key(owner_id, agent_id))
    handle = account_handle(account_id)
    with reader.lock:
        if reader.status in ("connecting", "connected") and reader.username == handle:
            return reader
        if reader.status in ("connecting", "connected", "stopping"):
            reader.stop()
            if reader.status != "idle":
                return reader  # still closing the previous chat; "Kết nối lại" in the session retries
    if not handle:
        with reader.lock:
            reader.status, reader.error = "error", "TikTok Account của phiên chưa có username"
        return reader
    try:
        reader.start(handle)
    except HTTPException as exc:
        with reader.lock:
            reader.status, reader.error = "error", str(exc.detail)
    return reader


def release_session(owner_id: str, agent_id: str | None) -> None:
    if agent_id:
        reader = readers.get(live_key(owner_id, agent_id))
        if reader is not None:
            reader.stop()


def _session(owner: dict, session_id: int):
    from app.live_sessions import LiveSession
    with SessionLocal() as db:
        item = db.get(LiveSession, session_id)
        if item is None or item.owner_id != owner["id"]:
            raise HTTPException(404, "Không tìm thấy phiên LIVE")
        db.expunge(item)
        return item


def session_view(item, after: int = 0) -> dict:
    handle = account_handle(item.tiktok_account_id)
    base = {"session_id": item.id, "agent_id": item.agent_id}
    if item.status != "RUNNING" or not item.agent_id:
        return {**base, "username": handle, "status": "idle", "error": "", "room_id": None, "last_id": 0, "messages": []}
    return {**base, **readers(live_key(item.owner_id, item.agent_id)).snapshot(max(0, after)),
            "username": readers(live_key(item.owner_id, item.agent_id)).username or handle}


@session_router.get("/{session_id}/chat")
def session_chat(session_id: int, owner: Owner, after: int = 0):
    return session_view(_session(owner, session_id), after)


@session_router.post("/{session_id}/chat/connect")
def session_chat_connect(session_id: int, owner: Owner):
    item = _session(owner, session_id)
    if item.status != "RUNNING" or not item.agent_id:
        raise HTTPException(409, "Phiên chưa bắt đầu")
    if not account_handle(item.tiktok_account_id):
        raise HTTPException(422, "TikTok Account của phiên chưa có username: đồng bộ lại tài khoản trên Windows Agent")
    # Reconnects after a lost or failed connection; a chat already being read stays as it is.
    follow_session(owner["id"], item.agent_id, item.tiktok_account_id)
    return session_view(item)


@session_router.post("/{session_id}/chat/disconnect")
def session_chat_disconnect(session_id: int, owner: Owner):
    item = _session(owner, session_id)
    release_session(owner["id"], item.agent_id)
    return session_view(item)
