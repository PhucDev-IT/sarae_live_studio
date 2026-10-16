"""Minimal OBS proof of concept: playlist and media-source playback.

OBS 28+ includes obs-websocket v5. Create an OBS Media Source named
'Sarae Video' (uncheck local file looping) in the scene being streamed.
Backend and OBS must run on the same Windows machine for file paths.
"""
import threading
import time
from pathlib import Path

import obsws_python as obs
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, SessionLocal, DATA_DIR
from app.media import Media, MEDIA_DIR
from app.config import settings

router = APIRouter(prefix="/api/live", tags=["LIVE / OBS"])


class Playlist(Base):
    __tablename__ = "live_playlists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    media_ids: Mapped[str] = mapped_column(Text, default="[]")


class PlaylistPayload(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    media_ids: list[int] = Field(min_length=1, max_length=500)


class ObsConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=4455, ge=1, le=65535)
    password: str = ""
    input_name: str = "Sarae Video"


class StartPayload(BaseModel):
    playlist_id: int
    start_index: int = Field(default=0, ge=0)


class PlayState:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = ObsConfig(host=settings.obs_host, port=settings.obs_port,
                                password=settings.obs_password, input_name=settings.obs_input_name)
        self.playlist_id: int | None = None
        self.index = 0
        self.ids: list[int] = []
        self.active = False
        self.last_status = ""
        self.generation = 0

    def client(self):
        # OBS WebSocket 5.x. No output or streaming controls: test safely in preview.
        return obs.ReqClient(host=self.config.host, port=self.config.port,
                             password=self.config.password, timeout=4)

    def status(self):
        with self.lock:
            try:
                c = self.client()
                try:
                    version = c.get_version().obs_version
                    state = c.get_media_input_status(self.config.input_name).media_state
                    return {"connected": True, "obs_version": version, "media_state": state,
                            "running": self.active, "playlist_id": self.playlist_id,
                            "index": self.index, "count": len(self.ids),
                            "input_name": self.config.input_name, "last_status": self.last_status}
                finally:
                    c.base_client.ws.close()
            except Exception as exc:
                return {"connected": False, "error": str(exc), "running": self.active,
                        "playlist_id": self.playlist_id, "index": self.index,
                        "count": len(self.ids), "input_name": self.config.input_name,
                        "last_status": self.last_status}

    def play(self, index: int):
        with self.lock:
            if not self.ids:
                raise HTTPException(409, "Playlist rỗng")
            if not 0 <= index < len(self.ids):
                raise HTTPException(422, "Vị trí video không hợp lệ")
            with SessionLocal() as db:
                item = db.get(Media, self.ids[index])
                if item is None:
                    raise HTTPException(409, "Video không còn trong thư viện")
                path = (MEDIA_DIR / item.stored_name).resolve()
                if not path.is_file():
                    raise HTTPException(409, f"Thiếu file {item.filename}")
                name = item.filename
            try:
                client = self.client()
                try:
                    client.set_input_settings(self.config.input_name, {"local_file": str(path)}, overlay=True)
                    client.trigger_media_input_action(self.config.input_name,
                        "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
                finally:
                    client.base_client.ws.close()
            except Exception as exc:
                self.last_status = str(exc)
                raise HTTPException(503, f"Không điều khiển được OBS: {exc}") from exc
            self.index = index
            self.active = True
            self.generation += 1
            self.last_status = f"Đang phát {name}"
            return {"ok": True, "index": index, "filename": name}

    def stop(self):
        with self.lock:
            self.active = False
            self.generation += 1
            try:
                client = self.client()
                try:
                    client.trigger_media_input_action(self.config.input_name,
                        "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP")
                finally:
                    client.base_client.ws.close()
            except Exception as exc:
                raise HTTPException(503, f"Không dừng được OBS: {exc}") from exc
            self.last_status = "Đã dừng"
            return {"ok": True}


state = PlayState()


def monitor():
    # Automatic next-track, using OBS media end state instead of guessed durations.
    # Avoids duplicate advancement: lock serializes skip and monitor.
    while True:
        time.sleep(1.0)
        with state.lock:
            if not state.active or not state.ids:
                continue
            try:
                client = state.client()
                try:
                    status = client.get_media_input_status(state.config.input_name).media_state
                finally:
                    client.base_client.ws.close()
                if status == "OBS_MEDIA_STATE_ENDED":
                    if state.index + 1 >= len(state.ids):
                        state.active = False
                        state.last_status = "Đã hết playlist"
                    else:
                        state.play(state.index + 1)
            except Exception as exc:
                state.last_status = f"OBS lỗi: {exc}"


threading.Thread(target=monitor, daemon=True, name="sarae-obs-monitor").start()


@router.get("/status")
def get_status():
    return state.status()


@router.post("/config")
def set_config(body: ObsConfig):
    if body.host not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(422, "Thử nghiệm hiện chỉ hỗ trợ OBS trên cùng máy")
    with state.lock:
        state.active = False
        state.config = body
        state.last_status = "Đã cập nhật cấu hình OBS"
    return state.status()


@router.get("/playlists")
def playlists():
    import json
    with SessionLocal() as db:
        return [{"id": p.id, "name": p.name, "media_ids": json.loads(p.media_ids)}
                for p in db.scalars(select(Playlist).order_by(Playlist.id.desc())).all()]


@router.post("/playlists", status_code=201)
def create_playlist(body: PlaylistPayload):
    import json
    with SessionLocal() as db:
        existing = set(db.scalars(select(Media.id).where(Media.id.in_(body.media_ids))).all())
        if len(existing) != len(set(body.media_ids)):
            raise HTTPException(422, "Playlist chứa video không tồn tại")
        playlist = Playlist(name=body.name.strip(), media_ids=json.dumps(body.media_ids))
        db.add(playlist)
        db.commit()
        db.refresh(playlist)
        return {"id": playlist.id, "name": playlist.name, "media_ids": body.media_ids}


@router.delete("/playlists/{playlist_id}", status_code=204)
def delete_playlist(playlist_id: int):
    with SessionLocal() as db:
        item = db.get(Playlist, playlist_id)
        if item is None:
            raise HTTPException(404, "Không tìm thấy playlist")
        with state.lock:
            if state.active and state.playlist_id == playlist_id:
                raise HTTPException(409, "Hãy dừng phát trước khi xóa playlist")
        db.delete(item)
        db.commit()


@router.post("/start")
def start(body: StartPayload):
    import json
    with SessionLocal() as db:
        playlist = db.get(Playlist, body.playlist_id)
        if playlist is None:
            raise HTTPException(404, "Không tìm thấy playlist")
        ids = json.loads(playlist.media_ids)
    with state.lock:
        if state.active:
            raise HTTPException(409, "Hãy dừng playlist hiện tại trước")
        state.ids = ids
        state.playlist_id = body.playlist_id
        return state.play(body.start_index)


@router.post("/next")
def next_video():
    with state.lock:
        if not state.active:
            raise HTTPException(409, "Chưa phát playlist")
        if state.index + 1 >= len(state.ids):
            return state.stop()
        return state.play(state.index + 1)


@router.post("/stop")
def stop_video():
    return state.stop()
