"""Cut a Media Library video into clips on the server, each clip becoming a new video of the same owner.

- mode COPY (default): ffmpeg stream copy. Codec, resolution, fps, bitrate, audio and rotation stay exactly as the
  original (no re-encoding, so no quality loss). A clip can only start on a keyframe: the start snaps to the last
  keyframe at/before the chosen time (the Web shows the keyframes, GET /{id}/keyframes, and snaps cuts to them).
- mode PRECISE: frame-accurate start, the video is re-encoded with the same codec family at visually lossless quality
  (resolution, fps and pixel format kept).

Cutting a long video takes a while (download from storage, ffmpeg, upload back), so it runs as a background job the
Web polls. Jobs live in memory: a restart loses the progress of a running job, never a finished clip.
"""
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import storage_service
from app.config import settings
from app.media_library import DB, THUMB_MAX_BYTES, media_view, _links, owned_media
from app.ownership import Owner
from app.storage_errors import StorageError, fail
from app.storage_models import (MediaItem, MediaItemPresenter, MediaStorageObject, MediaThumbnail, ProductMedia,
                                new_id)
from app.storage_providers import provider, upload_provider

log = logging.getLogger("sarae.media_clips")
router = APIRouter(prefix="/api/media-library", tags=["Media Library"])
MAX_CLIPS = 50
MIN_CLIP_SECONDS = 0.5
JOB_TTL_SECONDS = 3600
# Visually lossless re-encode for PRECISE mode, per source codec (anything else falls back to H.264).
ENCODERS = {"hevc": ("libx265", "20"), "h264": ("libx264", "16")}

_jobs: dict[str, dict] = {}
_keyframe_cache: dict[tuple[str, str], dict] = {}  # (media id, storage object) → keyframes; the object never changes
_jobs_lock = threading.Lock()
_worker = threading.Semaphore(1)  # one ffmpeg job at a time: cutting is CPU/disk heavy


def _tool(name: str) -> str:
    found = getattr(settings, f"sarae_{name}_path") or shutil.which(name)
    if not found:
        raise fail(503, "FFMPEG_MISSING", "Máy chủ chưa cài ffmpeg nên chưa cắt được video")
    return found


class ClipSpec(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    name: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _order(self):
        if self.end - self.start < MIN_CLIP_SECONDS:
            raise ValueError(f"Mỗi đoạn phải dài ít nhất {MIN_CLIP_SECONDS} giây")
        return self


class ClipRequest(BaseModel):
    clips: list[ClipSpec] = Field(min_length=1, max_length=MAX_CLIPS)
    mode: Literal["COPY", "PRECISE"] = "COPY"
    keep_links: bool = True  # copy products, presenters and tags of the original to every clip


def _job_view(job: dict) -> dict:
    return {key: job[key] for key in ("id", "media_id", "status", "mode", "total", "done", "phase", "step", "current", "progress",
                                      "started_at", "media_ids", "error")}


def _prune():
    limit = time.time() - JOB_TTL_SECONDS
    for job_id in [k for k, j in _jobs.items() if j["status"] in ("DONE", "FAILED") and j["finished_at"] < limit]:
        _jobs.pop(job_id, None)


@router.post("/{media_id}/clips", status_code=202)
def create_clips(media_id: str, body: ClipRequest, owner: Owner, db: DB):
    """Start cutting `clips` out of the video. Answers the job right away; poll GET /clip-jobs/{id}."""
    media = owned_media(db, owner["id"], media_id)
    if media.status != "AVAILABLE":
        raise fail(422, "MEDIA_NOT_AVAILABLE", "Video đã bị xóa, không thể cắt")
    if media.duration_seconds:
        for clip in body.clips:
            if clip.start >= media.duration_seconds:
                raise fail(422, "CLIP_OUT_OF_RANGE", "Có đoạn bắt đầu sau khi video đã kết thúc")
    ffmpeg, ffprobe = _tool("ffmpeg"), _tool("ffprobe")
    job = {"id": new_id(), "owner_id": owner["id"], "media_id": media.id, "status": "QUEUED", "mode": body.mode,
           "total": len(body.clips), "done": 0, "phase": "Đang chờ máy chủ rảnh", "step": "QUEUED", "current": 0, "progress": 0.0,
           "started_at": time.time(), "media_ids": [], "error": None, "finished_at": 0.0}
    with _jobs_lock:
        _prune()
        _jobs[job["id"]] = job
    # The worker gets its own sessions on the request's engine (tests swap the engine).
    sessions = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False)
    threading.Thread(target=_run, args=(job, body, sessions, ffmpeg, ffprobe), name=f"clips-{job['id']}", daemon=True).start()
    return _job_view(job)


@router.get("/{media_id}/keyframes")
def get_keyframes(media_id: str, owner: Owner, db: DB):
    """Times (s) where COPY mode can start a clip. Reads the whole stored file once, then answers from memory."""
    media = owned_media(db, owner["id"], media_id)
    if media.status != "AVAILABLE":
        raise fail(422, "MEDIA_NOT_AVAILABLE", "Video đã bị xóa")
    cached = _keyframe_cache.get((media.id, media.provider_file_id))
    if cached is None:
        ffprobe = _tool("ffprobe")
        with tempfile.TemporaryDirectory(prefix="sarae-keys-") as work:
            path = os.path.join(work, "source" + (os.path.splitext(media.filename)[1].lower() or ".mp4"))
            try:
                _download(db, media, path)
                cached = {"keyframes": _keyframes(ffprobe, path), "duration": _probe(ffprobe, path)["duration"]}
            except StorageError as exc:
                raise fail(exc.status or 422, exc.code, exc.message)
        _keyframe_cache[(media.id, media.provider_file_id)] = cached
    return cached


@router.get("/clip-jobs/{job_id}")
def get_clip_job(job_id: str, owner: Owner, db: DB):
    job = _jobs.get(job_id)
    if job is None or job["owner_id"] != owner["id"]:
        raise fail(404, "CLIP_JOB_NOT_FOUND", "Không tìm thấy tác vụ cắt video (máy chủ có thể đã khởi động lại)")
    view = _job_view(job)
    if job["media_ids"]:
        items = list(db.scalars(select(MediaItem).where(MediaItem.id.in_(job["media_ids"]))))
        links = _links(db, [m.id for m in items])
        view["items"] = [media_view(m, links[m.id]) for m in items]
    return view


# ----- worker ---------------------------------------------------------------------------------------

def _set(job: dict, **values):
    with _jobs_lock:
        job.update(values)


def _run(job: dict, body: ClipRequest, sessions: sessionmaker, ffmpeg: str, ffprobe: str):
    with _worker, tempfile.TemporaryDirectory(prefix="sarae-clips-") as work, sessions() as db:
        try:
            _set(job, status="RUNNING")
            _cut_all(db, job, body, work, ffmpeg, ffprobe)
            _set(job, status="DONE", step="DONE", phase="Hoàn tất", progress=1.0)
        except StorageError as exc:
            log.warning("clip job %s failed: %s", job["id"], exc.code)
            _set(job, status="FAILED", error=exc.message)
        except Exception:  # noqa: BLE001 - a background job must always end with a status
            log.exception("clip job %s crashed", job["id"])
            _set(job, status="FAILED", error="Cắt video thất bại, hãy thử lại")
        finally:
            _set(job, finished_at=time.time())


def _cut_all(db: Session, job: dict, body: ClipRequest, work: str, ffmpeg: str, ffprobe: str):
    source = db.get(MediaItem, job["media_id"])
    ext = os.path.splitext(source.filename)[1].lower() or ".mp4"
    original = os.path.join(work, "source" + ext)
    # Progress: reading the original is the first 25 %, then each clip gets an equal share (cut = first half, save = second).
    _set(job, step="DOWNLOAD", phase="Đang lấy video gốc từ kho")
    _download(db, source, original, lambda read: _set(job, progress=0.25 * min(1.0, read / max(1, source.size_bytes))))
    _set(job, step="ANALYZE", phase="Đang đọc thông tin video", progress=0.25)
    info = _probe(ffprobe, original)
    keyframes = _keyframes(ffprobe, original) if body.mode == "COPY" else []
    duration = info["duration"] or source.duration_seconds or 0
    products = list(db.scalars(select(ProductMedia.product_id).where(ProductMedia.media_id == source.id)))
    presenters = list(db.scalars(select(MediaItemPresenter.presenter_id).where(MediaItemPresenter.media_id == source.id)))
    stem = os.path.splitext(source.filename)[0][:200]
    base_name = source.display_name or stem
    for index, clip in enumerate(body.clips, start=1):
        start, end = clip.start, min(clip.end, duration) if duration else clip.end
        if keyframes:  # stream copy can only start on a keyframe: the last one at/before the chosen start
            start = max((k for k in keyframes if k <= start + 0.05), default=0.0)
        if end - start < MIN_CLIP_SECONDS:
            raise StorageError("CLIP_OUT_OF_RANGE", f"Đoạn {index} nằm ngoài thời lượng video")
        share = 0.75 / job["total"]
        _set(job, step="CUT", current=index, phase=f"Đang cắt đoạn {index}/{job['total']}", progress=0.25 + share * (index - 1))
        path = os.path.join(work, f"clip{index}{ext}")
        _ffmpeg(ffmpeg, _cut_args(original, path, start, end, body.mode, info, ext))
        clip_info = _probe(ffprobe, path)
        _set(job, step="SAVE", phase=f"Đang lưu đoạn {index}/{job['total']} vào kho", progress=0.25 + share * (index - 0.5))
        media = _store(db, source, path, f"{stem}_part{index}{ext}", clip.name.strip() or f"{base_name} - đoạn {index}",
                       clip_info, body.keep_links, products, presenters)
        # The new media is committed by _store. Record it before optional cover work so retries skip this clip.
        _set(job, done=index, media_ids=[*job["media_ids"], media.id], progress=0.25 + share * index)
        cover = _cover(ffmpeg, path, work, clip_info["duration"])
        if cover:
            db.add(MediaThumbnail(media_id=media.id, owner_id=media.owner_id, mime_type="image/jpeg", content=cover))
            db.commit()
        os.remove(path)


def _cut_args(src: str, dst: str, start: float, end: float, mode: str, info: dict, ext: str) -> list[str]:
    length = ["-t", f"{end - start:.3f}", "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0", "-dn", "-sn"]
    if mode == "COPY":
        # `start` is already a keyframe time, so the input seek lands exactly on it. (Seeking to an arbitrary time with
        # stream copy may land several keyframes earlier; output-side -ss drops the keyframe and breaks the start.)
        args = ["-ss", f"{start:.3f}", "-i", src, *length, "-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        args = ["-ss", f"{start:.3f}", "-i", src, *length]  # re-encoding: input seek is frame-accurate and fast
        encoder, crf = ENCODERS.get(info["codec"], ENCODERS["h264"])
        args += ["-c:v", encoder, "-crf", crf, "-preset", "medium"]
        if info["pix_fmt"]:
            args += ["-pix_fmt", info["pix_fmt"]]
        if encoder == "libx265" and ext in (".mp4", ".mov", ".m4v"):
            args += ["-tag:v", "hvc1"]  # so QuickTime/Safari/TikTok players recognise HEVC in MP4
        args += ["-c:a", "aac", "-b:a", f"{max(128, min(320, info['audio_kbps'] or 192))}k"]
    return [*args, "-movflags", "+faststart", dst]


def _ffmpeg(ffmpeg: str, args: list[str]):
    result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True,
                            timeout=6 * 3600)
    if result.returncode != 0:
        log.error("ffmpeg failed: %s", result.stderr[-2000:])
        raise StorageError("CLIP_FFMPEG_FAILED", "ffmpeg không cắt được video này", 422)


def _download(db: Session, media: MediaItem, path: str, on_progress=None):
    read = 0
    with open(path, "wb") as out:
        for chunk in provider(media.provider, db).open_read(media.provider_file_id).iter():
            out.write(chunk)
            read += len(chunk)
            if on_progress:
                on_progress(read)


def _keyframes(ffprobe: str, path: str) -> list[float]:
    """Keyframe times of the first video stream, from packet flags (no decoding)."""
    result = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags",
                             "-of", "csv=p=0", path], capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise StorageError("CLIP_PROBE_FAILED", "Không đọc được thông tin video", 422)
    times = set()
    for line in result.stdout.splitlines():
        pts, _, flags = line.partition(",")
        if "K" in flags and pts not in ("", "N/A"):
            times.add(round(float(pts), 3))
    return sorted(times)


def _probe(ffprobe: str, path: str) -> dict:
    result = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                            capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise StorageError("CLIP_PROBE_FAILED", "Không đọc được thông tin video", 422)
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    width, height = video.get("width"), video.get("height")
    rotation = 0
    for side in video.get("side_data_list") or []:
        rotation = int(float(side.get("rotation", 0) or 0))
    rotation = rotation or int((video.get("tags") or {}).get("rotate", 0) or 0)
    if width and height and abs(rotation) % 180 == 90:
        width, height = height, width  # store the size as displayed (portrait phone videos)
    duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0) or None
    return {"duration": duration, "width": width, "height": height, "codec": video.get("codec_name") or "",
            "pix_fmt": video.get("pix_fmt") or "", "audio_kbps": int(audio.get("bit_rate") or 0) // 1000}


def _cover(ffmpeg: str, path: str, work: str, duration: float | None) -> bytes | None:
    target = os.path.join(work, "cover.jpg")
    for quality in ("4", "8"):
        try:
            _ffmpeg(ffmpeg, ["-ss", f"{min(0.5, (duration or 1) / 2):.3f}", "-i", path, "-frames:v", "1",
                             "-vf", "scale='min(540,iw)':-2", "-q:v", quality, target])
        except StorageError:
            return None
        with open(target, "rb") as file:
            data = file.read()
        if len(data) <= THUMB_MAX_BYTES:
            return data
    return None


def _file_chunks(path: str, sha) -> Iterator[bytes]:
    with open(path, "rb") as file:
        while piece := file.read(1024 * 1024):
            sha.update(piece)
            yield piece


def _store(db: Session, source: MediaItem, path: str, filename: str, display_name: str, info: dict, keep_links: bool,
           products: list[int], presenters: list[int]) -> MediaItem:
    size = os.path.getsize(path)
    storage_service._check_quota(db, source.owner_id, size)
    store = upload_provider(db)
    media_id = new_id()
    sha = hashlib.sha256()
    stored = store.put(source.owner_id, media_id, filename, source.mime_type, size, _file_chunks(path, sha),
                       chunk_size=storage_service.CHUNK_SIZE)
    media = MediaItem(id=media_id, owner_id=source.owner_id, provider=store.name, provider_file_id=stored.object_id,
                      filename=filename, display_name=display_name[:200], mime_type=source.mime_type, size_bytes=size,
                      sha256=sha.hexdigest(), md5=stored.md5, duration_seconds=round(info["duration"], 3) if info["duration"] else None,
                      width=info["width"], height=info["height"],
                      notes=source.notes if keep_links else "", tags=source.tags if keep_links else "")
    db.add(media)
    db.add(MediaStorageObject(media_id=media.id, owner_id=source.owner_id, provider=store.name,
                              provider_file_id=stored.object_id, sha256=media.sha256, size_bytes=size))
    db.flush()
    if keep_links:
        for product_id in products:
            last = db.scalar(select(ProductMedia.sort_order).where(ProductMedia.product_id == product_id)
                             .order_by(ProductMedia.sort_order.desc()).limit(1))
            db.add(ProductMedia(owner_id=source.owner_id, product_id=product_id, media_id=media.id, sort_order=(last or 0) + 1))
        db.add_all(MediaItemPresenter(media_id=media.id, presenter_id=pid, owner_id=source.owner_id) for pid in presenters)
    db.commit()
    log.info("clip %s cut from media %s (%s bytes)", media.id, source.id, size)
    return media
