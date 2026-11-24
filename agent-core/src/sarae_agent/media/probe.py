"""FFprobe metadata used before handing a local file to OBS.

OBS receives the original file for zero-rotation media of any size or aspect ratio:
the playback engine sets scene item bounds that cover the OBS canvas. Rotated files
get a cached, display-oriented copy; the source file is never changed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from sarae_agent.config import paths

log = logging.getLogger("sarae.media.probe")

# One FFmpeg run per cached copy: a pre-LIVE warm-up and playback may ask for the same file at once.
_render_locks: dict[str, threading.Lock] = {}
_render_locks_guard = threading.Lock()


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    fps: str
    rotation: int
    audio: bool
    sample_rate: int | None
    channels: int | None
    duration: float = 0.0


def probe_media(path: Path) -> MediaInfo:
    if not shutil.which("ffprobe"):
        raise FileNotFoundError("Thiếu ffprobe.exe trong PATH của Windows Agent")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height,avg_frame_rate,sample_rate,channels:stream_tags=rotate:stream_side_data=rotation:format=duration",
         "-of", "json", str(path)], capture_output=True, text=True, timeout=15, check=True,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("File không có video stream")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    side = next((s for s in video.get("side_data_list", []) if "rotation" in s), {})
    angle = side.get("rotation", video.get("tags", {}).get("rotate", 0))
    rotation = round(float(angle)) % 360
    if rotation not in (0, 90, 180, 270):
        raise ValueError(f"Rotation metadata không hỗ trợ: {angle}")
    info = MediaInfo(int(video["width"]), int(video["height"]),
                     video.get("avg_frame_rate") or "0/0", rotation, bool(audio),
                     int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
                     int(audio["channels"]) if audio and audio.get("channels") else None,
                     float(payload.get("format", {}).get("duration") or 0))
    log.info("Media %s: %sx%s fps=%s rotation=%s audio=%s sample_rate=%s channels=%s",
             path.name, info.width, info.height, info.fps, info.rotation, info.audio,
             info.sample_rate, info.channels)
    return info


def obs_file(path: Path, info: MediaInfo) -> Path:
    """Bake display matrix into a local cache copy using FFmpeg autorotation. Any aspect ratio is left to OBS,
    which covers the canvas with the scene item's bounds (see PlaybackEngine._fit_video)."""
    width, height = (info.height, info.width) if info.rotation in (90, 270) else (info.width, info.height)
    if info.rotation == 0:
        return path
    if not shutil.which("ffmpeg"):
        raise FileNotFoundError("Thiếu ffmpeg.exe trong PATH của Windows Agent")
    stat = path.stat()
    key = hashlib.sha256(f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
    folder = paths.root / "obs_media"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{key}.mp4"
    with _render_locks_guard:
        lock = _render_locks.setdefault(key, threading.Lock())
    with lock:
        if target.is_file():
            return target
        temporary = folder / f"{key}.partial.mp4"
        try:
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                            "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-metadata:s:v:0",
                            "rotate=0", str(temporary)], capture_output=True, text=True, timeout=1800,
                           check=True)
            rendered = probe_media(temporary)
            if rendered.rotation != 0 or (rendered.width, rendered.height) != (width, height) or rendered.audio != info.audio:
                raise ValueError("FFmpeg không chuẩn hóa đúng orientation/khung hình/audio")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return target
