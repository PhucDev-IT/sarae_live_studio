"""Read-only TikTok public LIVE chat POC. No TikTok login, posting or OBS commands."""
import asyncio
from collections import deque
from datetime import datetime, timezone
import re
import threading
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.comment_processing import monitor
from app.ai_live_batches import enqueue

router = APIRouter(prefix="/api/tiktok-chat", tags=["TikTok LIVE Chat POC"])


class ConnectPayload(BaseModel):
    username: str


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
    def __init__(self):
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
                from TikTokLive import TikTokLiveClient  # optional POC dependency
            except ImportError as exc:
                raise HTTPException(503, "Chưa cài TikTokLive. Chạy: python -m pip install TikTokLive==7.0.1") from exc
            self.generation += 1
            gen = self.generation
            self.username = username
            self.status = "connecting"
            self.error = ""
            self.room_id = None
            self.messages.clear()
            self.seq = 0
            monitor.reset()
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
                    "comment": str(event.comment),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self.messages.append(message)
                event_id = str(getattr(event, "msg_id", None) or getattr(event, "message_id", None) or getattr(event, "id", None) or "")
                decision = monitor.process(message, event_id=event_id or None)
                if decision == 'selected':
                    enqueue(message)

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


reader = Reader()


@router.post("/connect")
def connect(body: ConnectPayload):
    reader.start(parse_username(body.username))
    return reader.snapshot()


@router.post("/disconnect")
def disconnect():
    reader.stop()
    return reader.snapshot()


@router.get("/status")
def status(after: int = 0):
    return reader.snapshot(max(0, after))


@router.get("/processing")
def processing(after: int = 0):
    return monitor.snapshot(max(0, after))
