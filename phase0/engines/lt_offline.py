"""Run LiveTalking's realtime pipeline unchanged and capture it to MP4, with timing metrics.

LiveTalking only streams (WebRTC/RTMP/virtualcam). This adapter swaps the stream output for one
that behaves like the WebRTC track — a consumer pulls one frame every 40 ms of wall clock and the
render loop is throttled by its queue size (LiveTalking's own get_buffer_size back-pressure) — and
writes what it pulls to MP4. Speech is fed sentence by sentence at the times in the audio manifest,
like a TTS would, so the gaps between sentences are LiveTalking's real idle path (no inference,
original frames), not zeros pushed through the model.

Nothing in the LiveTalking checkout is modified; it is imported from --lt-dir.

Run with the LiveTalking venv:
    python phase0/engines/lt_offline.py --lt-dir F:/Coder/phase0_vendor/LiveTalking --model wav2lip
        --avatar-id p0_b_w2l --sequence q_main --out outputs/phase0/lt_w2l256_b_q_main
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
FRAME_S = 0.04


class FileOutput:
    """Paced consumer standing in for the WebRTC video track."""

    def __init__(self, out_video: Path, out_audio: Path, paced: bool = True):
        self.paced = paced
        self.q: queue.Queue = queue.Queue()
        self.out_video, self.out_audio = out_video, out_audio
        self.audio = open(out_audio, "wb")
        self.pushed = 0  # frames handed over by process_frames
        self.emitted = 0
        self.emit_times: list[float] = []
        self.events: list[tuple[int, dict]] = []  # (frame index, eventpoint) for audio start/end markers
        self.underruns = 0
        self.underrun_at: list[float] = []  # seconds since first frame
        self.stall_s = 0.0
        self.t0: float | None = None
        self.size = None
        self._ff = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._consume, daemon=True)

    # BaseOutput interface used by BaseAvatar.process_frames
    def start(self) -> None:
        self._thread.start()

    def push_video_frame(self, frame) -> None:
        if self._ff is None:
            h, w = frame.shape[:2]
            self.size = (w, h)
            self._ff = subprocess.Popen(
                ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", "25",
                 "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
                 str(self.out_video)], stdin=subprocess.PIPE)
        self.q.put(frame)
        self.pushed += 1

    def push_audio_frame(self, frame, eventpoint=None) -> None:
        self.audio.write(frame.astype(np.int16).tobytes())
        if eventpoint and eventpoint.get("status"):
            self.events.append((self.pushed - 1, dict(eventpoint)))

    def get_buffer_size(self) -> int:
        return self.q.qsize()

    def stop(self) -> None:
        pass  # finalized by close() once the render thread has drained

    def _consume(self) -> None:
        nxt = None
        while not (self._stop.is_set() and self.q.empty()):
            if not self.paced:
                try:
                    frame = self.q.get(timeout=0.1)
                except queue.Empty:
                    continue
                now = time.perf_counter()
                self.t0 = self.t0 or now
                self._ff.stdin.write(frame.tobytes())
                self.emit_times.append(now)
                self.emitted += 1
                continue
            if nxt is not None:
                delay = nxt - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
            try:
                frame = self.q.get_nowait()
            except queue.Empty:
                if nxt is not None and not self._stop.is_set():
                    self.underruns += 1
                    self.underrun_at.append(round(time.perf_counter() - self.t0, 2))
                waited = time.perf_counter()
                frame = None
                while frame is None and not self._stop.is_set():
                    try:
                        frame = self.q.get(timeout=0.1)
                    except queue.Empty:
                        pass
                if frame is None:
                    break
                if nxt is not None:
                    self.stall_s += time.perf_counter() - waited
                    nxt = time.perf_counter()  # a player re-anchors after a stall
            now = time.perf_counter()
            if self.t0 is None:
                self.t0 = now
                nxt = now
            self._ff.stdin.write(frame.tobytes())
            self.emit_times.append(now)
            self.emitted += 1
            nxt += FRAME_S

    def close(self) -> None:
        self._stop.set()
        self._thread.join()
        self.audio.close()
        if self._ff:
            self._ff.stdin.close()
            self._ff.wait()


class Sampler(threading.Thread):
    def __init__(self, interval=1.0):
        super().__init__(daemon=True)
        import psutil
        import pynvml
        pynvml.nvmlInit()
        self.nv, self.h = pynvml, pynvml.nvmlDeviceGetHandleByIndex(0)
        self.proc = psutil.Process()
        self.psutil = psutil
        self.proc.cpu_percent(None)
        psutil.cpu_percent(None)
        self.rows: list[dict] = []
        self.interval = interval
        self.stop_ev = threading.Event()

    def run(self):
        t0 = time.perf_counter()
        while not self.stop_ev.is_set():
            mem = self.nv.nvmlDeviceGetMemoryInfo(self.h)
            util = self.nv.nvmlDeviceGetUtilizationRates(self.h)
            self.rows.append({
                "t": round(time.perf_counter() - t0, 2),
                "vram_used_mb": round(mem.used / 2**20),
                "gpu_util": util.gpu,
                "cpu_total": self.psutil.cpu_percent(None),
                "proc_cpu": self.proc.cpu_percent(None),
                "proc_rss_mb": round(self.proc.memory_info().rss / 2**20),
            })
            self.stop_ev.wait(self.interval)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lt-dir", type=Path, required=True)
    ap.add_argument("--model", choices=["wav2lip", "musetalk"], required=True)
    ap.add_argument("--avatar-id", required=True)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--audio-dir", type=Path, default=ROOT / "outputs" / "phase0" / "audio")
    ap.add_argument("--sequence", default="q_main")
    ap.add_argument("--tail-s", type=float, default=2.0, help="idle time recorded after the last sentence")
    ap.add_argument("--out", type=Path, required=True, help="output path without extension")
    ap.add_argument("--offline", action="store_true",
                    help="quality render: unpaced output, sentences fed on the content clock (frames pushed), "
                         "so the video lines up with the audio timeline even when the engine is slower than "
                         "realtime. Timing metrics from this mode are not realtime results.")
    ap.add_argument("--whole", action="store_true",
                    help="feed the whole sequence WAV (silences included) as one stream, as MuseTalk's official "
                         "script does; the gaps then go through the model instead of LiveTalking's idle path, and "
                         "the render aligns to the audio with one constant offset")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args.out, args.audio_dir = args.out.resolve(), args.audio_dir.resolve()  # we chdir into LiveTalking below

    manifest = json.loads((args.audio_dir / "manifest.json").read_text(encoding="utf-8"))
    segs = manifest["sequences"][args.sequence]["speech"]
    if args.whole:
        segs = [{"id": args.sequence, "start": 0.0, "end": manifest["sequences"][args.sequence]["seconds"]}]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out_video, out_audio = args.out.with_suffix(".video.mp4"), args.out.with_suffix(".pcm")

    sampler = Sampler()
    sampler.start()
    baseline_vram = None

    lt = args.lt_dir.resolve()
    os.chdir(lt)
    sys.path.insert(0, str(lt))
    sys.argv = ["app.py", "--config", "config.yaml", "--model", args.model, "--avatar_id", args.avatar_id,
                "--batch_size", str(args.batch_size), "--transport", "phase0file", "--tts", "edgetts"]
    import importlib

    import torch
    import registry
    from config import parse_args

    opt = parse_args()
    opt.sessionid = "phase0"
    t_load = time.perf_counter()
    mod = importlib.import_module({"wav2lip": "avatars.wav2lip_avatar", "musetalk": "avatars.musetalk_avatar"}[args.model])
    time.sleep(1.1)
    baseline_vram = sampler.rows[-1]["vram_used_mb"] if sampler.rows else None
    if args.model == "wav2lip":
        model = mod.load_model("./models/wav2lip.pth")
        avatar = mod.load_avatar(args.avatar_id)
        mod.warm_up(opt.batch_size, model, 256)
    else:
        model = mod.load_model()
        avatar = mod.load_avatar(args.avatar_id)
        mod.warm_up(opt.batch_size, model)
    load_s = time.perf_counter() - t_load

    session = registry.create("avatar", args.model, opt=opt, model=model, avatar=avatar)
    output = FileOutput(out_video, out_audio, paced=not args.offline)
    session.output = output  # transport "phase0file" is unknown to LiveTalking, so it created none

    infer_times: list[float] = []
    orig = session.inference_batch

    def timed(index, feats):
        t = time.perf_counter()
        r = orig(index, feats)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        infer_times.append(time.perf_counter() - t)
        return r

    session.inference_batch = timed
    torch.cuda.reset_peak_memory_stats()

    quit_ev = threading.Event()
    render = threading.Thread(target=session.render, args=(quit_ev,), daemon=True)
    render.start()
    while output.t0 is None:
        time.sleep(0.01)
    t0 = output.t0

    feeds = []
    sent_dir = args.audio_dir / "sentences"
    for seg in segs:
        if args.offline:
            # Content clock: feed once the output has advanced to the sentence's start.
            while output.pushed * FRAME_S < seg["start"]:
                time.sleep(0.005)
        else:
            delay = t0 + seg["start"] - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        # Sentences repeat in long sequences, so every feed gets its own key.
        key = f"{len(feeds)}:{seg['id']}"
        feeds.append({"key": key, "id": seg["id"], "planned_s": seg["start"], "fed_s": time.perf_counter() - t0,
                      "fed_frame": output.pushed})
        wav = (args.audio_dir / "sequences" if args.whole else sent_dir) / f"{seg['id']}.wav"
        session.put_audio_filepath(str(wav), {"utt": key})
    end_at = t0 + segs[-1]["end"] + args.tail_s
    # Wait until the last sentence has actually been played out, then record the idle tail.
    while True:
        done = [e for _, e in output.events if e.get("status") == "end" and e.get("utt") == feeds[-1]["key"]]
        if done and (args.offline or time.perf_counter() > end_at):
            last_end_frame = max(i for i, e in output.events if e.get("status") == "end")
            if output.emitted >= last_end_frame + int(args.tail_s / FRAME_S):
                break
        if time.perf_counter() > end_at + 120:
            print("timeout waiting for playback", flush=True)
            break
        time.sleep(0.05)
    wall_s = time.perf_counter() - t0
    quit_ev.set()
    render.join(timeout=30)
    output.close()
    sampler.stop_ev.set()

    # Mux: audio is exactly 2 x 20 ms per video frame, pushed in frame order.
    final = args.out.with_suffix(".mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out_video), "-f", "s16le", "-ar", "16000", "-ac", "1",
                    "-i", str(out_audio), "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(final)],
                   check=True)
    out_video.unlink()
    out_audio.unlink()

    # Latency: feed time -> wall time the first frame of that sentence left the paced output.
    lat = []
    for f in feeds:
        idx = next((i for i, e in output.events if e.get("status") == "start" and e.get("utt") == f["key"]), None)
        if idx is not None and idx < len(output.emit_times):
            lat.append({**f, "first_frame": idx, "latency_s": round(output.emit_times[idx] - t0 - f["fed_s"], 3)})
    batch = args.batch_size
    infer = np.array(infer_times[1:]) if len(infer_times) > 1 else np.array(infer_times)
    rows = sampler.rows
    metrics = {
        "engine": f"LiveTalking/{args.model}",
        "mode": "offline-quality" if args.offline else "realtime-paced",
        "avatar_id": args.avatar_id,
        "sequence": args.sequence,
        "batch_size": batch,
        "model_and_avatar_load_s": round(load_s, 2),
        "frame_size": output.size,
        "frames_emitted": output.emitted,
        "wall_s": round(wall_s, 2),
        # Paced emission rate over the whole run (frames left in the queue at stop are emitted on time too).
        "output_fps_wall": round((output.emitted - 1) / (output.emit_times[-1] - output.emit_times[0]), 2)
        if output.emitted > 1 else None,
        "underruns": output.underruns,
        "underrun_at_s": output.underrun_at,
        "stall_s": round(output.stall_s, 3),
        "infer_batches": len(infer_times),
        "infer_fps_median": round(batch / float(np.median(infer)), 1) if len(infer) else None,
        "infer_fps_p05": round(batch / float(np.percentile(infer, 95)), 1) if len(infer) else None,
        "torch_peak_alloc_mb": round(torch.cuda.max_memory_allocated() / 2**20),
        "vram_baseline_mb": baseline_vram,
        "vram_peak_mb": max(r["vram_used_mb"] for r in rows),
        "gpu_util_mean": round(float(np.mean([r["gpu_util"] for r in rows])), 1),
        "gpu_util_max": max(r["gpu_util"] for r in rows),
        "cpu_total_mean": round(float(np.mean([r["cpu_total"] for r in rows])), 1),
        "proc_cpu_mean": round(float(np.mean([r["proc_cpu"] for r in rows])), 1),
        "proc_rss_peak_mb": max(r["proc_rss_mb"] for r in rows),
        "latency": lat,
        "latency_median_s": round(float(np.median([x["latency_s"] for x in lat])), 3) if lat else None,
        "output": str(final),
    }
    args.out.with_suffix(".metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    with args.out.with_suffix(".samples.csv").open("w", encoding="utf-8") as fh:
        fh.write(",".join(rows[0].keys()) + "\n")
        for r in rows:
            fh.write(",".join(str(v) for v in r.values()) + "\n")
    print(json.dumps({k: v for k, v in metrics.items() if k != "latency"}, ensure_ascii=False, indent=2), flush=True)
    os._exit(0)  # LiveTalking leaves non-daemon TTS/inference threads behind


if __name__ == "__main__":
    main()
