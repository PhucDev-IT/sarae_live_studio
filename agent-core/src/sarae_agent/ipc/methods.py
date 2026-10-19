"""IPC method table: name -> (params model, handler). The GUI can call nothing else.

There is deliberately no method that runs a shell command or opens an arbitrary path for
writing; file paths accepted here are validated by the owning service.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from sarae_agent.errors import AgentError
from sarae_agent.ipc.server import IpcServer
from sarae_agent.logging_setup import event_handler
from sarae_agent.runtime import AgentRuntime


class Strict(BaseModel):
    model_config = {"extra": "forbid"}


class ConfigSetParams(Strict):
    cloud: dict | None = None
    obs: dict | None = None
    media: dict | None = None
    monitor: dict | None = None
    agent: dict | None = None
    obs_password: str | None = Field(default=None, max_length=256)
    clear_obs_password: bool = False


class ActivateParams(Strict):
    activation_key: str = Field(min_length=1, max_length=200)
    cloud_url: str | None = Field(default=None, max_length=300)


class FolderParams(Strict):
    path: str = Field(min_length=1, max_length=1000)


class MediaListParams(Strict):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=1000)
    query: str = Field(default="", max_length=200)


class PlayParams(Strict):
    media_id: str = Field(pattern=r"^[a-f0-9]{64}$")


class PlaylistParams(Strict):
    media_ids: list[str] = Field(min_length=1, max_length=300)
    loop: bool = True


class LoopParams(Strict):
    loop: bool


class MonitorConfigParams(Strict):
    interval_seconds: float | None = Field(default=None, ge=0.5, le=15)
    threshold: float | None = Field(default=None, ge=0.65, le=0.99)
    telegram_enabled: bool | None = None
    telegram_chat_id: str | None = Field(default=None, max_length=50)
    autostart: bool | None = None


class SpeakParams(Strict):
    text: str = Field(min_length=1, max_length=450)
    voice: Literal["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"] = "vi-VN-HoaiMyNeural"


class AutoPinOpenParams(Strict):
    url: str = Field(min_length=12, max_length=1000)


class AutoPinLoginParams(Strict):
    url: str = Field(default="https://shop.tiktok.com/streamer/live/product/dashboard", min_length=12, max_length=1000)

class AutoPinConfigureParams(Strict):
    products: list[str] = Field(min_length=1, max_length=100)
    interval_seconds: int = Field(ge=5, le=3600)

class LogsParams(Strict):
    limit: int = Field(default=200, ge=1, le=500)


class ObsConnectParams(Strict):
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    password: str | None = Field(default=None, max_length=256)


def register_all(server: IpcServer, rt: AgentRuntime) -> None:
    reg = server.register

    # system
    reg("system.status", lambda _: rt.system_status())
    reg("system.ping", lambda _: {"pong": True})
    reg("system.methods", lambda _: {"methods": server.methods, "cloud_command_kinds": rt.router.kinds})
    reg("system.shutdown", lambda _: {"accepted": True})

    # config
    def config_set(p: ConfigSetParams) -> dict:
        patch = {k: v for k, v in p.model_dump(include={"cloud", "obs", "media", "monitor", "agent"}).items()
                 if v is not None}
        if "media" in patch and "folders" in patch["media"]:
            raise AgentError("INVALID_PARAMS", "Dùng media.folders.add/remove để đổi thư mục")
        if "cloud" in patch and "api_url" in patch["cloud"] and rt.worker is not None:
            raise AgentError("AGENT_RUNNING", "Dừng Agent trước khi đổi Cloud URL")
        if patch:
            rt.config.update(patch)
        if p.clear_obs_password:
            rt.obs.set_password(None)
        elif p.obs_password:
            rt.obs.set_password(p.obs_password)
        if "obs" in patch or p.clear_obs_password or p.obs_password:
            rt.reload_obs_settings()
        return rt.config_view()

    reg("config.get", lambda _: rt.config_view())
    reg("config.set", config_set, ConfigSetParams)

    # activation / agent lifecycle
    def activate(p: ActivateParams) -> dict:
        if rt.worker is not None:
            raise AgentError("AGENT_RUNNING", "Dừng Agent trước khi kích hoạt")
        if p.cloud_url is not None:
            rt.config.update({"cloud": {"api_url": p.cloud_url}})
        url = rt.config.get().cloud.api_url
        if not url:
            raise AgentError("CLOUD_URL_MISSING", "Nhập Cloud API URL trước khi kích hoạt")
        result = rt.activation.activate(url, p.activation_key)
        rt.emit("agent.status_changed", rt.agent_status())
        return result

    def deactivate(_) -> dict:
        if rt.worker is not None:
            raise AgentError("AGENT_RUNNING", "Dừng Agent trước khi gỡ kích hoạt")
        result = rt.activation.deactivate_local()
        rt.emit("agent.status_changed", rt.agent_status())
        return result

    reg("activation.status", lambda _: rt.activation.status())
    reg("activation.activate", activate, ActivateParams)
    reg("activation.deactivate", deactivate)
    reg("agent.start", lambda _: rt.start_agent())
    reg("agent.stop", lambda _: rt.stop_agent())
    reg("agent.restart", lambda _: rt.restart_agent())
    reg("agent.status", lambda _: {**rt.agent_status(), "cloud": rt.cloud_status()})

    # media
    reg("media.folders.list", lambda _: {"folders": rt.library.folders()})
    reg("media.folders.add", lambda p: {"folders": rt.library.add_folder(p.path)}, FolderParams)
    reg("media.folders.remove", lambda p: {"folders": rt.library.remove_folder(p.path)}, FolderParams)
    reg("media.scan", lambda _: rt.library.start_scan())
    reg("media.scan_cancel", lambda _: rt.library.cancel_scan())
    reg("media.list", lambda p: rt.library.list_videos(p.offset, p.limit, p.query), MediaListParams)
    reg("media.summary", lambda _: rt.library.summary())

    # obs
    def obs_connect(p: ObsConnectParams) -> dict:
        patch = {k: v for k, v in {"host": p.host, "port": p.port}.items() if v is not None}
        if patch:
            rt.config.update({"obs": patch})
        if p.password is not None:
            rt.obs.set_password(p.password or None)
        rt.reload_obs_settings()
        return rt.obs.connect()

    reg("obs.status", lambda _: rt.obs.inspect())
    reg("obs.connect", obs_connect, ObsConnectParams)
    reg("obs.disconnect", lambda _: rt.obs.disconnect())

    # playback (local operator control; Cloud uses the same engine)
    reg("playback.status", lambda _: rt.playback.status())
    reg("playback.play", lambda p: rt.playback.play(p.media_id), PlayParams)
    reg("playback.playlist", lambda p: rt.playback.play_playlist(p.media_ids, p.loop), PlaylistParams)
    reg("playback.pause", lambda _: rt.playback.pause())
    reg("playback.resume", lambda _: rt.playback.resume())
    reg("playback.stop", lambda _: rt.playback.stop())
    reg("playback.next", lambda _: rt.playback.next())
    reg("playback.previous", lambda _: rt.playback.previous())
    reg("playback.loop", lambda p: rt.playback.set_loop(p.loop), LoopParams)

    # CAPTCHA monitor
    reg("monitor.status", lambda _: rt.monitor.status())
    reg("monitor.start", lambda _: rt.monitor.start())
    reg("monitor.stop", lambda _: rt.monitor.stop())
    reg("monitor.config", lambda p: rt.monitor.configure(p.model_dump(exclude_none=True)), MonitorConfigParams)
    # Templates come from Sarae through the Cloud (cloud/worker.py); the GUI can only count them.
    reg("monitor.templates.list", lambda _: {"templates": rt.monitor.templates()})

    # voice
    reg("voice.speak", lambda p: rt.voice.speak(p.text, p.voice), SpeakParams)

    # Auto Pin: controlled Chrome profile, local-only IPC (no Cloud command support).
    reg("autopin.status", lambda _: rt.auto_pin.status())
    # Step 1: plain Chrome for a manual TikTok sign-in; step 2 (open/start) lets Playwright use that profile.
    reg("autopin.login", lambda p: rt.auto_pin.login_browser(p.url), AutoPinLoginParams)
    reg("autopin.login_done", lambda _: rt.auto_pin.finish_login())
    reg("autopin.open", lambda p: rt.auto_pin.open_browser(p.url), AutoPinOpenParams)
    reg("autopin.scan", lambda _: rt.auto_pin.scan_products())
    reg("autopin.configure", lambda p: rt.auto_pin.configure(p.products, p.interval_seconds), AutoPinConfigureParams)
    reg("autopin.start", lambda _: rt.auto_pin.start())
    reg("autopin.stop", lambda _: rt.auto_pin.stop())

    # diagnostics
    reg("diagnostics.export", lambda _: rt.diagnostics())
    reg("logs.recent", lambda p: {"entries": list(event_handler.recent)[-p.limit:]}, LogsParams)
