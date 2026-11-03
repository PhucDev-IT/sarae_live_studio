"""Audio measurement and WAV packing. Reports only: nothing here changes the audio it is given."""
from __future__ import annotations

import io

import numpy as np
import soundfile as sf

SILENCE_DB = -40.0     # a 10 ms frame quieter than this counts as silence
EDGE_CUT_DB = -40.0    # the last/first 100 ms of a reference louder than this is probably cut mid-speech


def to_float(audio: np.ndarray) -> np.ndarray:
    audio = np.asarray(audio)
    if audio.ndim > 1:
        audio = audio.mean(axis=-1 if audio.shape[-1] <= 8 else 0)
    if np.issubdtype(audio.dtype, np.integer):
        return audio.astype(np.float64) / 32768.0
    return audio.astype(np.float64)


def frame_db(audio: np.ndarray, sr: int, frame_ms: float = 10.0) -> np.ndarray:
    x = to_float(audio)
    n = max(1, int(sr * frame_ms / 1000))
    frames = len(x) // n
    if frames == 0:
        return np.array([_db(x)])
    rms = np.sqrt(np.mean(x[:frames * n].reshape(frames, n) ** 2, axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-9))


def _db(x: np.ndarray) -> float:
    return float(20 * np.log10(max(float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0, 1e-9)))


def leading_silence_ms(audio: np.ndarray, sr: int, threshold_db: float = SILENCE_DB) -> float:
    levels = frame_db(audio, sr)
    loud = np.nonzero(levels > threshold_db)[0]
    return float((loud[0] if len(loud) else len(levels)) * 10.0)


def trailing_silence_ms(audio: np.ndarray, sr: int, threshold_db: float = SILENCE_DB) -> float:
    levels = frame_db(audio, sr)
    loud = np.nonzero(levels > threshold_db)[0]
    return float((len(levels) - 1 - loud[-1] if len(loud) else len(levels)) * 10.0)


def edge_db(audio: np.ndarray, sr: int, ms: float = 100.0, end: bool = True) -> float:
    x = to_float(audio)
    n = max(1, int(sr * ms / 1000))
    return _db(x[-n:] if end else x[:n])


def audio_stats(audio: np.ndarray, sr: int) -> dict:
    x = to_float(audio)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    return {
        "duration_s": round(len(x) / sr, 3),
        "peak_dbfs": round(20 * np.log10(max(peak, 1e-9)), 2),
        "rms_dbfs": round(_db(x), 2),
        "clipped_samples": int(np.sum(np.abs(x) >= 0.999)),
        "leading_silence_ms": leading_silence_ms(x, sr),
        "trailing_silence_ms": trailing_silence_ms(x, sr),
        "first_100ms_db": round(edge_db(x, sr, end=False), 1),
        "last_100ms_db": round(edge_db(x, sr, end=True), 1),
        "first_sample": round(float(x[0]), 5) if len(x) else 0.0,
        "last_sample": round(float(x[-1]), 5) if len(x) else 0.0,
    }


def reference_qc(audio: np.ndarray, sr: int, min_s: float = 1.0, max_s: float = 20.0) -> dict:
    """Report on a reference clip (never modifies it). Warnings follow docs/TTS_SERVER.md section 8."""
    stats = audio_stats(audio, sr)
    warnings = []
    d = stats["duration_s"]
    if d < min_s or d > max_s:
        warnings.append("duration_out_of_range")       # rejected by the service
    elif d < 3 or d > 10:
        warnings.append("duration_not_recommended")    # model card: 3-10 s
    if stats["last_100ms_db"] > EDGE_CUT_DB:
        warnings.append("end_may_be_cut")              # speech still running at the last sample
    if stats["first_100ms_db"] > EDGE_CUT_DB:
        warnings.append("start_may_be_cut")
    if stats["clipped_samples"]:
        warnings.append("clipping")
    if stats["rms_dbfs"] < -30:
        warnings.append("quiet")
    levels = frame_db(audio, sr)
    longest = run = 0
    for level in levels:
        run = run + 1 if level <= SILENCE_DB else 0
        longest = max(longest, run)
    stats["longest_pause_ms"] = longest * 10.0
    if longest * 10 > 1500:
        warnings.append("long_pause")
    return {**stats, "sample_rate": sr, "warnings": warnings}


def wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    """Exactly what omnivoice-demo returns, packed as WAV: (audio * 32767).astype(int16), mono PCM_16."""
    buf = io.BytesIO()
    sf.write(buf, (np.asarray(audio) * 32767).astype(np.int16), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()
