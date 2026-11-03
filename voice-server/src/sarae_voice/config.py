"""Service settings, all from SARAE_VOICE_* environment variables (or voice-server/.env)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SARAE_VOICE_", env_file=ROOT / ".env", extra="ignore")

    host: str = "127.0.0.1"                      # loopback by default; a LAN/VPN address must be chosen explicitly
    port: int = 8780
    data_dir: Path = ROOT / "data"               # voices, prompt cache, optional debug outputs (never in Git)
    engine: Literal["omnivoice", "fake"] = "omnivoice"
    device: str = "cuda:0"

    # Pinned to the Phase 0 baseline (docs/TTS_SERVER.md section 14).
    checkpoint: str = "g-group-ai-lab/g-omnivoice"
    checkpoint_revision: str = "f2b73211e57db389d81ab817987f90c02e0898ba"
    omnivoice_version: str = "0.2.1"
    strict_versions: bool = True                 # refuse to become ready with another omnivoice version
    asr_model: str = "openai/whisper-large-v3-turbo"   # what the official demo uses
    keep_asr_loaded: bool = False                # Whisper only runs when a voice is registered

    # Comma separated, so a token can be rotated with two valid at once.
    orchestrator_tokens: str = ""
    admin_tokens: str = ""

    max_text_chars: int = Field(default=450, ge=1, le=5000)   # the Agent's MAX_CHARS
    max_wav_bytes: int = 5 * 1024 * 1024
    min_ref_seconds: float = 1.0
    max_ref_seconds: float = 20.0                # longer clips would take a different preprocessing path
    prompt_ram_cache: int = Field(default=16, ge=1)
    max_pending: int = Field(default=4, ge=1)    # GPU calls waiting or running; more -> 429
    queue_timeout_s: float = 30.0                # not started within this -> 503, never run late
    save_outputs: bool = False                   # keep every WAV + metadata under data/outputs (debug)

    @staticmethod
    def _split(raw: str) -> set[str]:
        return {t.strip() for t in raw.split(",") if t.strip()}

    @property
    def orchestrator_token_set(self) -> set[str]:
        return self._split(self.orchestrator_tokens)

    @property
    def admin_token_set(self) -> set[str]:
        return self._split(self.admin_tokens)
