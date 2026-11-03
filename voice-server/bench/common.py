"""Shared helpers for the Phase 0 scripts: golden set, run folders, environment record, WAV output."""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sys
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

BENCH = Path(__file__).resolve().parent
RUNS = BENCH / "runs"
CHECKPOINT = "g-group-ai-lab/g-omnivoice"
DEMO_URL = "http://127.0.0.1:7862"


def golden(path: Path | None = None) -> list[dict]:
    return json.loads((path or BENCH / "golden_set.json").read_text(encoding="utf-8"))["items"]


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def seed_for(item_id: str, rep: int) -> int:
    """Same seed for the same sentence and repetition in every library arm (paired comparison)."""
    return zlib.crc32(f"{item_id}:{rep}".encode())


def new_run(name: str | None = None) -> Path:
    run = RUNS / (name or datetime.now().strftime("%Y%m%d_%H%M%S"))
    run.mkdir(parents=True, exist_ok=True)
    return run


def keep_reference(run: Path, label: str, ref: Path) -> dict:
    """Copy the reference byte for byte into the run, so every result can be traced to the exact file."""
    target = run / "refs" / f"{label}{ref.suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ref, target)
    return {"label": label, "source": str(ref), "copy": str(target.relative_to(run)), "sha256": sha256(ref)}


def write_wav_like_demo(path: Path, audio: np.ndarray, sr: int) -> None:
    """Exactly what omnivoice.cli.demo returns: (audio * 32767).astype(int16) at the model rate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, (np.asarray(audio) * 32767).astype(np.int16), sr, subtype="PCM_16")


def append_jsonl(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def package_versions(*names: str) -> dict:
    from importlib.metadata import PackageNotFoundError, version
    out = {}
    for name in names:
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = None
    return out


def hf_revision(repo_id: str) -> str | None:
    """Commit of the cached snapshot (refs/main) that from_pretrained resolves to."""
    from huggingface_hub import constants
    ref = Path(constants.HF_HUB_CACHE) / f"models--{repo_id.replace('/', '--')}" / "refs" / "main"
    return ref.read_text().strip() if ref.is_file() else None


def environment() -> dict:
    env = {"python": sys.version.split()[0], "platform": platform.platform(),
           "packages": package_versions("omnivoice", "torch", "torchaudio", "transformers", "huggingface-hub",
                                        "gradio_client", "numpy", "soundfile"),
           "checkpoint": CHECKPOINT, "checkpoint_revision": hf_revision(CHECKPOINT),
           "asr_revision": hf_revision("openai/whisper-large-v3-turbo")}
    try:
        import torch
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            env.update(gpu=torch.cuda.get_device_name(0), cuda=torch.version.cuda,
                       vram_free_gib_at_start=round(free / 2**30, 2), vram_total_gib=round(total / 2**30, 2))
    except Exception as exc:  # noqa: BLE001 - recorded, not fatal
        env["torch_error"] = repr(exc)
    return env
