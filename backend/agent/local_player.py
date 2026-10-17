"""Agent-owned playlist using Windows-local MP4 files only.

One process owns OBS playback. After Agent restart the last planned playlist can
be inspected but is NOT automatically resumed: LIVE must require operator action.
"""
import json
import time
from pathlib import Path
from threading import RLock
from app.database import DATA_DIR
from app.live import state as obs_state
from agent.local_library import resolve as resolve_media

STATE_FILE=DATA_DIR/"agent_live_playlist.json"

class LocalPlayer:
    def __init__(self):
        self.lock=RLock()
        self.media_ids=[]
        self.index=0
        self.active=False
        self.paused=False
        self.loop=False
        self.last_transition=0.0
        self.last_error=None
        self.recovery=None
        try:
            self.recovery=json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError,ValueError,TypeError):
            pass

    def _persist(self):
        data={"media_ids":self.media_ids,"index":self.index,
              "loop":self.loop,"was_active":self.active}
        temp=STATE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
        temp.replace(STATE_FILE)

    def _load_current(self):
        path=resolve_media(self.media_ids[self.index])
        with obs_state.lock:
            if obs_state.active:
                raise ValueError("Phiên LIVE local cũ đang chiếm OBS")
            client=obs_state.client()
            try:
                source=obs_state.config.input_name
                client.get_input_settings(source)
                client.set_input_settings(source,{"local_file":str(path),
                    "is_local_file":True,"looping":False,"close_when_inactive":False},
                    overlay=True)
                client.trigger_media_input_action(
                    source,"OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
            finally:
                client.base_client.ws.close()
        self.last_transition=time.monotonic()

    def playlist(self,media_ids:list[str],loop:bool=True):
        if not media_ids or len(media_ids)>300:
            raise ValueError("Playlist cần 1 đến 300 video")
        # Check all paths before replacing current playlist.
        for media_id in media_ids:
            resolve_media(media_id)
        with self.lock:
            if self.active:
                raise ValueError("Đang phát playlist; hãy dừng trước khi bắt đầu playlist mới")
            self.media_ids=list(media_ids)
            self.index=0
            self.loop=bool(loop)
            self.last_error=None
            self._load_current()
            self.active=True
            self.paused=False
            self.recovery=None
            self._persist()
            return self.status()

    def play(self,path_or_media_id:str):
        # Caller passes a media ID, not an arbitrary file path.
        return self.playlist([path_or_media_id],loop=False)

    def next(self):
        with self.lock:
            if not self.active:raise ValueError("Chưa phát playlist")
            following=self.index+1
            if following>=len(self.media_ids):
                if not self.loop:
                    return self.stop()
                following=0
            self.index=following
            self._load_current()
            self.paused=False
            self._persist()
            return self.status()

    def previous(self):
        with self.lock:
            if not self.active:raise ValueError("Chưa phát playlist")
            self.index=(self.index-1)%len(self.media_ids)
            self._load_current()
            self.paused=False
            self._persist()
            return self.status()

    def pause(self):
        with self.lock,obs_state.lock:
            if not self.active:raise ValueError("Chưa phát playlist")
            if not self.paused:
                client=obs_state.client()
                try:
                    client.trigger_media_input_action(
                        obs_state.config.input_name,"OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE")
                finally:
                    client.base_client.ws.close()
            self.paused=True
            return self.status()

    def resume(self):
        with self.lock,obs_state.lock:
            if not self.active:raise ValueError("Chưa phát playlist")
            if self.paused:
                client=obs_state.client()
                try:
                    client.trigger_media_input_action(
                        obs_state.config.input_name,"OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY")
                finally:
                    client.base_client.ws.close()
            self.paused=False
            self.last_transition=time.monotonic()
            return self.status()

    def tick(self):
        with self.lock:
            if not self.active or self.paused or time.monotonic()-self.last_transition<2:
                return
            with obs_state.lock:
                client=obs_state.client()
                try:
                    response=client.get_media_input_status(obs_state.config.input_name)
                    media_state=str(response.media_state).upper()
                finally:
                    client.base_client.ws.close()
            if media_state in ("OBS_MEDIA_STATE_ENDED","OBS_MEDIA_STATE_STOPPED","ENDED","STOPPED"):
                return self.next()

    def stop(self):
        with self.lock:
            if self.active:
                with obs_state.lock:
                    client=obs_state.client()
                    try:
                        client.trigger_media_input_action(
                            obs_state.config.input_name,
                            "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP")
                    finally:
                        client.base_client.ws.close()
            self.active=False
            self.paused=False
            self.recovery=None
            self._persist()
            return self.status()

    def status(self):
        with self.lock:
            return {"active":self.active,"media_ids":list(self.media_ids),
                    "index":self.index,"count":len(self.media_ids),
                    "loop":self.loop,"paused":self.paused,"needs_manual_resume":bool(
                        self.recovery and self.recovery.get("was_active") and not self.active)}

local_player=LocalPlayer()
