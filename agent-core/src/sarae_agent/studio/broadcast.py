"""Nonblocking public TikTok LIVE status, adapted from sarae-ai-live-studio/tiktok_live.

This information is displayed to the operator; the Console remains the authority for real pin actions.
"""
from __future__ import annotations

import asyncio
import threading
import time


def read_room(data: dict, username: str) -> dict:
    body = data.get("data") or {}
    user, room = body.get("user") or {}, body.get("liveRoom") or {}
    status = room.get("status", user.get("status"))
    state = "live" if status == 2 else "offline" if status == 4 else "unknown"
    viewers = (room.get("liveRoomStats") or {}).get("userCount")
    return {"state": state, "username": username, "room_id": str(user.get("roomId") or "") if state == "live" else None,
            "title": room.get("title") if state == "live" else None,
            "viewers": viewers if state == "live" and isinstance(viewers, int) else None,
            "checked_at": time.time(), "error": "", "source": "public_tiktok"}


def check_room(username: str) -> dict:
    async def fetch():
        from TikTokLive import TikTokLiveClient
        from TikTokLive.client.web.routes.fetch_room_id_api import FetchRoomIdAPIRoute
        client = TikTokLiveClient(unique_id="@" + username)
        try:
            return await asyncio.wait_for(FetchRoomIdAPIRoute.fetch_user_room_data(web=client.web, unique_id=username), timeout=8)
        finally:
            await client.web.close()
    return read_room(asyncio.run(fetch()), username)


class BroadcastWatch:
    def __init__(self, checker=check_room, *, clock=time.time, interval=30):
        self.checker, self.clock, self.interval = checker, clock, interval
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._username = ""
        self._state = {"state": "unknown", "username": None, "checked_at": None, "error": "", "source": "public_tiktok"}

    def follow(self, username: str):
        with self._lock:
            if self._username == username:
                return
            self._username = username
            self._state = {"state": "checking" if username else "unknown", "username": username or None,
                           "checked_at": None, "error": "", "source": "public_tiktok"}
        self._wake.set()

    def view(self):
        with self._lock:
            result = dict(self._state)
        age = None if result.get("checked_at") is None else max(0, self.clock() - result["checked_at"])
        result["age_seconds"] = None if age is None else round(age, 1)
        if age is not None and age > max(90, self.interval * 3):
            result["state"] = "unknown"
        return result

    def refresh(self):
        self._wake.set()

    def check_once(self):
        with self._lock:
            username = self._username
        if not username:
            return
        try:
            result = self.checker(username)
        except Exception:
            result = {"state": "unknown", "username": username, "checked_at": self.clock(),
                      "error": "Chưa kiểm tra được TikTok LIVE. Hệ thống sẽ thử lại.", "source": "public_tiktok"}
        with self._lock:
            if self._username == username:
                self._state = result

    def start(self):
        if self._thread is not None:
            return
        def run():
            while not self._stop.is_set():
                self._wake.clear()
                self.check_once()
                self._wake.wait(self.interval)
        self._thread = threading.Thread(target=run, name="studio-broadcast", daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        self._wake.set()
