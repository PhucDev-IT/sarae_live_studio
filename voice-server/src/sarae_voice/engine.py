"""G-OmniVoice engine: the exact generation path verified in Phase 0, nothing added.

Every method is synchronous and must be called through GpuGate (one GPU thread).
* load: OmniVoice.from_pretrained(<pinned snapshot>, device_map=device, dtype=float16) — as omnivoice-demo.
* prompts: model.create_voice_clone_prompt(ref_audio=<path>, ref_text=None | transcript) with the library's
  default preprocessing. ref_text=None runs Whisper exactly like the demo with an empty Reference Text box.
* synthesis: model.generate(text, language=None, generation_config=<preset>, voice_clone_prompt=prompt)[0],
  24 kHz float audio, no extra processing. Preset "demo_default" is what demo.py _gen_core builds from its UI defaults.
FakeEngine has the same interface without a model, for tests and for wiring other components.
"""
from __future__ import annotations

import gc
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from sarae_voice.config import Settings
from sarae_voice.errors import ServiceError

log = logging.getLogger("sarae.voice.engine")

PRESETS: dict[str, dict] = {
    # omnivoice/cli/demo.py _gen_core with the UI defaults (32 steps, CFG 2.0, denoise, preprocess, postprocess);
    # every other OmniVoiceGenerationConfig field stays at the library default. Never change this preset:
    # add a new name instead, so "demo_default" keeps meaning "same as the official demo".
    "demo_default": {"num_step": 32, "guidance_scale": 2.0, "denoise": True, "preprocess_prompt": True,
                     "postprocess_output": True},
}
PROMPT_FORMAT = 1   # VoiceClonePrompt.save format_version in omnivoice 0.2.1


@dataclass
class PromptResult:
    prompt: Any
    ref_text: str              # transcript the prompt actually contains (after the library's punctuation step)
    asr_used: bool
    tokens: int


class Engine(Protocol):
    state: str                 # loading | ready | error
    error: dict | None
    sampling_rate: int

    def load(self) -> None: ...
    def info(self) -> dict: ...
    def compat_basis(self) -> dict: ...
    def create_prompt(self, wav_path: Path, ref_text: str | None) -> PromptResult: ...
    def save_prompt(self, prompt: Any, path: Path) -> None: ...
    def load_prompt(self, path: Path) -> Any: ...
    def synthesize(self, prompt: Any, text: str, preset: str, seed: int | None) -> np.ndarray: ...
    def memory(self) -> dict: ...


def _version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


class OmniVoiceEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.state = "loading"
        self.error: dict | None = None
        self.sampling_rate = 24000
        self.model = None
        self.snapshot: str | None = None
        self.timings: dict[str, float] = {}
        self.versions = {name: _version(name) for name in ("omnivoice", "torch", "torchaudio", "transformers",
                                                           "huggingface-hub")}

    # --- lifecycle -----------------------------------------------------------------------------
    def load(self) -> None:
        s = self.settings
        try:
            if s.strict_versions and self.versions["omnivoice"] != s.omnivoice_version:
                raise ServiceError("TTS_VERSION_MISMATCH", f"omnivoice {self.versions['omnivoice']} installed, "
                                   f"{s.omnivoice_version} required by the verified baseline", 503)
            started = time.perf_counter()
            import torch
            from huggingface_hub import snapshot_download
            from omnivoice import OmniVoice
            self.timings["import_s"] = round(time.perf_counter() - started, 3)
            if s.device.startswith("cuda") and not torch.cuda.is_available():
                raise ServiceError("TTS_GPU_UNAVAILABLE", "CUDA is not available to PyTorch", 503)
            started = time.perf_counter()
            try:   # pinned revision from the local cache; download only when it is not there yet
                self.snapshot = snapshot_download(s.checkpoint, revision=s.checkpoint_revision, local_files_only=True)
            except Exception:  # noqa: BLE001 - not cached: fetch that exact revision once
                self.snapshot = snapshot_download(s.checkpoint, revision=s.checkpoint_revision)
            self.timings["resolve_s"] = round(time.perf_counter() - started, 3)
            started = time.perf_counter()
            self.model = OmniVoice.from_pretrained(self.snapshot, device_map=s.device, dtype=torch.float16)
            self.model._asr_model_name = s.asr_model
            if s.device.startswith("cuda"):
                torch.cuda.synchronize()
            self.timings["load_s"] = round(time.perf_counter() - started, 3)
            self.sampling_rate = int(self.model.sampling_rate)
            self.state, self.error = "ready", None
            log.info("G-OmniVoice ready: %s@%s on %s (%s)", s.checkpoint, s.checkpoint_revision[:8], s.device,
                     self.timings)
        except ServiceError as exc:
            self.state, self.error = "error", {"code": exc.code, "message": exc.message}
            log.error("Engine not ready: %s", exc.message)
        except Exception as exc:  # noqa: BLE001 - reported by /health/ready, never crashes the process
            self.state, self.error = "error", {"code": "TTS_LOAD_FAILED", "message": f"{type(exc).__name__}: {exc}"[:300]}
            log.exception("Engine failed to load")

    def info(self) -> dict:
        return {"engine": "omnivoice", "checkpoint": self.settings.checkpoint,
                "checkpoint_revision": self.settings.checkpoint_revision, "snapshot": self.snapshot,
                "device": self.settings.device, "dtype": "float16", "sample_rate": self.sampling_rate,
                "versions": self.versions, "asr_model": self.settings.asr_model, "presets": PRESETS,
                "load_timings": self.timings, "gpu": self._gpu_name()}

    def compat_basis(self) -> dict:
        return {"checkpoint": self.settings.checkpoint, "checkpoint_revision": self.settings.checkpoint_revision,
                "omnivoice": self.versions["omnivoice"], "prompt_format": PROMPT_FORMAT, "preprocess_prompt": True,
                "asr_model": self.settings.asr_model}

    # --- prompts -------------------------------------------------------------------------------
    def create_prompt(self, wav_path: Path, ref_text: str | None) -> PromptResult:
        model = self._ready()
        try:
            if ref_text is None and model._asr_pipe is None:
                model.load_asr_model()
            prompt = model.create_voice_clone_prompt(ref_audio=str(wav_path), ref_text=ref_text)
        except ServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._gpu_error(exc, "VOICE_PROMPT_FAILED") from exc
        finally:
            if ref_text is None and not self.settings.keep_asr_loaded:
                self._release_asr()
        if not (prompt.ref_text or "").strip(" ."):
            raise ServiceError("VOICE_ASR_FAILED", "Whisper returned no transcript; send one with the voice", 422)
        return PromptResult(prompt, prompt.ref_text, ref_text is None, int(prompt.ref_audio_tokens.shape[-1]))

    def save_prompt(self, prompt: Any, path: Path) -> None:
        prompt.save(str(path))

    def load_prompt(self, path: Path) -> Any:
        from omnivoice import VoiceClonePrompt
        return VoiceClonePrompt.load(str(path))

    # --- synthesis -----------------------------------------------------------------------------
    def synthesize(self, prompt: Any, text: str, preset: str, seed: int | None) -> np.ndarray:
        model = self._ready()
        from omnivoice import OmniVoiceGenerationConfig
        try:
            if seed is not None:     # the demo never seeds; a seed only makes a run reproducible (tests, bench)
                from omnivoice.utils.common import fix_random_seed
                fix_random_seed(seed)
            audio = model.generate(text=text, language=None, generation_config=OmniVoiceGenerationConfig(**PRESETS[preset]),
                                   voice_clone_prompt=prompt)[0]
        except Exception as exc:  # noqa: BLE001
            raise self._gpu_error(exc, "TTS_INFERENCE_FAILED") from exc
        return np.asarray(audio, dtype=np.float32)

    def memory(self) -> dict:
        try:
            import torch
            if not torch.cuda.is_available():
                return {}
            free, total = torch.cuda.mem_get_info()
            return {"allocated_gib": round(torch.cuda.memory_allocated() / 2**30, 3),
                    "reserved_gib": round(torch.cuda.memory_reserved() / 2**30, 3),
                    "peak_allocated_gib": round(torch.cuda.max_memory_allocated() / 2**30, 3),
                    "peak_reserved_gib": round(torch.cuda.max_memory_reserved() / 2**30, 3),
                    "device_free_gib": round(free / 2**30, 3), "device_total_gib": round(total / 2**30, 3)}
        except Exception:  # noqa: BLE001 - status must not fail because of a metric
            return {}

    # --- helpers -------------------------------------------------------------------------------
    def _ready(self):
        if self.state != "ready" or self.model is None:
            raise ServiceError("TTS_NOT_READY", f"Engine is {self.state}", 503, retry_after=5)
        return self.model

    def _release_asr(self) -> None:
        if self.model is not None and self.model._asr_pipe is not None:
            self.model._asr_pipe = None
            gc.collect()
            self._empty_cache()

    @staticmethod
    def _empty_cache() -> None:
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass

    def _gpu_error(self, exc: Exception, code: str) -> ServiceError:
        import torch
        if isinstance(exc, torch.cuda.OutOfMemoryError):
            self._empty_cache()
            log.warning("CUDA out of memory (%s)", code)
            return ServiceError("TTS_OOM", "GPU out of memory", 507, retry_after=5)
        if "CUDA error" in str(exc) or "CUBLAS" in str(exc):
            # The CUDA context may be unusable: stop accepting work until a restart.
            self.state, self.error = "error", {"code": "TTS_GPU_FAILED", "message": str(exc)[:300]}
            log.error("CUDA failure, engine marked as error: %s", exc)
            return ServiceError("TTS_GPU_FAILED", "GPU failure; restart the voice server", 503)
        log.exception("%s", code)
        return ServiceError(code, f"{type(exc).__name__}", 500)

    def _gpu_name(self) -> str | None:
        try:
            import torch
            return torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
        except Exception:  # noqa: BLE001
            return None


# --- fake ----------------------------------------------------------------------------------------
@dataclass
class FakePrompt:
    wav_sha256: str
    ref_text: str


class FakeEngine:
    """Same contract as OmniVoiceEngine, no model: a tone whose length follows the text. Knobs for tests."""

    def __init__(self, settings: Settings | None = None, *, delay_s: float = 0.0, load_error: str | None = None,
                 revision: str = "fake-rev", asr_text: str = "Đây là giọng nói mẫu của shop."):
        self.state = "loading"
        self.error: dict | None = None
        self.sampling_rate = 24000
        self.delay_s = delay_s
        self.load_error = load_error
        self.revision = revision
        self.asr_text = asr_text
        self.fail_next: str | None = None        # "oom" | "inference" | "cuda" for the next synthesize
        self.calls: dict[str, int] = {"load": 0, "create_prompt": 0, "asr": 0, "synthesize": 0, "load_prompt": 0}
        self.prompt_texts: list[str | None] = []
        self._active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def load(self) -> None:
        self.calls["load"] += 1
        time.sleep(self.delay_s)
        if self.load_error:
            self.state, self.error = "error", {"code": self.load_error, "message": "fake load failure"}
        else:
            self.state, self.error = "ready", None

    def info(self) -> dict:
        return {"engine": "fake", "checkpoint": "fake/checkpoint", "checkpoint_revision": self.revision,
                "sample_rate": self.sampling_rate, "presets": PRESETS, "versions": {"omnivoice": "fake"}}

    def compat_basis(self) -> dict:
        return {"checkpoint": "fake/checkpoint", "checkpoint_revision": self.revision, "omnivoice": "fake",
                "prompt_format": PROMPT_FORMAT, "preprocess_prompt": True, "asr_model": "fake-asr"}

    def create_prompt(self, wav_path: Path, ref_text: str | None) -> PromptResult:
        self._check()
        self.calls["create_prompt"] += 1
        self.prompt_texts.append(ref_text)
        if ref_text is None:
            self.calls["asr"] += 1
            ref_text = self.asr_text
        if not ref_text.strip(" ."):
            raise ServiceError("VOICE_ASR_FAILED", "Whisper returned no transcript; send one with the voice", 422)
        text = ref_text if ref_text.rstrip()[-1:] in ".!?" else ref_text + "."
        return PromptResult(FakePrompt(hashlib.sha256(Path(wav_path).read_bytes()).hexdigest(), text),
                            text, self.prompt_texts[-1] is None, 100)

    def save_prompt(self, prompt: FakePrompt, path: Path) -> None:
        path.write_text(json.dumps(prompt.__dict__, ensure_ascii=False), encoding="utf-8")

    def load_prompt(self, path: Path) -> FakePrompt:
        self.calls["load_prompt"] += 1
        return FakePrompt(**json.loads(path.read_text(encoding="utf-8")))

    def synthesize(self, prompt: FakePrompt, text: str, preset: str, seed: int | None) -> np.ndarray:
        self._check()
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            self.calls["synthesize"] += 1
            time.sleep(self.delay_s)
            fail, self.fail_next = self.fail_next, None
            if fail == "oom":
                raise ServiceError("TTS_OOM", "GPU out of memory", 507, retry_after=5)
            if fail == "inference":
                raise ServiceError("TTS_INFERENCE_FAILED", "RuntimeError", 500)
            if fail == "cuda":
                self.state, self.error = "error", {"code": "TTS_GPU_FAILED", "message": "fake"}
                raise ServiceError("TTS_GPU_FAILED", "GPU failure; restart the voice server", 503)
            n = int(self.sampling_rate * (0.2 + 0.06 * len(text)))
            t = np.arange(n) / self.sampling_rate
            return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        finally:
            with self._lock:
                self._active -= 1

    def memory(self) -> dict:
        return {}

    def _check(self) -> None:
        if self.state != "ready":
            raise ServiceError("TTS_NOT_READY", f"Engine is {self.state}", 503, retry_after=5)


def build_engine(settings: Settings) -> Engine:
    return FakeEngine(settings) if settings.engine == "fake" else OmniVoiceEngine(settings)
