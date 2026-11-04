"""Phase 0.2 input validation for a model video (CPU only).

Checks container/stream facts with ffprobe, then decodes every frame (or every Nth) and tracks
the largest face with OpenCV YuNet. Writes <name>.report.json, <name>.report.md and a per-frame
<name>.frames.csv next to --out.

The face metrics are proxies chosen to predict how lip-sync engines that crop a fixed face box per
frame (Wav2Lip, MuseTalk) will behave. They are not the engines' own detectors (S3FD / DWPose), so a
PASS here is necessary, not sufficient: the engine's own preprocessing still has to succeed.

    uv run python tools/validate_video.py VIDEO --kind static|motion|full [--out DIR] [--stride N]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = HERE.parent / "models" / "face_detection_yunet_2023mar.onnx"
DETECT_WIDTH = 640  # frames are downscaled to this width for detection only
FACE_SCORE = 0.85  # below this YuNet boxes on real model videos were hands, not faces (2026-11-04 review)

# Initial heuristics, per video kind. Tune them after the first real model videos are reviewed;
# every threshold that decides a verdict lives here so the report can quote it.
THRESHOLDS = {
    "static": {
        "duration_s": (10.0, 120.0),
        "head_jitter_p95": (0.03, 0.06),  # face-centre move per frame / face width
        "yaw_p95": (0.20, 0.35),  # |nose offset| / inter-ocular distance
        "body_motion_mean": (2.0, 5.0),  # mean abs grey diff outside the face, 0-255
    },
    "motion": {
        "duration_s": (10.0, 120.0),
        "head_jitter_p95": (0.06, 0.12),
        "yaw_p95": (0.35, 0.55),
        "body_motion_mean": (8.0, 15.0),
    },
    "full": {
        "duration_s": (45.0, 75.0),
        "head_jitter_p95": (0.06, 0.12),
        "yaw_p95": (0.35, 0.55),
        "body_motion_mean": (8.0, 15.0),
    },
}
COMMON = {
    "face_presence": (0.98, 0.90),  # fraction of analysed frames with a face (PASS >=, WARN >=)
    "face_height_px": (200, 128),  # median face box height in source pixels
    "detect_score_p05": (0.80, 0.65),
    "blur_median": (60.0, 25.0),  # Laplacian variance of the face resized to 256 px
    "brightness": (60.0, 200.0),  # acceptable mean face luma
    "longest_gap_s": (0.2, 1.0),
    "track_breaks": (0, 3),
}


@dataclass
class Check:
    name: str
    value: object
    status: str  # PASS | WARN | FAIL | INFO
    note: str = ""


@dataclass
class FrameRow:
    idx: int
    t: float
    faces: int
    score: float = 0.0
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    yaw: float = 0.0
    roll_deg: float = 0.0
    blur: float = 0.0
    brightness: float = 0.0
    mouth_ok: int = 0
    global_motion: float = 0.0
    body_motion: float = 0.0


@dataclass
class Report:
    video: str
    kind: str
    analysed_at: str
    probe: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)
    verdict: str = "INFO"


def ffprobe(path: Path) -> dict:
    exe = shutil.which("ffprobe")
    if not exe:
        raise SystemExit("ffprobe not found on PATH")
    out = subprocess.run(
        [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True, encoding="utf-8",
    ).stdout
    return json.loads(out)


def ratio(text: str | None) -> float:
    if not text or text in ("0/0", "N/A"):
        return 0.0
    num, _, den = text.partition("/")
    return float(num) / float(den or 1)


def rotation_of(stream: dict) -> int:
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            return int(side["rotation"])
    return int(stream.get("tags", {}).get("rotate", 0))


def summarize_probe(raw: dict) -> dict:
    video = next((s for s in raw["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise SystemExit("no video stream")
    audio = next((s for s in raw["streams"] if s.get("codec_type") == "audio"), None)
    rot = rotation_of(video)
    w, h = int(video["width"]), int(video["height"])
    dw, dh = (h, w) if abs(rot) in (90, 270) else (w, h)
    return {
        "container": raw["format"].get("format_name"),
        "codec": video.get("codec_name"),
        "profile": video.get("profile"),
        "pix_fmt": video.get("pix_fmt"),
        "coded_size": [w, h],
        "rotation": rot,
        "display_size": [dw, dh],
        "orientation": "portrait" if dh > dw else ("landscape" if dw > dh else "square"),
        "r_frame_rate": ratio(video.get("r_frame_rate")),
        "avg_frame_rate": ratio(video.get("avg_frame_rate")),
        "nb_frames": int(video.get("nb_frames", 0) or 0),
        "duration_s": float(video.get("duration") or raw["format"].get("duration") or 0),
        "bit_rate": int(video.get("bit_rate") or raw["format"].get("bit_rate") or 0),
        "audio": audio.get("codec_name") if audio else None,
    }


def grade(value: float, good: float, ok: float, higher_is_better: bool = True) -> str:
    if higher_is_better:
        return "PASS" if value >= good else ("WARN" if value >= ok else "FAIL")
    return "PASS" if value <= good else ("WARN" if value <= ok else "FAIL")


def iou(a: FrameRow, b: FrameRow) -> float:
    x1, y1 = max(a.x, b.x), max(a.y, b.y)
    x2, y2 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union else 0.0


def box_iou(a, b) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return float(inter / union) if union else 0.0


def analyse_frames(path: Path, model: Path, stride: int) -> tuple[list[FrameRow], float, tuple[int, int], dict]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"OpenCV cannot open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    detector = None
    rows: list[FrameRow] = []
    prev_small = None
    first_small = last_small = None
    frame_diffs: list[float] = []
    size = (0, 0)
    last_box = None
    idx = -1
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        if idx % stride:
            continue
        h, w = frame.shape[:2]
        size = (w, h)
        scale = DETECT_WIDTH / w if w > DETECT_WIDTH else 1.0
        small = cv2.resize(frame, (round(w * scale), round(h * scale))) if scale != 1.0 else frame
        if detector is None:
            detector = cv2.FaceDetectorYN.create(str(model), "", (small.shape[1], small.shape[0]), 0.6, 0.3, 50)
        _, faces = detector.detect(small)
        if faces is not None:
            # YuNet fires on gesturing hands at ~0.6-0.8; only confident boxes count as faces.
            faces = faces[faces[:, 14] >= FACE_SCORE]
            faces = faces if len(faces) else None
        row = FrameRow(idx=idx, t=idx / fps, faces=0 if faces is None else len(faces))

        grey = cv2.cvtColor(cv2.resize(frame, (160, round(160 * h / w))), cv2.COLOR_BGR2GRAY).astype(np.float32)
        if first_small is None:
            first_small = grey
        last_small = grey
        diff = None if prev_small is None else np.abs(grey - prev_small)
        prev_small = grey
        if diff is not None:
            row.global_motion = float(diff.mean())
            frame_diffs.append(row.global_motion)

        if row.faces:
            # Follow the same person: the box overlapping the previous one, else the most confident.
            if last_box is not None:
                f = max(faces, key=lambda r: (box_iou(r[:4] / scale, last_box), r[14]))
            else:
                f = max(faces, key=lambda r: r[14])
            f = f / np.array([scale] * 14 + [1.0])
            last_box = f[:4]
            row.score = float(f[14])
            row.x, row.y, row.w, row.h = map(float, f[:4])
            reye, leye, nose, rmouth, lmouth = f[4:14].reshape(5, 2)
            iod = float(np.linalg.norm(leye - reye)) or 1.0
            mid = (reye + leye) / 2
            row.yaw = float((nose[0] - mid[0]) / iod)
            row.roll_deg = math.degrees(math.atan2(leye[1] - reye[1], leye[0] - reye[0]))
            # Mouth corners must sit in the lower half of the box and below the nose; a hand or
            # microphone over the mouth usually breaks this or drops the score.
            lower = row.y + row.h * 0.5
            row.mouth_ok = int(min(rmouth[1], lmouth[1]) > max(lower, nose[1]) and row.score >= 0.75)

            x0, y0 = max(0, int(row.x)), max(0, int(row.y))
            x1, y1 = min(w, int(row.x + row.w)), min(h, int(row.y + row.h))
            crop = frame[y0:y1, x0:x1]
            if crop.size:
                g = cv2.cvtColor(cv2.resize(crop, (256, 256)), cv2.COLOR_BGR2GRAY)
                row.blur = float(cv2.Laplacian(g, cv2.CV_64F).var())
                row.brightness = float(g.mean())
            if diff is not None:
                k = 160 / w
                mask = np.ones_like(diff, dtype=bool)
                mask[int(y0 * k):int(y1 * k) + 1, int(x0 * k):int(x1 * k) + 1] = False
                row.body_motion = float(diff[mask].mean()) if mask.any() else 0.0
        elif diff is not None:
            row.body_motion = row.global_motion
        rows.append(row)
    cap.release()

    median_diff = float(np.median(frame_diffs)) if frame_diffs else 0.0
    seam = float(np.abs(first_small - last_small).mean()) if first_small is not None else 0.0
    loop = {
        "first_last_diff": round(seam, 2),
        "median_frame_diff": round(median_diff, 2),
        # How many "normal" frame steps a hard cut from the last to the first frame looks like.
        "seam_ratio": round(seam / median_diff, 1) if median_diff else None,
    }
    return rows, fps, size, loop


def p(values, q):
    return float(np.percentile(values, q)) if len(values) else 0.0


def build_report(path: Path, kind: str, rows: list[FrameRow], fps: float, size, loop: dict, probe: dict, stride: int) -> Report:
    rep = Report(video=str(path), kind=kind, analysed_at=time.strftime("%Y-%m-%d %H:%M:%S"), probe=probe)
    th = THRESHOLDS[kind]
    c = rep.checks
    W, H = size

    # Container / stream facts
    c.append(Check("codec", probe["codec"], "PASS" if probe["codec"] in ("h264", "hevc") else "WARN",
                   "h264/hevc decode everywhere; other codecs are re-encoded before preprocessing"))
    short = min(probe["display_size"])
    c.append(Check("resolution", f"{probe['display_size'][0]}x{probe['display_size'][1]}",
                   grade(short, 1080, 720), "short side >= 1080 PASS, >= 720 WARN"))
    c.append(Check("orientation", probe["orientation"], "PASS" if probe["orientation"] == "portrait" else "WARN",
                   "TikTok LIVE is 9:16; landscape works for the engines but wastes pixels"))
    if probe["rotation"]:
        c.append(Check("rotation_metadata", probe["rotation"], "WARN",
                       "phone rotation flag; bake it in (ffmpeg re-encode) because some engine loaders ignore it"))
    vfr = abs(probe["r_frame_rate"] - probe["avg_frame_rate"]) > 0.5
    c.append(Check("fps", round(probe["avg_frame_rate"], 3),
                   "WARN" if vfr or not 24 <= probe["avg_frame_rate"] <= 60 else "PASS",
                   ("variable frame rate suspected (r=%.2f avg=%.2f); resample to 25 fps CFR" % (probe["r_frame_rate"], probe["avg_frame_rate"])) if vfr
                   else "engines resample to 25 fps (one video frame per 40 ms of audio)"))
    lo, hi = th["duration_s"]
    d = probe["duration_s"]
    c.append(Check("duration_s", round(d, 2), "PASS" if lo <= d <= hi else "WARN", f"expected {lo:g}-{hi:g} s for kind={kind}"))
    mbps = probe["bit_rate"] / 1e6
    # Bitrate depends on content (a still scene compresses well), so it never fails a video on its own.
    c.append(Check("bitrate_mbps", round(mbps, 2), "PASS" if mbps >= 8 else "WARN",
                   ">= 8 Mbps PASS, else WARN: check the face crop for compression artefacts"))

    faces = [r for r in rows if r.faces]
    n = len(rows)
    presence = len(faces) / n if n else 0.0
    rep.stats = {"frames_analysed": n, "stride": stride, "decoded_size": [W, H], "decode_fps": fps, "loop": loop}
    c.append(Check("face_presence", round(presence, 4), grade(presence, *COMMON["face_presence"]),
                   "fraction of analysed frames with a detected face"))
    multi = sum(1 for r in rows if r.faces > 1)
    c.append(Check("multiple_faces_frames", multi, "PASS" if multi == 0 else "WARN",
                   "engines take one face; a second face (poster, mirror) can steal the crop"))
    if not faces:
        rep.verdict = "FAIL"
        return rep

    heights = [r.h for r in faces]
    c.append(Check("face_height_px_median", round(p(heights, 50)), grade(p(heights, 50), *COMMON["face_height_px"]),
                   "lip-sync crops are 256 px; smaller faces get upscaled and blur"))
    inside = sum(1 for r in faces if r.x - 0.15 * r.w >= 0 and r.y - 0.15 * r.h >= 0
                 and r.x + 1.15 * r.w <= W and r.y + 1.25 * r.h <= H) / len(faces)
    c.append(Check("face_inside_frame_with_margin", round(inside, 4), grade(inside, 0.99, 0.95),
                   "face box + margin (chin/jaw padding the engines add) stays inside the frame"))
    scores = [r.score for r in faces]
    c.append(Check("detect_score_p05", round(p(scores, 5), 3), grade(p(scores, 5), *COMMON["detect_score_p05"]),
                   "low-confidence frames usually mean blur, extreme angle or occlusion"))
    mouth = sum(r.mouth_ok for r in faces) / len(faces)
    c.append(Check("mouth_visible_ratio", round(mouth, 4), grade(mouth, 0.98, 0.90),
                   "HEURISTIC occlusion proxy from YuNet mouth landmarks; review flagged frames by eye"))

    # Tracking: consecutive analysed frames that both have a face
    ious, jitter, scale_ch = [], [], []
    breaks = 0
    gap = longest_gap = 0
    prev = None
    for r in rows:
        if not r.faces:
            gap += 1
            longest_gap = max(longest_gap, gap)
            prev = None
            continue
        gap = 0
        if prev is not None:
            v = iou(prev, r)
            ious.append(v)
            if v < 0.3:
                breaks += 1
            cx0, cy0 = prev.x + prev.w / 2, prev.y + prev.h / 2
            cx1, cy1 = r.x + r.w / 2, r.y + r.h / 2
            jitter.append(math.hypot(cx1 - cx0, cy1 - cy0) / max(prev.w, 1.0) / stride)
            scale_ch.append(abs(r.h - prev.h) / max(prev.h, 1.0) / stride)
        prev = r
    gap_s = longest_gap * stride / fps
    c.append(Check("longest_face_gap_s", round(gap_s, 3), grade(gap_s, *COMMON["longest_gap_s"], higher_is_better=False),
                   "longest run without a face; the engine must hold or skip these frames"))
    c.append(Check("track_breaks", breaks, grade(breaks, *COMMON["track_breaks"], higher_is_better=False),
                   "consecutive-frame IoU < 0.3 (face box jumps)"))
    jp95 = p(jitter, 95)
    c.append(Check("head_jitter_p95", round(jp95, 4), grade(jp95, *th["head_jitter_p95"], higher_is_better=False),
                   "per-frame face-centre move / face width; high values = fast head motion, visible seam risk"))
    c.append(Check("face_scale_change_p95", round(p(scale_ch, 95), 4), grade(p(scale_ch, 95), 0.03, 0.06, higher_is_better=False),
                   "per-frame face size change (moving toward/away from camera)"))
    yaw95 = p([abs(r.yaw) for r in faces], 95)
    c.append(Check("yaw_p95", round(yaw95, 3), grade(yaw95, *th["yaw_p95"], higher_is_better=False),
                   "head-turn proxy; ~0.5 is a clear three-quarter view where mouth inpainting degrades"))
    roll95 = p([abs(r.roll_deg) for r in faces], 95)
    c.append(Check("roll_deg_p95", round(roll95, 1), grade(roll95, 10, 20, higher_is_better=False), "head tilt"))
    body = float(np.mean([r.body_motion for r in rows[1:]])) if n > 1 else 0.0
    lo_b, hi_b = th["body_motion_mean"]
    if kind == "static":
        body_status = grade(body, lo_b, hi_b, higher_is_better=False)
    else:
        body_status = "PASS" if body >= lo_b * 0.25 else "WARN"  # natural-motion clips should actually move
    c.append(Check("body_motion_mean", round(body, 2), body_status,
                   "mean abs grey diff outside the face (0-255); static expects little, motion/full expects some"))
    blur = p([r.blur for r in faces], 50)
    c.append(Check("face_blur_median", round(blur, 1), grade(blur, *COMMON["blur_median"]),
                   "Laplacian variance on the 256 px face crop; low = soft focus / motion blur"))
    bright = p([r.brightness for r in faces], 50)
    lo_l, hi_l = COMMON["brightness"]
    c.append(Check("face_brightness_median", round(bright, 1), "PASS" if lo_l <= bright <= hi_l else "WARN",
                   f"mean face luma; outside {lo_l:g}-{hi_l:g} = under/over exposed"))
    sr = loop["seam_ratio"]
    c.append(Check("loop_seam_ratio", sr, "INFO",
                   "first-vs-last frame difference in units of a normal frame step; >>3 means a plain loop "
                   "will visibly jump (ping-pong or crossfade looping avoids it)"))

    statuses = [x.status for x in c]
    rep.verdict = "FAIL" if "FAIL" in statuses else ("WARN" if "WARN" in statuses else "PASS")
    rep.stats.update({
        "face_iou_median": round(p(ious, 50), 4),
        "yaw_range": [round(min(r.yaw for r in faces), 3), round(max(r.yaw for r in faces), 3)],
        "face_box_median_px": [round(p([r.w for r in faces], 50)), round(p(heights, 50))],
    })
    return rep


def write_outputs(rep: Report, rows: list[FrameRow], out: Path, stem: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data = asdict(rep)
    (out / f"{stem}.report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / f"{stem}.frames.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(asdict(rows[0]).keys()) if rows else ["idx"])
        wr.writeheader()
        for r in rows:
            wr.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(r).items()})
    lines = [
        f"# Input validation — {Path(rep.video).name}",
        "",
        f"- Kind: `{rep.kind}`  ·  Verdict: **{rep.verdict}**  ·  Analysed: {rep.analysed_at}",
        f"- Probe: {rep.probe['codec']} {rep.probe['display_size'][0]}x{rep.probe['display_size'][1]} "
        f"{rep.probe['avg_frame_rate']:.3f} fps, {rep.probe['duration_s']:.2f} s, audio={rep.probe['audio']}",
        "",
        "| Check | Value | Status | Note |",
        "|---|---|---|---|",
    ]
    lines += [f"| {x.name} | {x.value} | {x.status} | {x.note} |" for x in (Check(**d) for d in data["checks"])]
    lines += ["", "Face metrics come from OpenCV YuNet, not the engines' own detectors. "
              "A PASS here does not replace the engine preprocessing run."]
    (out / f"{stem}.report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def lower_priority() -> None:
    # The machine may be streaming; never compete with OBS for CPU.
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 10)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--kind", choices=sorted(THRESHOLDS), required=True)
    ap.add_argument("--out", type=Path, default=Path("outputs/phase0/validation"))
    ap.add_argument("--stride", type=int, default=1, help="analyse every Nth frame")
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    args = ap.parse_args(argv)
    if not args.model.exists():
        raise SystemExit(f"YuNet model missing: {args.model}")
    lower_priority()
    t0 = time.perf_counter()
    probe = summarize_probe(ffprobe(args.video))
    rows, fps, size, loop = analyse_frames(args.video, args.model, max(1, args.stride))
    rep = build_report(args.video, args.kind, rows, fps, size, loop, probe, max(1, args.stride))
    rep.stats["analysis_seconds"] = round(time.perf_counter() - t0, 2)
    write_outputs(rep, rows, args.out, args.video.stem)
    print(f"{rep.verdict}  {args.video.name}  ({len(rows)} frames, {rep.stats['analysis_seconds']} s)")
    for x in rep.checks:
        if x.status in ("WARN", "FAIL"):
            print(f"  {x.status:4}  {x.name} = {x.value}  - {x.note}")
    print(f"report: {args.out / (args.video.stem + '.report.md')}")
    return 0 if rep.verdict != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
