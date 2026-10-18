"""DB-free OBS playback engine that runs on the Windows Agent.

Port of ``app.live.PlayState`` (product queue looping, pending "after_current"
switch, dual A/B Media Sources, watchdog with stall recovery and bounded
retries) without SQLAlchemy or a process-global OBS. Videos are addressed by
Agent media ids (64-hex sha256 of the local path) and turned into files by the
``resolve`` callable; business state (sessions, lineups, history) stays on the
VPS, which drives this engine through Agent commands.

Public API (kept stable for the cloud LIVE facade):
    PlaybackEngine(resolve, on_event=None, config_file=None, client_factory=None)
    start_playlist(media_ids, loop=True, start_index=0, playlist_id=None)
    product_play(session_id, product_id, media_ids, mode)  # 'immediate' | 'after_current'
    cancel_pending(), pause(), resume(), next(), previous(), select(index), seek(ms), stop()
    configure(...), list_inputs(host, port, password), status(), tick(), start_monitor()

Errors are raised as ``PlaybackError(status_code, detail)``. Events passed to
``on_event`` are dicts ``{type, session_id, product_id, detail}`` with type
PRODUCT_SWITCHED, PLAYBACK_ENDED or ERROR.
"""
import json
import threading
import time
from pathlib import Path
from typing import Callable

import obsws_python as obs
from pydantic import BaseModel, Field

from app.config import settings
from app.database import DATA_DIR

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
OBS_CONFIG_FILE = DATA_DIR / "obs_config.json"


class PlaybackError(Exception):
    """Same shape as fastapi.HTTPException so callers can map it 1:1."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ObsConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=4455, ge=1, le=65535)
    password: str = ""
    input_name: str = "Sarae Video"
    dual_mode: bool = False
    # A/B Media Source names for dual mode; empty falls back to "<input_name> A" / "<input_name> B".
    source_a: str = Field(default="", max_length=200)
    source_b: str = Field(default="", max_length=200)


def load_obs_config(path: Path = OBS_CONFIG_FILE) -> ObsConfig:
    default = ObsConfig(host=settings.obs_host, port=settings.obs_port,
                        password=settings.obs_password, input_name=settings.obs_input_name)
    try:
        return ObsConfig.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        # A corrupt file must not stop the Agent from starting; the operator can save the config again.
        return default


def save_obs_config(config: ObsConfig, path: Path = OBS_CONFIG_FILE):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _default_client(config: ObsConfig):
    # OBS WebSocket 5.x. No output or streaming controls.
    return obs.ReqClient(host=config.host, port=config.port, password=config.password, timeout=4)


def _close(client):
    try:
        client.base_client.ws.close()
    except Exception:
        pass


class PlaybackEngine:
    def __init__(self, resolve: Callable[[str], Path | None], on_event: Callable[[dict], None] | None = None,
                 config_file: Path | None = None, client_factory: Callable[[ObsConfig], object] | None = None):
        self.resolve = resolve
        self.on_event = on_event
        self.config_file = Path(config_file) if config_file else OBS_CONFIG_FILE
        self.client_factory = client_factory or _default_client
        self.lock = threading.RLock()
        self.config = load_obs_config(self.config_file)
        self.playlist_id: int | None = None
        self.index = 0
        self.ids: list[str] = []
        self.active = False
        self.paused = False
        self.last_status = ""
        self.generation = 0
        self.dual_mode = self.config.dual_mode
        self.active_slot = 0
        self.slots: list[str] = []
        self.scene_item_ids: list[int] = []
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
        # Lineups pushed by the VPS: the full ordered media list per product, re-checked at each video end.
        self.product_media: dict[int, list[str]] = {}
        self._monitor_thread = None
        self._monitor_stop = threading.Event()

    # ----- helpers -------------------------------------------------------------------------------

    def _emit(self, kind: str, detail: dict | None = None, product_id=None):
        if self.on_event is None:
            return
        try:
            self.on_event({"type": kind, "session_id": self.session_id,
                           "product_id": self.product_id if product_id is None else product_id,
                           "detail": detail or {}})
        except Exception:
            pass

    def _path(self, media_id: str) -> Path | None:
        try:
            path = self.resolve(media_id)
        except Exception:
            return None
        if path is None:
            return None
        path = Path(path)
        return path if path.is_file() else None

    def _valid_ids(self, media_ids: list[str]) -> list[str]:
        return [m for m in media_ids if self._path(m) is not None]

    def product_queue(self, product_id) -> list[str]:
        ids = self._valid_ids(self.product_media.get(product_id, []))
        if not ids:
            raise PlaybackError(409, f"Sản phẩm #{product_id} chưa có video hợp lệ")
        return ids

    def client(self):
        return self.client_factory(self.config)

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
        media_id = self.ids[index]
        path = self._path(media_id)
        if path is None:
            raise PlaybackError(409, f"Thiếu file video {media_id[:12]}")
        path = path.resolve()
        return str(path), path.name

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
                raise PlaybackError(422, "Cần có đủ hai Media Source " + " và ".join(self._sources()) + " trong scene OBS hiện tại")
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
            except PlaybackError as exc:
                self.last_status = f"Không preload được video kế: {exc.detail}"

    def active_source(self):
        return self.slots[self.active_slot] if self.dual_mode and self.slots else self.config.input_name

    def _media_action(self, action: str, label: str):
        c = None
        try:
            c = self.client()
            c.trigger_media_input_action(self.active_source(), action)
        except Exception as exc:
            raise PlaybackError(503, f"OBS {label} thất bại: {exc}") from exc
        finally:
            if c is not None:
                _close(c)

    def _deactivate(self, reason: str, message: str):
        """Engine-initiated end of playback (not an explicit stop command)."""
        self.active = False
        self.paused = False
        self.last_status = message
        self._emit("PLAYBACK_ENDED", {"reason": reason, "message": message})

    # ----- status ---------------------------------------------------------------------------------

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
        """Same shape as GET /api/live/status."""
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
                    _close(c)
            except Exception as exc:
                return {"connected": False, "error": str(exc), "running": self.active,
                        "playlist_id": self.playlist_id, "index": self.index,
                        "count": len(self.ids), "input_name": self.config.input_name,
                        "last_status": self.last_status, **self.metrics()}

    # ----- core playback --------------------------------------------------------------------------

    def play(self, index: int):
        with self.lock:
            if not self.ids:
                raise PlaybackError(409, "Playlist rỗng")
            if not 0 <= index < len(self.ids):
                raise PlaybackError(422, "Vị trí video không hợp lệ")
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
                    _close(client)
            except PlaybackError:
                raise
            except Exception as exc:
                self.last_status = str(exc)
                raise PlaybackError(503, f"Không điều khiển được OBS: {exc}") from exc
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
            return {"ok": True, "index": index, "filename": name, "media_id": self.ids[index]}

    def _begin_session(self, ids, *, mode, loop, playlist_id=None, session_id=None, product_id=None):
        self.session_mode = mode
        self.session_id = session_id
        self.product_id = product_id
        self.pending_product_id = None
        self.ids = ids
        self.playlist_id = playlist_id
        self.loop = loop
        self.paused = False
        self.session_started_at = time.time()
        self.transition_count = self.recovery_count = self.error_count = 0

    def start_playlist(self, media_ids: list[str], loop: bool = True, start_index: int = 0, playlist_id=None):
        with self.lock:
            if self.active:
                raise PlaybackError(409, "Hãy dừng playlist hiện tại trước")
            if not media_ids:
                raise PlaybackError(422, "Playlist cần ít nhất 1 video")
            missing = [m for m in media_ids if self._path(m) is None]
            if missing:
                raise PlaybackError(409, f"Thiếu {len(missing)} video trên máy Agent")
            self._begin_session(list(media_ids), mode="playlist", loop=bool(loop), playlist_id=playlist_id)
            return self.play(start_index)

    def switch_product(self, product_id, trigger: str | None = None):
        """Play product_id from its first video. ``trigger`` set means a pending switch was applied."""
        ids = self.product_queue(product_id)
        old_ids, old_index, old_product = self.ids, self.index, self.product_id
        self.ids = ids
        try:
            result = self.play(0)
        except Exception:
            self.ids, self.index, self.product_id = old_ids, old_index, old_product
            raise
        previous = self.product_id
        self.product_id = product_id
        self.pending_product_id = None
        self.session_mode = "product"
        if trigger is not None:
            self._emit("PRODUCT_SWITCHED", {"trigger": trigger, "from_product_id": previous,
                                            "media_id": result["media_id"]})
        return result

    def product_play(self, session_id: int, product_id: int, media_ids: list[str], mode: str = "immediate"):
        """Start a product LIVE session, or switch product inside the running one.

        mode 'immediate' plays the product now; 'after_current' queues it until the current
        video ends (then PRODUCT_SWITCHED is emitted). When nothing runs, it always starts now.
        """
        if mode not in ("immediate", "after_current"):
            raise PlaybackError(422, "mode phải là immediate hoặc after_current")
        with self.lock:
            if self.active and self.session_mode != "product":
                raise PlaybackError(409, "Đang phát playlist; hãy dừng trước khi phát sản phẩm")
            if self.active and self.session_id != session_id:
                raise PlaybackError(409, f"Phiên LIVE #{self.session_id} đang phát; hãy dừng trước")
            previous_lineup = self.product_media.get(product_id)
            self.product_media[product_id] = list(media_ids)
            try:
                self.product_queue(product_id)
            except PlaybackError:
                if previous_lineup is None:
                    self.product_media.pop(product_id, None)
                else:
                    self.product_media[product_id] = previous_lineup
                raise
            if not self.active:
                ids = self.product_queue(product_id)
                self._begin_session(ids, mode="product", loop=True, session_id=session_id, product_id=product_id)
                try:
                    return self.play(0)
                except Exception:
                    self.product_id = None
                    raise
            if mode == "immediate":
                return self.switch_product(product_id)
            if product_id == self.product_id:
                # Same product: the new lineup applies at the next video boundary.
                self.pending_product_id = None
                return {"ok": True, "pending_product_id": None}
            self.pending_product_id = product_id
            self.last_status = f"Đã xếp sản phẩm #{product_id} sau video hiện tại"
            return {"ok": True, "pending_product_id": product_id}

    def cancel_pending(self):
        with self.lock:
            pending = self.pending_product_id
            self.pending_product_id = None
            return {"ok": True, "cancelled_product_id": pending}

    def pause(self):
        with self.lock:
            if not self.active or self.paused:
                raise PlaybackError(409, "Không có video đang phát để Pause")
            self._media_action("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE", "Pause")
            self.paused = True
            self.last_status = "Đã tạm dừng"
            return {"ok": True}

    def resume(self):
        with self.lock:
            if not self.active or not self.paused:
                raise PlaybackError(409, "Không có video tạm dừng để Resume")
            self._media_action("OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY", "Resume")
            self.paused = False
            self.last_cursor = None
            self.last_progress_at = time.monotonic()
            self.last_status = "Đã tiếp tục phát"
            return {"ok": True}

    def next(self):
        with self.lock:
            if not self.active:
                raise PlaybackError(409, "Chưa phát playlist")
            if self.pending_product_id is not None:
                return self.switch_product(self.pending_product_id, trigger="next")
            if self.index + 1 >= len(self.ids):
                if self.loop:
                    return self.play(0)
                return self.stop()
            return self.play(self.index + 1)

    def previous(self):
        with self.lock:
            if not self.active:
                raise PlaybackError(409, "Chưa phát playlist")
            target = self.index - 1
            if target < 0:
                target = len(self.ids) - 1 if self.loop else 0
            return self.play(target)

    def select(self, index: int):
        with self.lock:
            if not self.active:
                raise PlaybackError(409, "Hãy bắt đầu playlist trước")
            return self.play(index)

    def seek(self, milliseconds: int):
        with self.lock:
            if not self.active:
                raise PlaybackError(409, "Chưa phát playlist")
            if milliseconds < 0:
                raise PlaybackError(422, "Thời điểm tua không hợp lệ")
            c = None
            try:
                c = self.client()
                # OBS knows the duration of the loaded file; the Agent has no media DB.
                try:
                    duration = getattr(c.get_media_input_status(self.active_source()), "media_duration", None)
                except Exception:
                    duration = None
                if isinstance(duration, (int, float)) and duration > 0 and milliseconds > duration:
                    raise PlaybackError(422, "Thời điểm tua vượt quá thời lượng video")
                c.set_media_input_cursor(self.active_source(), milliseconds)
            except PlaybackError:
                raise
            except Exception as exc:
                raise PlaybackError(503, f"OBS Seek thất bại: {exc}") from exc
            finally:
                if c is not None:
                    _close(c)
            self.last_progress_at = time.monotonic()
            self.last_cursor = milliseconds
            self.last_status = f"Đã tua đến {milliseconds / 1000:.1f}s"
            return {"ok": True}

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
                    _close(client)
            except Exception as exc:
                raise PlaybackError(503, f"Không dừng được OBS: {exc}") from exc
            self.retry_count = 0
            self.pending_product_id = None
            self.last_status = "Đã dừng"
            return {"ok": True}

    # ----- OBS configuration ----------------------------------------------------------------------

    def configure(self, host="127.0.0.1", port=4455, password="", input_name="Sarae Video",
                  dual_mode=False, source_a="", source_b=""):
        """Save the OBS connection locally. An empty password keeps the saved one."""
        try:
            body = ObsConfig(host=host, port=port, password=password or "", input_name=input_name,
                             dual_mode=dual_mode, source_a=source_a or "", source_b=source_b or "")
        except Exception as exc:
            raise PlaybackError(422, f"Cấu hình OBS không hợp lệ: {exc}") from exc
        if body.host not in LOCAL_HOSTS:
            raise PlaybackError(422, "Chỉ hỗ trợ OBS trên cùng máy Agent")
        if not body.input_name.strip():
            raise PlaybackError(422, "Thiếu tên Media Source")
        if body.dual_mode:
            a, b = body.source_a.strip() or body.input_name + " A", body.source_b.strip() or body.input_name + " B"
            if a == b:
                raise PlaybackError(422, "Hai Media Source A và B phải khác tên nhau")
        with self.lock:
            if not body.password:
                body = body.model_copy(update={"password": self.config.password})
            self.active = False
            self.config = body
            self.dual_mode = body.dual_mode
            self.slots = []
            self.last_status = "Đã cập nhật cấu hình OBS"
            try:
                save_obs_config(body, self.config_file)
            except OSError as exc:
                self.last_status = f"Đã cập nhật cấu hình OBS nhưng không lưu được xuống đĩa: {exc}"
        return self.status()

    def list_inputs(self, host="127.0.0.1", port=4455, password=""):
        """List OBS inputs, marking those placed in the current program scene. Saved config is untouched."""
        if host not in LOCAL_HOSTS:
            raise PlaybackError(422, "Chỉ hỗ trợ OBS trên cùng máy Agent")
        config = ObsConfig(host=host, port=port, password=password or self.config.password)
        try:
            client = self.client_factory(config)
        except Exception as exc:
            raise PlaybackError(503, f"Không kết nối được OBS: {exc}") from exc
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
            raise PlaybackError(502, f"OBS từ chối yêu cầu: {exc}") from exc
        finally:
            _close(client)

    # ----- watchdog -------------------------------------------------------------------------------

    def tick(self):
        """One watchdog iteration: advance ended videos, recover stalls, retry OBS with backoff."""
        with self.lock:
            if not self.active or self.paused or not self.ids or time.monotonic() < self.next_retry_at:
                return
            try:
                client = self.client()
                try:
                    source = self.active_source()
                    report = client.get_media_input_status(source)
                    media_status = report.media_state
                finally:
                    _close(client)
                cursor = getattr(report, "media_cursor", None)
                now = time.monotonic()
                if media_status == "OBS_MEDIA_STATE_PLAYING" and isinstance(cursor, (int, float)):
                    if self.last_cursor is None or cursor > self.last_cursor + 100 or cursor < self.last_cursor - 500:
                        self.last_cursor = cursor
                        self.last_progress_at = now
                    elif now - self.last_progress_at >= 20:
                        if self.stall_restarts >= 2:
                            self._deactivate("stalled", "Watchdog: video đứng sau 2 lần thử khôi phục")
                            return
                        restore = self.client()
                        try:
                            restore.trigger_media_input_action(source, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
                        finally:
                            _close(restore)
                        self.stall_restarts += 1
                        self.recovery_count += 1
                        self.last_cursor = None
                        self.last_progress_at = now
                        self.last_status = "Watchdog: đã restart video bị đứng"
                elif media_status in ("OBS_MEDIA_STATE_PAUSED", "OBS_MEDIA_STATE_STOPPED"):
                    self.last_cursor = None
                    self.last_progress_at = now
                if self.retry_count:
                    self.recovery_count += 1
                    self.last_cursor = None
                    self.last_progress_at = now
                    self.last_status = "OBS đã kết nối lại; tiếp tục theo dõi"
                self.retry_count = 0
                self.next_retry_at = 0
                if media_status == "OBS_MEDIA_STATE_ENDED":
                    self._advance_after_end()
            except Exception as exc:
                self.retry_count += 1
                self.error_count += 1
                self.last_status = f"OBS lỗi (thử lại {self.retry_count}/5): {exc}"
                if self.retry_count >= 5:
                    self._deactivate("obs_error", f"Đã tạm dừng do OBS lỗi nhiều lần: {exc}")
                else:
                    self.next_retry_at = time.monotonic() + min(2 ** self.retry_count, 20)

    def _advance_after_end(self):
        if self.session_mode == "product" and self.pending_product_id is not None:
            pending = self.pending_product_id
            try:
                self.switch_product(pending, trigger="video_end")
                return
            except PlaybackError as exc:
                self.pending_product_id = None
                self.last_status = f"Chuyển sản phẩm lỗi: {exc.detail}; tiếp tục sản phẩm cũ"
                self._emit("ERROR", {"code": "switch_failed", "pending_product_id": pending,
                                     "message": exc.detail})
        if self.session_mode == "product":
            try:
                refreshed = self.product_queue(self.product_id)
                previous_id = self.ids[self.index]
                self.index = refreshed.index(previous_id) if previous_id in refreshed else -1
                self.ids = refreshed
            except PlaybackError as exc:
                self._deactivate("no_media", f"Dừng phiên: {exc.detail}")
                return
        target = self.index + 1
        if target >= len(self.ids):
            if not self.loop:
                self._deactivate("playlist_end", "Đã hết playlist")
                return
            target = 0
        # Skips missing media files, up to one complete playlist traversal.
        for shift in range(len(self.ids)):
            candidate = (target + shift) % len(self.ids)
            if not self.loop and candidate < target:
                break
            try:
                self.play(candidate)
                return
            except PlaybackError as exc:
                if exc.status_code == 409:
                    self.last_status = f"Bỏ qua video lỗi: {exc.detail}"
                    continue
                raise
        self._deactivate("no_media", "Không còn video hợp lệ để phát")

    def start_monitor(self, interval: float = 1.0):
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_stop.clear()

        def run():
            while not self._monitor_stop.wait(interval):
                try:
                    self.tick()
                except Exception:
                    pass

        self._monitor_thread = threading.Thread(target=run, daemon=True, name="sarae-playback-monitor")
        self._monitor_thread.start()

    def stop_monitor(self):
        self._monitor_stop.set()
