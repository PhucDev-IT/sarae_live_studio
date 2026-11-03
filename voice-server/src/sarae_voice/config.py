"""Service settings, all from SARAE_VOICE_* environment variables (or voice-server/.env)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
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
    queue_timeout_s: float = 30.0                # not started within this -> 503, never run late
    save_outputs: bool = False                   # keep every WAV + metadata under data/outputs (debug)

    # --- Phase 2: jobs, scheduling, admission, results, operation ------------------------------------------
    job_max_chars: int = Field(default=5000, ge=1, le=20000)   # long-form jobs; live/preview/prepare keep max_text_chars
    queue_live: int = 8                          # bounded queue per priority class
    queue_preview: int = 4
    queue_prepare: int = 50
    queue_long: int = 5
    queue_benchmark: int = 2
    starvation_s: float = 30.0                   # a lower-class call waiting this long gets a fair turn ...
    fair_every: int = 4                          # ... after every 4 higher-class starts
    max_deadline_s: float = 3600.0
    job_retention_s: float = 900.0               # finished jobs (and idempotency keys) kept this long
    result_ttl_s: float = 86400.0                # result cache files
    result_max_bytes: int = 1024 * 1024 * 1024
    max_output_bytes: int = 32 * 1024 * 1024     # one WAV (24 kHz PCM16 ≈ 11 min)
    read_token_max_ttl_s: float = 900.0
    read_token_secret: str = ""                  # empty: random per process (tokens die with the jobs on restart)
    live_lease_max_ttl_s: float = 300.0
    prepare_live_max_audio_s: float = 20.0       # prepare jobs accepted during a LIVE lease up to this estimate
    chars_per_audio_s: float = 19.0              # Phase 0: 954 chars -> 50 s; only for estimates, never promises
    cleanup_interval_s: float = 300.0
    shutdown_grace_s: float = 30.0               # wait for the running GPU call on shutdown
    exit_on_gpu_failure: bool = True             # CUDA failure -> exit so the supervisor restarts a clean process

    def queue_limits(self) -> dict[str, int]:
        return {"live": self.queue_live, "preview": self.queue_preview, "prepare": self.queue_prepare,
                "long": self.queue_long, "benchmark": self.queue_benchmark, "system": 1000}

    @field_validator("data_dir")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value if value.is_absolute() else ROOT / value     # relative to voice-server/, whatever the cwd

    @staticmethod
    def _split(raw: str) -> set[str]:
        return {t.strip() for t in raw.split(",") if t.strip()}

    @property
    def orchestrator_token_set(self) -> set[str]:
        return self._split(self.orchestrator_tokens)

    @property
    def admin_token_set(self) -> set[str]:
        return self._split(self.admin_tokens)
