"""Existing local cloned voices, shared with sarae-ai-live-studio; no cloud account required."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from sarae_agent.errors import AgentError

VOICE_ID = re.compile(r"[A-Za-z0-9_-]{3,64}")
EDGE_VOICES = [{"id": "vi-VN-HoaiMyNeural", "name": "Hoài My (nữ)", "provider": "edge", "ready": True},
               {"id": "vi-VN-NamMinhNeural", "name": "Nam Minh (nam)", "provider": "edge", "ready": True}]


class LocalVoiceServer:
    """Dev only (SARAE_STUDIO_VOICE_MODE=local): the voice server on this machine, 127.0.0.1:8780."""
    provider = "local_voice"
    prefix = "local:"
    mode = "local"

    def __init__(self, root: Path | None = None, *, transport=None):
        self.root = root or Path(os.environ.get("SARAE_VOICE_SERVER_DIR") or
                                 Path(__file__).resolve().parents[4].parent / "sarae-voice-server")
        self.transport = transport
        self._lock = threading.Lock()
        self._refreshing = threading.Lock()
        self._online = False
        self._reachable = False
        self._error = "Chưa kiểm tra voice server."
        self._profiles = self._disk_profiles()
        self._process = None
        self._starting = False

    def _environment(self) -> dict:
        values = {}
        try:
            for line in (self.root / ".env").read_text(encoding="utf-8-sig").splitlines():
                key, separator, value = line.partition("=")
                if separator and not key.strip().startswith("#"):
                    values[key.strip()] = value.strip().strip("\"'")
        except OSError:
            pass
        return {**values, **os.environ}

    def _connection(self) -> tuple[str, str]:
        env = self._environment()
        base = env.get("SARAE_STUDIO_VOICE_URL") or f"http://127.0.0.1:{env.get('SARAE_VOICE_PORT', '8780')}"
        try:
            parts = urlsplit(base)
            valid = parts.scheme in {"http", "https"} and parts.hostname in {"localhost", "127.0.0.1", "::1"} and not parts.username and not parts.password and not parts.query and not parts.fragment
            _ = parts.port
        except ValueError:
            valid = False
        if not valid:
            raise AgentError("TTS_CONFIG", "Voice server phải chạy trên máy này (localhost).")
        token = env.get("SARAE_VOICE_ORCHESTRATOR_TOKENS", "").split(",")[0].strip()
        if not token:
            raise AgentError("TTS_CONFIG", "Chưa có token voice server. Kiểm tra .env của sarae-voice-server.")
        return base.rstrip("/"), token

    def _disk_profiles(self) -> list[dict]:
        env = self._environment()
        folder = Path(env.get("SARAE_VOICE_DATA_DIR") or self.root / "data")
        if not folder.is_absolute():
            folder = self.root / folder
        profiles = []
        for path in list((folder / "voices").glob("*/profile.json"))[:100]:
            try:
                profile = json.loads(path.read_text(encoding="utf-8"))
                if VOICE_ID.fullmatch(str(profile.get("voice_id", ""))):
                    profiles.append(profile)
            except (OSError, ValueError):
                continue
        return profiles

    def catalog(self) -> dict:
        with self._lock:
            options = [*EDGE_VOICES, *[{"id": "local:" + p["voice_id"], "name": p.get("name") or p["voice_id"],
                        "provider": "local_voice", "ready": self._online and p.get("prompt") is not None,
                        "revision": p.get("rev", "")} for p in self._profiles]]
            return {"voice_options": options, "voice_server": {"online": self._online, "starting": self._starting, "error": self._error,
                                                       "mode": self.mode}}

    def start(self) -> None:
        """Explicit operator action; the shared GPU server keeps running when Studio closes."""
        python = self.root / ".venv" / "Scripts" / "python.exe"
        if not python.is_file() or not (self.root / ".env").is_file():
            raise AgentError("TTS_UNAVAILABLE", "Chưa cài sarae-voice-server hoặc thiếu .env. Hãy thiết lập voice server trước.")
        self._connection()
        self.refresh()
        with self._lock:
            if self._online or self._starting:
                return
            self._starting = True
            self._error = "Voice server đang nạp giọng và GPU…"
        try:
            if not self._reachable:
                self._process = subprocess.Popen([str(python), "-m", "sarae_voice"], cwd=self.root,
                                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            with self._lock:
                self._starting = False
            raise AgentError("TTS_UNAVAILABLE", "Không mở được voice server.") from exc
        def wait_ready():
            try:
                for _ in range(45):
                    self.refresh()
                    if self.catalog()["voice_server"]["online"]:
                        break
                    if self._process is not None and self._process.poll() is not None:
                        self.refresh()
                        break
                    threading.Event().wait(2)
            finally:
                with self._lock:
                    self._starting = False
        threading.Thread(target=wait_ready, name="studio-voice-start", daemon=True).start()

    def refresh(self) -> dict:
        if not self._refreshing.acquire(blocking=False):
            return self.catalog()
        try:
            base, token = self._connection()
            with httpx.Client(transport=self.transport, timeout=3, follow_redirects=False) as client:
                ready = client.get(base + "/health/ready")
                response = client.get(base + "/v1/voices", headers={"Authorization": "Bearer " + token})
                if response.status_code != 200:
                    raise AgentError("TTS_UNAVAILABLE", f"Không đọc được giọng local (HTTP {response.status_code}).")
                profiles = [p for p in response.json().get("voices", []) if isinstance(p, dict) and VOICE_ID.fullmatch(str(p.get("voice_id", "")))]
            with self._lock:
                self._profiles = profiles
                self._reachable = True
                self._online = ready.status_code == 200
                self._error = "" if self._online else "Voice server đang khởi động hoặc chưa sẵn sàng."
        except (AgentError, httpx.HTTPError, ValueError) as exc:
            profiles = self._disk_profiles()
            with self._lock:
                self._online = False
                self._reachable = False
                self._error = exc.message if isinstance(exc, AgentError) else "Không kết nối được voice server. Hãy mở sarae-voice-server rồi làm mới giọng."
                if profiles:
                    self._profiles = profiles
        finally:
            self._refreshing.release()
        return self.catalog()

    def revision(self, voice_id: str) -> str:
        with self._lock:
            return next((p.get("rev", "") for p in self._profiles if p["voice_id"] == voice_id), "")

    def normalize(self, model, voice, rate, settings):
        if not VOICE_ID.fullmatch(voice) or model not in (None, "live_fast") or not -30 <= rate <= 50:
            raise AgentError("INVALID_PARAMS", "Giọng local hoặc tốc độ đọc không hợp lệ.")
        return "live_fast", {"rate": rate, "revision": (settings or {}).get("revision", self.revision(voice))}

    requires_key = False

    def synthesize(self, text, voice, model, settings, output, api_key, first_audio):
        executable = shutil.which("ffmpeg")
        if not executable:
            raise AgentError("TTS_UNAVAILABLE", "Cần FFmpeg để phát giọng local qua OBS.")
        base, token = self._connection()
        try:
            with httpx.Client(transport=self.transport, timeout=90, follow_redirects=False) as client:
                with client.stream("POST", base + "/v1/tts/synthesize", headers={"Authorization": "Bearer " + token},
                                   json={"voice_id": voice, "text": text, "preset": model, "seed": 42}) as response:
                    if response.status_code != 200:
                        raise AgentError("TTS_UNAVAILABLE", f"Voice server chưa tạo được giọng (HTTP {response.status_code}).")
                    audio = bytearray()
                    for chunk in response.iter_bytes():
                        audio.extend(chunk)
                        if len(audio) > 10 * 1024 * 1024:
                            raise AgentError("TTS_INVALID_AUDIO", "Âm thanh voice server quá lớn.")
            if not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
                raise AgentError("TTS_INVALID_AUDIO", "Voice server chưa trả âm thanh WAV hợp lệ.")
            first_audio()
            result = subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "wav", "-i", "pipe:0",
                                     "-vn", "-af", f"atempo={1 + settings.get('rate', 0) / 100:g}", "-codec:a", "libmp3lame", str(output)],
                                    input=bytes(audio), capture_output=True, timeout=20,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode or not output.is_file() or not output.stat().st_size:
                raise AgentError("TTS_INVALID_AUDIO", "Không chuyển được âm thanh giọng local để phát OBS.")
        except httpx.HTTPError as exc:
            raise AgentError("TTS_UNAVAILABLE", "Không kết nối được voice server local.") from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentError("TTS_FAILED", "Xử lý âm thanh giọng local thất bại.") from exc
