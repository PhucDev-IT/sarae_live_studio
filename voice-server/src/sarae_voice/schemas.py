"""Request/response models (OpenAPI)."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from sarae_voice.engine import PRESETS

Preset = Literal[tuple(PRESETS)]  # type: ignore[valid-type]


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class Live(BaseModel):
    status: Literal["alive"] = "alive"


class Ready(BaseModel):
    state: Literal["loading", "ready", "error"]
    error: ErrorBody | None = None
    busy: bool
    pending: int


class WavInfo(BaseModel):
    sha256: str
    bytes: int
    sample_rate: int
    channels: int
    subtype: str
    duration_s: float


class Transcript(BaseModel):
    mode: Literal["asr", "manual"] = Field(description="asr = Whisper, like the official demo with an empty Reference Text")
    manual_text: str | None
    asr_text: str | None = Field(description="Whisper result, stored once and reused when the prompt is rebuilt")
    ref_text_used: str | None = Field(description="Exact transcript inside the current VoiceClonePrompt")


class PromptInfo(BaseModel):
    compat: str
    created_at: str
    prompt_ms: float
    tokens: int
    asr_used: bool


class VoiceProfile(BaseModel):
    voice_id: str
    name: str | None
    owner_ref: str | None
    created_at: str
    updated_at: str
    rev: str = Field(description="Changes when the WAV or the transcript changes")
    wav: WavInfo
    qc: dict = Field(description="Reference report (never changes the WAV); see qc.warnings")
    transcript: Transcript
    prompt: PromptInfo | None


class VoiceList(BaseModel):
    voices: list[VoiceProfile]


class TranscriptUpdate(BaseModel):
    transcript: str | None = Field(default=None, max_length=1000,
                                   description="Manual transcript; null or empty returns to the stored Whisper text")


class Warmup(BaseModel):
    voice_id: str
    rev: str
    cache: Literal["ram", "disk", "encoded"]
    ms: float


Priority = Literal["live", "preview", "prepare", "long", "benchmark"]


class JobCreate(BaseModel):
    voice_id: str = Field(pattern=r"^[A-Za-z0-9_-]{3,64}$")
    text: str = Field(min_length=1, max_length=20000)
    preset: Preset = "demo_default"   # type: ignore[valid-type]
    priority: Priority = "live"
    deadline_at: datetime | None = Field(default=None, description="UTC; never started after it. Default per priority")
    reader_agent_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$",
                                        description="Agent allowed to read the audio with the returned read token")
    owner_ref: str | None = Field(default=None, max_length=100, description="Must match the voice's owner_ref")
    tags: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9:_.-]{1,80}$")]] = Field(default_factory=list, max_length=8)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)

    @field_validator("deadline_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("deadline_at needs a timezone (UTC)")
        return value


class JobAudioInfo(BaseModel):
    bytes: int
    audio_ms: float
    sample_rate: int
    sha256: str


class JobView(BaseModel):
    job_id: str
    state: Literal["queued", "running", "done", "failed", "cancelled", "expired"]
    cancel_requested: bool
    voice_id: str
    voice_rev: str
    prompt_compat: str
    preset: str
    generation_config: dict
    priority: Priority
    chars: int
    seed: int | None
    deadline_at: str
    reader_agent_id: str | None
    owner_ref: str | None
    tags: list[str]
    created_at: str
    started_at: str | None
    finished_at: str | None
    source: Literal["generated", "result_cache"] | None
    error: ErrorBody | None
    timings: dict
    audio: JobAudioInfo | None
    reads: int


class JobCreated(JobView):
    created: Literal["new", "replay", "result_cache"] = Field(description="replay = same Idempotency-Key and request")
    read_token: str | None = Field(description="Only in this response; scoped to this job and reader_agent_id")
    read_token_expires_at: str | None


class JobCancelled(BaseModel):
    cancelled: int


class LeaseRequest(BaseModel):
    owner_ref: str = Field(min_length=1, max_length=100)
    session_id: str = Field(pattern=r"^[A-Za-z0-9:_.-]{1,80}$")
    ttl_s: float = Field(default=60, description="Renew before it ends (about every ttl/2)")


class SynthesizeRequest(BaseModel):
    voice_id: str = Field(pattern=r"^[A-Za-z0-9_-]{3,64}$")
    text: str = Field(min_length=1, max_length=5000)
    preset: Preset = "demo_default"   # type: ignore[valid-type]
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1,
                             description="Optional; the official demo never seeds. Only for reproducible tests")
