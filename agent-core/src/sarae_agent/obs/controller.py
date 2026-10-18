"""Single persistent obs-websocket connection shared by playback and TTS.

* One lock serialises every request (the websocket client is not thread-safe).
* Any transport failure drops the client; the next call reconnects (rate-limited).
* Success is only reported after OBS answers the request; settings changes are read back.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from sarae_agent.activation.secrets import obs_secret_file
from sarae_agent.config import ConfigStore
from sarae_agent.errors import AgentError

log = logging.getLogger("sarae.obs")

MEDIA_ACTIONS = {
    "play": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY",
    "pause": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE",
    "stop": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP",
    "restart": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART",
}
ENDED_STATES = {"OBS_MEDIA_STATE_ENDED", "OBS_MEDIA_STATE_STOPPED", "OBS_MEDIA_STATE_NONE"}


def _short(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}"


class ObsController:
    RECONNECT_MIN_SECONDS = 3.0

    def __init__(self, config: ConfigStore, emit: Callable[[str, dict], None], client_factory=None):
        self._config = config
        self._emit = emit
        self._lock = threading.RLock()
        self._client = None
        self._last_attempt = 0.0
        self._factory = client_factory
        self._status: dict = {"connected": False, "state": "disconnected", "error": None,
                              "obs_version": None, "websocket_version": None}
        self.wanted = config.get().obs.auto_connect  # keep reconnecting in the background

    # --- secrets -------------------------------------------------------------------------
    @staticmethod
    def password_set() -> bool:
        return obs_secret_file.exists()

    @staticmethod
    def _password() -> str:
        try:
            data = obs_secret_file.load() or {}
        except AgentError:
            return ""
        return str(data.get("password") or "")

    @staticmethod
    def set_password(password: str | None) -> None:
        if password:
            obs_secret_file.save({"password": password})
        else:
            obs_secret_file.delete()

    # --- connection ----------------------------------------------------------------------
    def _set_status(self, **changes: Any) -> None:
        before = (self._status.get("connected"), self._status.get("state"), self._status.get("error"))
        self._status.update(changes)
        after = (self._status.get("connected"), self._status.get("state"), self._status.get("error"))
        if before != after:
            self._emit("obs.status_changed", self.status())

    def _make_client(self):
        cfg = self._config.get().obs
        if self._factory:
            return self._factory(cfg, self._password())
        import obsws_python as obs
        return obs.ReqClient(host=cfg.host, port=cfg.port, password=self._password(), timeout=4)

    def _drop(self, reason: str | None) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._set_status(connected=False, state="disconnected" if reason is None else "error", error=reason)

    def connect(self, *, force: bool = True) -> dict:
        with self._lock:
            if self._client is not None and not force:
                return self.status()
            self._drop(None)
            self.wanted = True
            self._last_attempt = time.monotonic()
            self._set_status(state="connecting", error=None)
            try:
                self._client = self._make_client()
                version = self._client.send("GetVersion", raw=True) or {}
            except Exception as exc:  # noqa: BLE001
                self._client = None
                code, message = self._classify_connect(exc)
                self._set_status(connected=False, state="error", error={"code": code, "message": message})
                raise AgentError(code, message) from exc
            self._set_status(connected=True, state="connected", error=None,
                             obs_version=version.get("obsVersion"),
                             websocket_version=version.get("obsWebSocketVersion"))
            log.info("Đã kết nối OBS %s (websocket %s)", version.get("obsVersion"),
                     version.get("obsWebSocketVersion"))
            return self.status()

    def disconnect(self) -> dict:
        with self._lock:
            self.wanted = False
            self._drop(None)
            return self.status()

    @staticmethod
    def _classify_connect(exc: BaseException) -> tuple[str, str]:
        text = str(exc).lower()
        if isinstance(exc, ConnectionRefusedError) or "refused" in text or "10061" in text:
            return "OBS_NOT_RUNNING", "OBS chưa chạy hoặc chưa bật WebSocket Server (Tools → WebSocket Server Settings)"
        if "authentication" in text or "auth" in text or "4009" in text:
            return "OBS_AUTH_FAILED", "Sai mật khẩu OBS WebSocket"
        if isinstance(exc, TimeoutError) or "timed out" in text or "timeout" in text:
            return "OBS_TIMEOUT", "OBS không phản hồi (kiểm tra host/port)"
        return "OBS_CONNECT_FAILED", f"Không kết nối được OBS: {_short(exc)}"

    def _call(self, request: str, data: dict | None = None) -> dict:
        with self._lock:
            if self._client is None:
                if time.monotonic() - self._last_attempt < self.RECONNECT_MIN_SECONDS and self._status.get("error"):
                    raise AgentError("OBS_DISCONNECTED", "Chưa kết nối OBS", {"last_error": self._status.get("error")})
                self.connect()
            try:
                return self._client.send(request, data, raw=True) or {}
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "code", None)
                if isinstance(code, int):  # OBSSDKRequestError: OBS answered with a failure status
                    if code == 600:
                        name = (data or {}).get("inputName", "")
                        raise AgentError("OBS_INPUT_MISSING",
                                         f"OBS không có nguồn '{name}'. Hãy tạo Media Source đúng tên này",
                                         {"input": name}) from exc
                    raise AgentError("OBS_REQUEST_FAILED", f"OBS từ chối {request} (mã {code})",
                                     {"obs_code": code}) from exc
                self._drop({"code": "OBS_DISCONNECTED", "message": _short(exc)})
                log.warning("Mất kết nối OBS: %s", _short(exc), extra={"code": "OBS_DISCONNECTED"})
                raise AgentError("OBS_DISCONNECTED", "Mất kết nối OBS", {"reason": _short(exc)},
                                 retryable=True) from exc

    def ensure_connected(self) -> bool:
        """Background reconnect used by the watchdog; never raises."""
        with self._lock:
            if self._client is not None:
                try:
                    self._client.send("GetVersion", raw=True)
                    return True
                except Exception as exc:  # noqa: BLE001
                    self._drop({"code": "OBS_DISCONNECTED", "message": _short(exc)})
            if not self.wanted or time.monotonic() - self._last_attempt < 10:
                return False
            try:
                self.connect()
                return True
            except AgentError:
                return False

    # --- queries -------------------------------------------------------------------------
    def status(self) -> dict:
        cfg = self._config.get().obs
        return {**self._status, "host": cfg.host, "port": cfg.port, "media_input": cfg.media_input,
                "voice_input": cfg.voice_input, "password_set": self.password_set(), "auto_connect": self.wanted}

    def inspect(self) -> dict:
        """Connection + presence of the configured media/voice inputs (for obs.status)."""
        cfg = self._config.get().obs
        result = self.status()
        if not self._status.get("connected"):
            return result
        for key, name in (("media_input_ok", cfg.media_input), ("voice_input_ok", cfg.voice_input)):
            try:
                self._call("GetInputSettings", {"inputName": name})
                result[key] = True
            except AgentError as exc:
                result[key] = False if exc.code == "OBS_INPUT_MISSING" else None
        try:
            result["media"] = self.media_status(cfg.media_input)
        except AgentError:
            result["media"] = None
        result.update({k: v for k, v in self.status().items() if k in ("connected", "state", "error")})
        return result

    def media_status(self, input_name: str) -> dict:
        data = self._call("GetMediaInputStatus", {"inputName": input_name})
        return {"state": data.get("mediaState"), "duration_ms": data.get("mediaDuration"),
                "cursor_ms": data.get("mediaCursor")}

    # --- commands ------------------------------------------------------------------------
    def play_file(self, input_name: str, path: str, *, unmute: bool = False) -> dict:
        self._call("GetInputSettings", {"inputName": input_name})  # fail fast when missing
        self._call("SetInputSettings", {"inputName": input_name, "overlay": True, "inputSettings": {
            "local_file": path, "is_local_file": True, "looping": False,
            "close_when_inactive": False, "restart_on_activate": False}})
        applied = self._call("GetInputSettings", {"inputName": input_name}).get("inputSettings", {})
        if str(applied.get("local_file", "")).replace("\\", "/").casefold() != path.replace("\\", "/").casefold():
            raise AgentError("OBS_NOT_APPLIED", "OBS không nhận file mới cho nguồn phát", {"input": input_name})
        if unmute:
            self._call("SetInputMute", {"inputName": input_name, "inputMuted": False})
        self._call("TriggerMediaInputAction", {"inputName": input_name, "mediaAction": MEDIA_ACTIONS["restart"]})
        return self.media_status(input_name)

    def media_action(self, input_name: str, action: str) -> dict:
        if action not in MEDIA_ACTIONS:
            raise AgentError("INVALID_REQUEST", f"Hành động media không hợp lệ: {action}")
        self._call("TriggerMediaInputAction", {"inputName": input_name, "mediaAction": MEDIA_ACTIONS[action]})
        return self.media_status(input_name)

    def close(self) -> None:
        with self._lock:
            self.wanted = False
            self._drop(None)
