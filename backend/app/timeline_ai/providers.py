"""Replaceable providers of the AI Timeline Analyzer, chosen by the owner's settings (`timeline_ai` in owner settings).

- TranscriptProvider: audio file → segments with word timestamps. `faster_whisper` (local, optional dependency).
- Aligner: refines word timestamps. `words` keeps Whisper's own word timestamps; `whisperx` (optional dependency).
- LLMProvider: prompt → JSON text. `gemini` (the owner's or the platform's API key, app/ai_service.py).
- VisionProvider: one frame + prompt → JSON text. `gemini` multimodal, used only when enabled.

No API key or model name is written here: keys come from app/ai_service.api_key, models from the settings (Whisper's
default from the Backend environment, the LLM's from the owner's AI settings). Heavy libraries are imported only when
their provider is used, so the Backend runs without them (requirements-timeline-ai.txt).
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Protocol

import httpx

log = logging.getLogger("sarae.timeline_ai")


class ProviderUnavailable(RuntimeError):
    """A configured provider cannot run here (library not installed, no API key)."""


class TranscriptProvider(Protocol):
    name: str
    version: str

    def transcribe(self, audio_path: str, language: str) -> list[dict]:
        """[{start_ms, end_ms, text, words: [{start_ms, end_ms, word, probability}]}]"""


class Aligner(Protocol):
    name: str
    version: str

    def align(self, audio_path: str, segments: list[dict], language: str) -> list[dict]: ...


class LLMProvider(Protocol):
    name: str
    version: str

    def complete_json(self, prompt: str, max_output_tokens: int) -> str: ...


class VisionProvider(Protocol):
    name: str
    version: str

    def describe_json(self, image_jpeg: bytes, prompt: str) -> str: ...


# ----- transcript ---------------------------------------------------------------------------------------------------

class FasterWhisperTranscriber:
    name = "faster_whisper"

    def __init__(self, model: str, device: str = "auto", compute_type: str = "int8"):
        self.model, self.device, self.compute_type = model, device, compute_type
        self.version = f"faster_whisper:{model}:{compute_type}"

    def transcribe(self, audio_path: str, language: str) -> list[dict]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ProviderUnavailable("Máy chủ chưa cài faster-whisper (pip install -r requirements-timeline-ai.txt)") from exc
        model = WhisperModel(self.model, device=self.device, compute_type=self.compute_type)
        # Samples, not a path: given a path, faster-whisper decodes with PyAV, whose recent releases dropped an argument
        # it passes (`metadata_errors`). Our audio is already 16 kHz mono PCM from FFmpeg.
        segments, _info = model.transcribe(read_wav(audio_path), language=language, word_timestamps=True, vad_filter=True)
        out = []
        for seg in segments:
            words = [{"start_ms": int(w.start * 1000), "end_ms": int(w.end * 1000), "word": w.word.strip(),
                      "probability": round(float(w.probability), 3)} for w in (seg.words or [])]
            out.append({"start_ms": int(seg.start * 1000), "end_ms": int(seg.end * 1000), "text": seg.text.strip(),
                        "words": words})
        return out


def read_wav(path: str):
    """16 kHz mono 16-bit PCM WAV (pipeline.extract_audio) → float32 samples in [-1, 1], as Whisper models take them."""
    import wave

    import numpy as np  # a dependency of faster-whisper
    with wave.open(path, "rb") as wav:
        if wav.getframerate() != 16000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ProviderUnavailable("Âm thanh phải là WAV 16 kHz mono 16-bit")
        frames = wav.readframes(wav.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


class WordAligner:
    """Keeps Whisper's own word timestamps (word_timestamps=True), which are aligned on the audio already."""
    name = "words"
    version = "words:1"

    def align(self, audio_path: str, segments: list[dict], language: str) -> list[dict]:
        return segments


class WhisperXAligner:
    name = "whisperx"

    def __init__(self, model: str | None = None, device: str = "cpu"):
        self.model, self.device = model, device
        self.version = f"whisperx:{model or 'default'}"

    def align(self, audio_path: str, segments: list[dict], language: str) -> list[dict]:
        try:
            import whisperx
        except ImportError as exc:
            raise ProviderUnavailable("Máy chủ chưa cài whisperx; dùng alignment 'words'") from exc
        align_model, metadata = whisperx.load_align_model(language_code=language, device=self.device, model_name=self.model)
        audio = whisperx.load_audio(audio_path)
        source = [{"start": s["start_ms"] / 1000, "end": s["end_ms"] / 1000, "text": s["text"]} for s in segments]
        result = whisperx.align(source, align_model, metadata, audio, self.device, return_char_alignments=False)
        out = []
        for seg in result.get("segments", []):
            words = [{"start_ms": int(w["start"] * 1000), "end_ms": int(w["end"] * 1000), "word": w["word"],
                      "probability": round(float(w.get("score", 0)), 3)} for w in seg.get("words", []) if "start" in w]
            out.append({"start_ms": int(seg["start"] * 1000), "end_ms": int(seg["end"] * 1000),
                        "text": seg["text"].strip(), "words": words})
        return out or segments


# ----- LLM / vision -------------------------------------------------------------------------------------------------

class GeminiLLM:
    name = "gemini"

    def __init__(self, model: str, api_key: str, timeout: float = 120):
        self.model, self._key, self.timeout = model, api_key, timeout
        self.version = f"gemini:{model}"

    def _generate(self, parts: list[dict], max_output_tokens: int) -> str:
        from app.ai_service import redact
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {"contents": [{"parts": parts}],
                "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": max_output_tokens,
                                     "temperature": 0}}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, headers={"x-goog-api-key": self._key}, json=body)
                response.raise_for_status()
                payload = response.json()
            return payload["candidates"][0]["content"]["parts"][0]["text"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(redact(f"Gemini lỗi: {exc}", self._key)[:300]) from None

    def complete_json(self, prompt: str, max_output_tokens: int) -> str:
        return self._generate([{"text": prompt}], max_output_tokens)


class GeminiVision(GeminiLLM):
    def describe_json(self, image_jpeg: bytes, prompt: str) -> str:
        image = {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(image_jpeg).decode()}}
        return self._generate([image, {"text": prompt}], 400)


# ----- factory ------------------------------------------------------------------------------------------------------

def build(owner_id: str, config) -> dict:
    """Providers for one analysis, from the validated settings (api.TimelineAISettings)."""
    from app.ai_service import api_key, load_config
    t = config.transcript
    transcriber = FasterWhisperTranscriber(t.model, t.device, t.compute_type)
    aligner = WhisperXAligner(config.alignment.model) if config.alignment.provider == "whisperx" else WordAligner()
    llm_model = config.llm.model or load_config(owner_id).model
    key, _source = api_key(owner_id, config.llm.provider)
    if not key:
        raise ProviderUnavailable("Chưa có API key Gemini (Cài đặt AI) để phân tích transcript")
    llm = GeminiLLM(llm_model, key)
    vision = None
    if config.vision.enabled:
        vision = GeminiVision(config.vision.model or llm_model, key)
    return {"transcriber": transcriber, "aligner": aligner, "llm": llm, "vision": vision}


def loads_json(text: str):
    """The JSON an LLM returned, tolerating a ```json fence around it."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1:] if "\n" in text else text
    return json.loads(text)
