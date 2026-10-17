"""Agent-owned local playback without Cloud video storage."""
from threading import RLock
from app.live import state as obs_state

class LocalPlayer:
    def __init__(self):
        self.lock=RLock()
        self.current=None
        self.active=False

    def play(self,path:str):
        with self.lock,obs_state.lock:
            if obs_state.active:
                raise ValueError("Phiên LIVE cũ đang chiếm OBS")
            client=obs_state.client()
            try:
                source=obs_state.config.input_name
                client.get_input_settings(source)
                client.set_input_settings(source,{"local_file":path,"is_local_file":True,
                    "looping":False,"close_when_inactive":False},overlay=True)
                client.trigger_media_input_action(source,"OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
            finally:
                client.base_client.ws.close()
            self.current=path
            self.active=True
            return {"active":True,"message":"Đã gửi video local đến OBS"}

    def next(self):
        raise ValueError("Chưa có playlist local theo Agent; hãy chọn video từ Web")

    def stop(self):
        with self.lock:
            if self.active:
                with obs_state.lock:
                    client=obs_state.client()
                    try:
                        client.trigger_media_input_action(obs_state.config.input_name,
                            "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP")
                    finally:
                        client.base_client.ws.close()
            self.active=False
            self.current=None
            return {"active":False}

local_player=LocalPlayer()
