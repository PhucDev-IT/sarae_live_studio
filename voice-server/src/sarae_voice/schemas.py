"""Request/response models (OpenAPI)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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


class SynthesizeRequest(BaseModel):
    voice_id: str = Field(pattern=r"^[A-Za-z0-9_-]{3,64}$")
    text: str = Field(min_length=1, max_length=5000)
    preset: Preset = "demo_default"   # type: ignore[valid-type]
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1,
                             description="Optional; the official demo never seeds. Only for reproducible tests")
