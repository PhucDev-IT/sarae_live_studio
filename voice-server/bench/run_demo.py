"""Phase 0 demo arms: send the golden set to the official omnivoice-demo already running on 127.0.0.1:7862.

    .venv\\Scripts\\python.exe bench\\run_demo.py --run-name <same run as run_library> --ref C:\\path\\voice_ref.wav

Arms:
  D-ASR   Reference Text left empty (the demo transcribes with Whisper): how the baseline was judged.
  D-MAN   only with --manual-transcript-file.
Every UI value is passed explicitly with the demo's own defaults (Auto language, 32 steps, CFG 2.0, denoise,
speed 1.0, no duration, preprocess and postprocess on), so a changed default in the page cannot slip in.
The demo cannot be seeded; its arms are compared to the library arms by distribution, not sample by sample.
Timings are wall-clock per HTTP call and include upload and the demo's per-click prompt creation (with ASR).
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from common import DEMO_URL, append_jsonl, golden, keep_reference, new_run, sha256


def gradio_uploads(since: float) -> list[Path]:
    root = Path(tempfile.gettempdir()) / "gradio"
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*") if p.is_file() and p.stat().st_mtime >= since
            and p.suffix.lower() in (".wav", ".mp3", ".flac")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--manual-transcript-file", type=Path)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--items", default="")
    ap.add_argument("--skip-long", action="store_true")
    ap.add_argument("--run-name")
    ap.add_argument("--url", default=DEMO_URL)
    args = ap.parse_args()

    from gradio_client import Client, handle_file

    run = new_run(args.run_name)
    out = run / "demo.jsonl"
    ref_info = keep_reference(run, "main", args.ref)
    ref = run / ref_info["copy"]
    items = [i for i in golden() if not args.items or i["id"] in args.items.split(",")]
    client = Client(args.url, verbose=False)
    arms = [("D-ASR", "")]
    if args.manual_transcript_file:
        arms.append(("D-MAN", args.manual_transcript_file.read_text(encoding="utf-8").strip()))

    started_at = time.time()
    for arm, ref_text in arms:
        for item in items:
            if item["category"] == "long" and args.skip_long:
                continue
            for rep in range(1 if item["category"] == "long" else args.reps):
                started = time.perf_counter()
                audio_path, status = client.predict(item["text"], "Auto", handle_file(str(ref)), ref_text, "",
                                                    32, 2.0, True, 1.0, None, True, True, api_name="/_clone_fn")
                wall = time.perf_counter() - started
                wav = run / arm / f"{item['id']}_r{rep}{Path(audio_path).suffix if audio_path else '.wav'}"
                wav.parent.mkdir(parents=True, exist_ok=True)
                if audio_path:
                    shutil.copy2(audio_path, wav)
                append_jsonl(out, {"arm": arm, "item": item["id"], "category": item["category"], "rep": rep,
                                   "wav": str(wav.relative_to(run)) if audio_path else None, "status": status,
                                   "wall_s": round(wall, 3)})
                print(f"[{arm}] {item['id']} r{rep} {wall:.2f}s {status}", flush=True)

    # Does the demo see the reference byte for byte? Gradio may convert an uploaded file before the model reads it.
    uploads = [{"path": str(p), "sha256": sha256(p), "same_as_ref": sha256(p) == ref_info["sha256"]}
               for p in gradio_uploads(started_at)]
    upload_check = {"ref_sha256": ref_info["sha256"], "uploads_seen": len(uploads),
                    "any_identical": any(u["same_as_ref"] for u in uploads),
                    "distinct_hashes": sorted({u["sha256"] for u in uploads})[:10]}
    (run / "demo_env.json").write_text(json.dumps({"url": args.url, "upload_check": upload_check,
                                                   "uploads": uploads[:20]}, indent=2), encoding="utf-8")
    print(json.dumps(upload_check, indent=2))


if __name__ == "__main__":
    main()
