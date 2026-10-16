"""Minimal OBS proof of concept: playlist and media-source playback.

OBS 28+ includes obs-websocket v5. Create an OBS Media Source named
'Sarae Video' (uncheck local file looping) in the scene being streamed.
Backend and OBS must run on the same Windows machine for file paths.
"""
import json
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
from app.catalog import Product
from app.product_videos import ProductLiveVideo
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
    dual_mode: bool = False
    # A/B Media Source names for dual mode; empty falls back to "<input_name> A" / "<input_name> B".
    source_a: str = Field(default="", max_length=200)
    source_b: str = Field(default="", max_length=200)


class ObsConnection(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=4455, ge=1, le=65535)
    password: str = ""


class SeekPayload(BaseModel):
    milliseconds: int = Field(ge=0)


class SelectPayload(BaseModel):
    index: int = Field(ge=0)


class ProductStartPayload(BaseModel):
    product_id: int = Field(gt=0)


class ProductSwitchPayload(BaseModel):
    product_id: int = Field(gt=0)
    mode: str = Field(pattern="^(immediate|after_current)$")


class StartPayload(BaseModel):
    playlist_id: int
    start_index: int = Field(default=0, ge=0)
    loop: bool = False


# OBS connection saved from the web console so a Backend restart does not ask for it again.
# Lives next to the SQLite database on the operator's machine; it holds the WebSocket password, so it is git-ignored.
OBS_CONFIG_FILE = DATA_DIR / "obs_config.json"


def load_obs_config() -> ObsConfig:
    default = ObsConfig(host=settings.obs_host, port=settings.obs_port,
                        password=settings.obs_password, input_name=settings.obs_input_name)
    try:
        return ObsConfig.model_validate_json(OBS_CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        # A corrupt file must not stop the API from starting; the operator can save the config again.
        return default


def save_obs_config(config: ObsConfig):
    tmp = OBS_CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(config.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OBS_CONFIG_FILE)


class PlayState:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = load_obs_config()
        self.playlist_id: int | None = None
        self.index = 0
        self.ids: list[int] = []
        self.active = False
        self.paused = False
        self.last_status = ""
        self.generation = 0
        self.dual_mode = self.config.dual_mode
        self.active_slot = 0
        self.slots = []
        self.scene_name = ""
        self.loop = False
        self.retry_count = 0
        self.next_retry_at = 0.0
        self.error_count = 0
        self.transition_count = 0
        self.recovery_count = 0
        self.session_started_at = None
        self.last_progress_at = time.monotonic()
        self.last_cursor = None
        self.stall_restarts = 0
        self.session_mode = "playlist"
        self.product_id = None
        self.pending_product_id = None
        self.session_id = None

    def product_queue(self, product_id):
        with SessionLocal() as db:
            product = db.get(Product, product_id)
            if product is None:
                raise HTTPException(404, "Không tìm thấy sản phẩm")
            rows = db.scalars(select(Media).join(ProductLiveVideo, ProductLiveVideo.media_id == Media.id).where(ProductLiveVideo.product_id == product_id).order_by(ProductLiveVideo.sort_order)).all()
            ids = [m.id for m in rows if (Path(m.local_path) if m.local_path else MEDIA_DIR / m.stored_name).is_file()]
            if not ids:
                raise HTTPException(409, f"Sản phẩm {product.name} chưa có video hợp lệ")
            return ids

    def switch_product(self, product_id):
        ids = self.product_queue(product_id)
        old_ids, old_index, old_product = self.ids, self.index, self.product_id
        self.ids = ids
        try:
            result = self.play(0)
        except Exception:
            self.ids, self.index, self.product_id = old_ids, old_index, old_product
            raise
        self.product_id = product_id
        self.pending_product_id = None
        self.session_mode = "product"
        if self.session_id is not None:
            from app.live_sessions import record_product_switch
            record_product_switch(self.session_id, product_id)
        return result

    def client(self):
        # OBS WebSocket 5.x. No output or streaming controls: test safely in preview.
        return obs.ReqClient(host=self.config.host, port=self.config.port,
                             password=self.config.password, timeout=4)

    def _sources(self):
        return [self.config.source_a.strip() or self.config.input_name + " A",
                self.config.source_b.strip() or self.config.input_name + " B"]

    def _locate_dual(self, client):
        """Only switch to A/B when BOTH scene items exist in current scene."""
        scene = client.get_current_program_scene().current_program_scene_name
        names = self._sources()
        try:
            ids = [client.get_scene_item_id(scene, name).scene_item_id for name in names]
        except Exception:
            return None
        return scene, names, ids

    def _media(self, index):
        with SessionLocal() as db:
            item = db.get(Media, self.ids[index])
            if item is None:
                raise HTTPException(409, "Video không còn trong thư viện")
            path = (Path(item.local_path) if item.local_path else MEDIA_DIR / item.stored_name).resolve()
            if not path.is_file():
                raise HTTPException(409, f"Thiếu file {item.filename}")
            return str(path), item.filename

    def _stage(self, client, slot, index):
        path, _ = self._media(index)
        name = self.slots[slot]
        # Keep decoder open when hidden. Mute the stand-by source to avoid audio overlap.
        client.set_input_mute(name, True)
        client.set_input_settings(name, {"local_file": path, "is_local_file": True,
                                         "looping": False, "close_when_inactive": False}, overlay=True)
        client.trigger_media_input_action(name, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")

    def _play_dual(self, client, index, initial=False):
        if initial:
            located = self._locate_dual(client)
            if not located:
                raise HTTPException(422, "Cần có đủ hai Media Source " + " và ".join(self._sources()) + " trong scene OBS hiện tại")
            self.scene_name, self.slots, self.scene_item_ids = located
            self.active_slot = 0
            for slot, name in enumerate(self.slots):
                client.set_input_mute(name, True)
                client.set_scene_item_enabled(self.scene_name, self.scene_item_ids[slot], False)
            self._stage(client, 0, index)
            time.sleep(0.2)
            client.set_scene_item_enabled(self.scene_name, self.scene_item_ids[0], True)
            client.set_input_mute(self.slots[0], False)
        else:
            incoming = 1 - self.active_slot
            # The next input has been staged during the previous video.
            expected_path, _ = self._media(index)
            actual_settings = client.get_input_settings(self.slots[incoming]).input_settings
            if actual_settings.get("local_file") != expected_path:
                self._stage(client, incoming, index)
                time.sleep(0.3)
            # Show the next video first: no empty frame between sources.
            client.set_scene_item_enabled(self.scene_name, self.scene_item_ids[incoming], True)
            client.set_input_mute(self.slots[incoming], False)
            client.set_input_mute(self.slots[self.active_slot], True)
            client.set_scene_item_enabled(self.scene_name, self.scene_item_ids[self.active_slot], False)
            self.active_slot = incoming
        following = index + 1 if index + 1 < len(self.ids) else (0 if self.loop else None)
        if following is not None:
            try:
                self._stage(client, 1 - self.active_slot, following)
            except HTTPException as exc:
                self.last_status = f"Không preload được video kế: {exc.detail}"

    def metrics(self):
        return {"session_mode": self.session_mode, "queue_ids": list(self.ids), "product_id": self.product_id,
                "pending_product_id": self.pending_product_id, "session_id": self.session_id,
                "paused": self.paused, "loop": self.loop, "retry_count": self.retry_count,
                "error_count": self.error_count, "transition_count": self.transition_count,
                "recovery_count": self.recovery_count,
                "session_seconds": int(time.time() - self.session_started_at) if self.session_started_at else 0,
                # Current connection settings (never the password) so the UI can show and prefill them.
                "obs": {"host": self.config.host, "port": self.config.port, "input_name": self.config.input_name,
                        "dual_mode": self.dual_mode, "dual_sources": self._sources(),
                        "has_password": bool(self.config.password)}}

    def status(self):
        with self.lock:
            try:
                c = self.client()
                try:
                    version = c.get_version().obs_version
                    if self.dual_mode:
                        located = self._locate_dual(c)
                        if located is None:
                            return {"connected": False, "error": "Cần hai Media Sources: " + " và ".join(self._sources()) + " trong scene đang chạy", "dual_mode": True, "running": self.active, "playlist_id": self.playlist_id, "index": self.index, "count": len(self.ids), "last_status": self.last_status, "loop": self.loop, "retry_count": self.retry_count, **self.metrics()}
                        source_name = self.slots[self.active_slot] if self.active and self.slots else located[1][0]
                    else:
                        source_name = self.config.input_name
                    media = c.get_media_input_status(source_name)
                    return {"connected": True, "obs_version": version, "media_state": media.media_state,
                            "media_cursor": getattr(media, "media_cursor", None),
                            "media_duration": getattr(media, "media_duration", None),
                            "running": self.active, "playlist_id": self.playlist_id,
                            "index": self.index, "count": len(self.ids),
                            "input_name": self.config.input_name, "dual_mode": self.dual_mode, "last_status": self.last_status, **self.metrics()}
                finally:
                    c.base_client.ws.close()
            except Exception as exc:
                return {"connected": False, "error": str(exc), "running": self.active,
                        "playlist_id": self.playlist_id, "index": self.index,
                        "count": len(self.ids), "input_name": self.config.input_name,
                        "last_status": self.last_status, **self.metrics()}

    def play(self, index: int):
        with self.lock:
            if not self.ids:
                raise HTTPException(409, "Playlist rỗng")
            if not 0 <= index < len(self.ids):
                raise HTTPException(422, "Vị trí video không hợp lệ")
            path, name = self._media(index)
            try:
                client = self.client()
                try:
                    if self.dual_mode:
                        self._play_dual(client, index, initial=not self.active)
                    else:
                        client.set_input_settings(self.config.input_name, {"local_file": path}, overlay=True)
                        client.trigger_media_input_action(self.config.input_name,
                            "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
                finally:
                    client.base_client.ws.close()
            except HTTPException:
                raise
            except Exception as exc:
                self.last_status = str(exc)
                raise HTTPException(503, f"Không điều khiển được OBS: {exc}") from exc
            if self.active:
                self.transition_count += 1
            self.index = index
            self.last_progress_at = time.monotonic()
            self.last_cursor = None
            self.stall_restarts = 0
            self.retry_count = 0
            self.next_retry_at = 0.0
            self.active = True
            self.paused = False
            self.generation += 1
            self.last_status = f"Đang phát {name}"
            return {"ok": True, "index": index, "filename": name}

    def stop(self):
        with self.lock:
            self.active = False
            self.paused = False
            self.generation += 1
            try:
                client = self.client()
                try:
                    if self.dual_mode and self.slots:
                        for slot, name in enumerate(self.slots):
                            client.set_input_mute(name, True)
                            client.set_scene_item_enabled(self.scene_name, self.scene_item_ids[slot], False)
                            client.trigger_media_input_action(name, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP")
                    else:
                        client.trigger_media_input_action(self.config.input_name,
                            "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP")
                finally:
                    client.base_client.ws.close()
            except Exception as exc:
                raise HTTPException(503, f"Không dừng được OBS: {exc}") from exc
            self.retry_count = 0
            self.pending_product_id = None
            self.last_status = "Đã dừng"
            return {"ok": True}


state = PlayState()


def monitor():
    """Monitor ended videos and retry transient OBS disconnects with bounded backoff."""
    while True:
        time.sleep(1.0)
        with state.lock:
            if not state.active or state.paused or not state.ids or time.monotonic() < state.next_retry_at:
                continue
            try:
                client = state.client()
                try:
                    source = state.slots[state.active_slot] if state.dual_mode and state.slots else state.config.input_name
                    report = client.get_media_input_status(source)
                    media_status = report.media_state
                finally:
                    client.base_client.ws.close()
                cursor = getattr(report, "media_cursor", None)
                now = time.monotonic()
                if media_status == "OBS_MEDIA_STATE_PLAYING" and isinstance(cursor, (int, float)):
                    if state.last_cursor is None or cursor > state.last_cursor + 100 or cursor < state.last_cursor - 500:
                        state.last_cursor = cursor
                        state.last_progress_at = now
                    elif now - state.last_progress_at >= 20:
                        if state.stall_restarts >= 2:
                            state.active = False
                            state.last_status = "Watchdog: video đứng sau 2 lần thử khôi phục"
                            continue
                        restore = state.client()
                        try:
                            restore.trigger_media_input_action(source, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
                        finally:
                            restore.base_client.ws.close()
                        state.stall_restarts += 1
                        state.recovery_count += 1
                        state.last_cursor = None
                        state.last_progress_at = now
                        state.last_status = "Watchdog: đã restart video bị đứng"
                elif media_status in ("OBS_MEDIA_STATE_PAUSED", "OBS_MEDIA_STATE_STOPPED"):
                    state.last_cursor = None
                    state.last_progress_at = now
                if state.retry_count:
                    state.recovery_count += 1
                    state.last_cursor = None
                    state.last_progress_at = now
                    state.last_status = "OBS đã kết nối lại; tiếp tục theo dõi"
                state.retry_count = 0
                state.next_retry_at = 0
                if media_status == "OBS_MEDIA_STATE_ENDED":
                    if state.session_mode == "product" and state.pending_product_id is not None:
                        try:
                            state.switch_product(state.pending_product_id)
                            continue
                        except HTTPException as exc:
                            state.pending_product_id = None
                            state.last_status = f"Chuyển sản phẩm lỗi: {exc.detail}; tiếp tục sản phẩm cũ"
                    if state.session_mode == "product":
                        try:
                            refreshed = state.product_queue(state.product_id)
                            previous_id = state.ids[state.index]
                            if previous_id in refreshed:
                                state.index = refreshed.index(previous_id)
                            else:
                                state.index = -1
                            state.ids = refreshed
                        except HTTPException as exc:
                            state.active = False
                            state.last_status = f"Dừng phiên: {exc.detail}"
                            continue
                    target = state.index + 1
                    if target >= len(state.ids):
                        if not state.loop:
                            state.active = False
                            state.paused = False
                            state.last_status = "Đã hết playlist"
                            continue
                        target = 0
                    # Skips missing media files, up to one complete playlist traversal.
                    played = False
                    for shift in range(len(state.ids)):
                        candidate = (target + shift) % len(state.ids)
                        if not state.loop and candidate < target:
                            break
                        try:
                            state.play(candidate)
                            played = True
                            break
                        except HTTPException as exc:
                            if exc.status_code == 409:
                                state.last_status = f"Bỏ qua video lỗi: {exc.detail}"
                                continue
                            raise
                    if not played:
                        state.active = False
                        state.paused = False
                        state.last_status = "Không còn video hợp lệ để phát"
            except Exception as exc:
                state.retry_count += 1
                state.error_count += 1
                state.last_status = f"OBS lỗi (thử lại {state.retry_count}/5): {exc}"
                if state.retry_count >= 5:
                    state.active = False
                    state.last_status = f"Đã tạm dừng do OBS lỗi nhiều lần: {exc}"
                else:
                    state.next_retry_at = time.monotonic() + min(2 ** state.retry_count, 20)


threading.Thread(target=monitor, daemon=True, name="sarae-obs-monitor").start()


@router.get("/status")
def get_status():
    return state.status()


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@router.post("/config")
def set_config(body: ObsConfig):
    if body.host not in LOCAL_HOSTS:
        raise HTTPException(422, "Thử nghiệm hiện chỉ hỗ trợ OBS trên cùng máy")
    if body.dual_mode:
        a, b = body.source_a.strip() or body.input_name + " A", body.source_b.strip() or body.input_name + " B"
        if a == b:
            raise HTTPException(422, "Hai Media Source A và B phải khác tên nhau")
    with state.lock:
        # An empty password keeps the saved one, so the operator does not retype it on every change.
        if not body.password:
            body = body.model_copy(update={"password": state.config.password})
        state.active = False
        state.config = body
        state.dual_mode = body.dual_mode
        state.slots = []
        state.last_status = "Đã cập nhật cấu hình OBS"
        try:
            save_obs_config(body)
        except OSError as exc:
            state.last_status = f"Đã cập nhật cấu hình OBS nhưng không lưu được xuống đĩa: {exc}"
    return state.status()


@router.post("/obs/inputs")
def list_obs_inputs(body: ObsConnection):
    """List OBS inputs so the UI can offer real source names; marks those placed in the current program scene.

    Uses the given connection (an empty password falls back to the saved one) and does not change the saved config.
    """
    if body.host not in LOCAL_HOSTS:
        raise HTTPException(422, "Thử nghiệm hiện chỉ hỗ trợ OBS trên cùng máy")
    password = body.password or state.config.password
    try:
        client = obs.ReqClient(host=body.host, port=body.port, password=password, timeout=4)
    except Exception as exc:
        raise HTTPException(503, f"Không kết nối được OBS: {exc}")
    try:
        scene = client.get_current_program_scene().current_program_scene_name
        in_scene = {item.get("sourceName") for item in client.get_scene_item_list(scene).scene_items}
        inputs = [{"name": item.get("inputName"), "kind": item.get("unversionedInputKind") or item.get("inputKind"),
                   "in_scene": item.get("inputName") in in_scene}
                  for item in client.get_input_list().inputs]
        # Media Sources first, then those in the live scene, then by name.
        inputs.sort(key=lambda i: (i["kind"] != "ffmpeg_source", not i["in_scene"], i["name"] or ""))
        return {"scene": scene, "inputs": inputs}
    except Exception as exc:
        raise HTTPException(502, f"OBS từ chối yêu cầu: {exc}")
    finally:
        client.base_client.ws.close()


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
        state.session_mode = "playlist"
        state.session_id = None
        state.product_id = None
        state.pending_product_id = None
        state.ids = ids
        state.playlist_id = body.playlist_id
        state.loop = body.loop
        state.paused = False
        state.session_started_at = time.time()
        state.transition_count = 0
        state.recovery_count = 0
        state.error_count = 0
        return state.play(body.start_index)


@router.post("/next")
def next_video():
    with state.lock:
        if not state.active:
            raise HTTPException(409, "Chưa phát playlist")
        if state.pending_product_id is not None:
            return state.switch_product(state.pending_product_id)
        if state.index + 1 >= len(state.ids):
            if state.loop:
                return state.play(0)
            return state.stop()
        return state.play(state.index + 1)


@router.post("/stop")
def stop_video():
    with state.lock:
        if state.session_id is not None and state.active:
            from app.live_sessions import finish_session
            return finish_session(state.session_id)
        return state.stop()


@router.put("/playlists/{playlist_id}")
def edit_playlist(playlist_id: int, body: PlaylistPayload):
    import json
    with state.lock:
        if state.active and state.playlist_id == playlist_id:
            raise HTTPException(409, "Hãy Stop playlist đang phát trước khi chỉnh sửa")
        with SessionLocal() as db:
            item = db.get(Playlist, playlist_id)
            if item is None:
                raise HTTPException(404, "Không tìm thấy playlist")
            existing = set(db.scalars(select(Media.id).where(Media.id.in_(body.media_ids))).all())
            if len(existing) != len(set(body.media_ids)):
                raise HTTPException(422, "Playlist chứa video không tồn tại")
            item.name = body.name.strip()
            item.media_ids = json.dumps(body.media_ids)
            db.commit()
            return {"id": playlist_id, "name": item.name, "media_ids": body.media_ids}


def active_source():
    return state.slots[state.active_slot] if state.dual_mode and state.slots else state.config.input_name


@router.post("/pause")
def pause_video():
    with state.lock:
        if not state.active or state.paused:
            raise HTTPException(409, "Không có video đang phát để Pause")
        c = state.client()
        try:
            c.trigger_media_input_action(active_source(), "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE")
        except Exception as exc:
            raise HTTPException(503, f"OBS Pause thất bại: {exc}") from exc
        finally:
            c.base_client.ws.close()
        state.paused = True
        state.last_status = "Đã tạm dừng"
        return {"ok": True}


@router.post("/resume")
def resume_video():
    with state.lock:
        if not state.active or not state.paused:
            raise HTTPException(409, "Không có video tạm dừng để Resume")
        c = state.client()
        try:
            c.trigger_media_input_action(active_source(), "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY")
        except Exception as exc:
            raise HTTPException(503, f"OBS Resume thất bại: {exc}") from exc
        finally:
            c.base_client.ws.close()
        state.paused = False
        state.last_cursor = None
        state.last_progress_at = time.monotonic()
        state.last_status = "Đã tiếp tục phát"
        return {"ok": True}


@router.post("/previous")
def previous_video():
    with state.lock:
        if not state.active:
            raise HTTPException(409, "Chưa phát playlist")
        target = state.index - 1
        if target < 0:
            target = len(state.ids) - 1 if state.loop else 0
        return state.play(target)


@router.post("/select")
def play_selected(body: SelectPayload):
    with state.lock:
        if not state.active:
            raise HTTPException(409, "Hãy bắt đầu playlist trước")
        return state.play(body.index)


@router.post("/seek")
def seek_video(body: SeekPayload):
    with state.lock:
        if not state.active:
            raise HTTPException(409, "Chưa phát playlist")
        with SessionLocal() as db:
            item = db.get(Media, state.ids[state.index])
            if item is None:
                raise HTTPException(409, "Video không tồn tại")
            if item.duration_seconds is not None and body.milliseconds > int(item.duration_seconds * 1000):
                raise HTTPException(422, "Thời điểm tua vượt quá thời lượng video")
        c = state.client()
        try:
            c.set_media_input_cursor(active_source(), body.milliseconds)
        except Exception as exc:
            raise HTTPException(503, f"OBS Seek thất bại: {exc}") from exc
        finally:
            c.base_client.ws.close()
        state.last_progress_at = time.monotonic()
        state.last_cursor = body.milliseconds
        state.last_status = f"Đã tua đến {body.milliseconds / 1000:.1f}s"
        return {"ok": True}


@router.get("/products")
def available_products():
    with SessionLocal() as db:
        products = db.scalars(select(Product).order_by(Product.id)).all()
        counts = {}
        for row in db.scalars(select(ProductLiveVideo)).all():
            counts[row.product_id] = counts.get(row.product_id, 0) + 1
        return [{"id": p.id, "name": p.name, "sku": p.sku, "image_url": p.image_url,
                 "video_count": counts.get(p.id, 0)} for p in products]


@router.post("/product-session/start")
def start_product_session(body: ProductStartPayload):
    """Legacy ad-hoc start without a managed session (no lineup, no history). The web console starts
    sessions through /api/live/sessions/{id}/start instead."""
    with state.lock:
        if state.active:
            raise HTTPException(409, "Hãy Stop phiên phát hiện tại trước")
        ids = state.product_queue(body.product_id)
        state.ids = ids
        state.loop = True
        state.playlist_id = None
        state.pending_product_id = None
        state.session_mode = "product"
        state.session_id = None
        state.session_started_at = time.time()
        state.transition_count = state.recovery_count = state.error_count = 0
        state.product_id = body.product_id
        result = state.play(0)
        return result


@router.post("/product-session/switch")
def switch_product_session(body: ProductSwitchPayload):
    with state.lock:
        if state.session_id is not None:
            from app.live_sessions import switch_session_product, SwitchInput
            return switch_session_product(state.session_id, SwitchInput(product_id=body.product_id, mode=body.mode))
        if not state.active or state.session_mode != "product":
            raise HTTPException(409, "Chưa có phiên LIVE theo sản phẩm")
        state.product_queue(body.product_id)
        if body.mode == "immediate":
            return state.switch_product(body.product_id)
        if body.product_id == state.product_id:
            state.pending_product_id = None
            return {"ok": True, "pending_product_id": None}
        state.pending_product_id = body.product_id
        state.last_status = f"Đã xếp sản phẩm #{body.product_id} sau video hiện tại"
        return {"ok": True, "pending_product_id": body.product_id}


@router.post("/product-session/cancel-switch")
def cancel_product_switch():
    with state.lock:
        pending = state.pending_product_id
        state.pending_product_id = None
        if pending is not None and state.session_id is not None:
            from app.live_sessions import record_event
            record_event(state.session_id, "SWITCH_CANCELLED", pending)
        return {"ok": True}
