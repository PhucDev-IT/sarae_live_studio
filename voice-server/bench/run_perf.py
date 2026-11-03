"""Phase 0 performance benchmark: ONE G-OmniVoice process on the GPU, same model/config/WAV as the quality baseline.

    .venv\\Scripts\\python.exe bench\\run_perf.py --ref C:\\path\\voice_ref_pause_7s_test.wav --run-name <run>

Measures, without changing anything the quality baseline verified (checkpoint, demo generation config,
reference WAV, ASR transcript path, library post-processing):
  startup   N fresh subprocesses: import, from_pretrained (checkpoint already cached), first generation
  prompt    Whisper load; prompt from WAV with ASR; from the stored transcript; .pt load
  buckets   latency, RTF and VRAM per text length (perf_texts.json)
  soak      many consecutive requests with ONE VoiceClonePrompt: memory trend, latency drift, and a fixed-seed probe
            generated at the start and at the end (identical output = no state carried between requests)
  encode    int16 + WAV packing in memory (what an API response adds; there is no endpoint yet)
Refuses to start when another process already holds GPU memory, unless --allow-busy-gpu.
"""
from __future__ import annotations

import argparse
import gc
import io
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from common import (BENCH, CHECKPOINT, append_jsonl, environment, keep_reference, new_run, read_jsonl,
                    write_wav_like_demo)
from run_library import demo_config, synced

BUSY_MIB = 1500   # desktop compositing alone stays well below this on the RTX 3060


def gpu_snapshot() -> dict:
    fields = "memory.used,memory.total,utilization.gpu,temperature.gpu,pstate,clocks.sm,power.draw"
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return dict(zip(fields.split(","), [v.strip() for v in out.split(",")]))
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": repr(exc)}


def pct(values, q):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 3)


def describe(values) -> dict:
    values = [v for v in values if isinstance(v, (int, float))]
    if not values:
        return {}
    return {"n": len(values), "median": pct(values, 0.5), "p95": pct(values, 0.95),
            "min": round(min(values), 3), "max": round(max(values), 3)}


def startup_probe(ref: Path) -> None:
    """Runs in a fresh subprocess: what a service restart costs with the checkpoint already in the HF cache."""
    t0 = time.perf_counter()
    import torch
    from omnivoice import OmniVoice
    t_import = time.perf_counter() - t0
    model, t_load = synced(torch, lambda: OmniVoice.from_pretrained(CHECKPOINT, device_map="cuda:0",
                                                                    dtype=torch.float16))
    prompt = model.create_voice_clone_prompt(ref_audio=str(ref), ref_text=json.loads(
        (ref.parent / "transcript.json").read_text(encoding="utf-8"))["ref_text"])
    _, t_first = synced(torch, lambda: model.generate(text="Dạ có size Mờ nha chị.", language=None,
                                                      generation_config=demo_config(), voice_clone_prompt=prompt))
    _, t_second = synced(torch, lambda: model.generate(text="Dạ có size Mờ nha chị.", language=None,
                                                       generation_config=demo_config(), voice_clone_prompt=prompt))
    print(json.dumps({"import_s": round(t_import, 3), "load_s": round(t_load, 3), "first_gen_s": round(t_first, 3),
                      "second_gen_s": round(t_second, 3),
                      "vram_alloc_gib": round(torch.cuda.memory_allocated() / 2**30, 3)}))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--run-name")
    ap.add_argument("--startup-runs", type=int, default=3)
    ap.add_argument("--soak", type=int, default=None, help="override soak request count")
    ap.add_argument("--buckets", default="", help="comma separated; default all")
    ap.add_argument("--allow-busy-gpu", action="store_true")
    ap.add_argument("--startup-probe", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.startup_probe:
        return startup_probe(args.startup_probe)

    before = gpu_snapshot()
    used = float(before.get("memory.used") or 0)
    if used > BUSY_MIB and not args.allow_busy_gpu:
        sys.exit(f"GPU already uses {used:.0f} MiB (> {BUSY_MIB}). Close other CUDA processes "
                 "(omnivoice demos on 7862/7863, OBS, ...) or pass --allow-busy-gpu.")

    spec = json.loads((BENCH / "perf_texts.json").read_text(encoding="utf-8"))
    run = new_run(args.run_name)
    rows_path = run / "perf.jsonl"
    ref_info = keep_reference(run, "main", args.ref)
    ref = run / ref_info["copy"]
    report = {"gpu_before": before, "ref": ref_info}

    import torch
    import psutil
    from omnivoice import OmniVoice, VoiceClonePrompt
    from omnivoice.utils.common import fix_random_seed

    proc = psutil.Process()
    model, load_s = synced(torch, lambda: OmniVoice.from_pretrained(CHECKPOINT, device_map="cuda:0",
                                                                    dtype=torch.float16))
    config = demo_config()
    report["env"] = {**environment(), "model_load_s_in_bench": round(load_s, 3),
                     "vram_after_load_gib": round(torch.cuda.memory_allocated() / 2**30, 3)}

    # --- prompt preparation (same WAV, same ASR path as the verified baseline) -------------------------------
    prompt_rows = {}
    _, prompt_rows["asr_load_s"] = synced(torch, model.load_asr_model)
    prompt_rows["vram_with_asr_gib"] = round(torch.cuda.memory_allocated() / 2**30, 3)
    asr_times, prompt = [], None
    for _ in range(5):
        prompt, s = synced(torch, lambda: model.create_voice_clone_prompt(ref_audio=str(ref), ref_text=None))
        asr_times.append(s)
    (ref.parent / "transcript.json").write_text(json.dumps({"ref_text": prompt.ref_text}, ensure_ascii=False),
                                                encoding="utf-8")
    # The service runs Whisper only when a voice is registered; release it like the service would.
    model._asr_pipe = None
    gc.collect()
    torch.cuda.empty_cache()
    prompt_rows["vram_after_asr_release_gib"] = round(torch.cuda.memory_allocated() / 2**30, 3)
    txt_times = [synced(torch, lambda: model.create_voice_clone_prompt(ref_audio=str(ref), ref_text=prompt.ref_text))[1]
                 for _ in range(10)]
    pt = run / "prompt.pt"
    prompt.save(str(pt))
    load_times = [synced(torch, lambda: VoiceClonePrompt.load(str(pt)))[1] for _ in range(20)]
    prompt_rows.update(ref_text=prompt.ref_text, with_asr_s=describe(asr_times), from_transcript_s=describe(txt_times),
                       pt_load_s=describe(load_times), pt_bytes=pt.stat().st_size)
    report["prompt"] = prompt_rows
    print("prompt:", json.dumps(prompt_rows, ensure_ascii=False), flush=True)

    def generate(text: str, seed: int):
        fix_random_seed(seed)
        torch.cuda.reset_peak_memory_stats()
        audio, s = synced(torch, lambda: model.generate(text=text.strip(), language=None, generation_config=config,
                                                        voice_clone_prompt=prompt)[0])
        started = time.perf_counter()
        buf = io.BytesIO()
        sf.write(buf, (audio * 32767).astype(np.int16), model.sampling_rate, format="WAV", subtype="PCM_16")
        encode_s = time.perf_counter() - started
        audio_s = len(audio) / model.sampling_rate
        return audio, {"inference_s": round(s, 3), "audio_s": round(audio_s, 3), "rtf": round(s / audio_s, 3),
                       "encode_ms": round(encode_s * 1000, 2), "wav_bytes": buf.getbuffer().nbytes,
                       "peak_alloc_gib": round(torch.cuda.max_memory_allocated() / 2**30, 3),
                       "peak_reserved_gib": round(torch.cuda.max_memory_reserved() / 2**30, 3),
                       "alloc_after_gib": round(torch.cuda.memory_allocated() / 2**30, 4),
                       "rss_gib": round(proc.memory_info().rss / 2**30, 3)}

    generate("Dạ có size Mờ nha chị.", 0)   # warm-up (CUDA kernels, allocator), not reported

    # --- length buckets ---------------------------------------------------------------------------------------
    wanted = [b for b in spec["buckets"] if not args.buckets or b in args.buckets.split(",")]
    for bucket in wanted:
        b = spec["buckets"][bucket]
        for t, text in enumerate(b["texts"]):
            for rep in range(b["reps"]):
                audio, stats = generate(text, 1000 * t + rep)
                wav = run / "buckets" / bucket / f"t{t}_r{rep}.wav"
                write_wav_like_demo(wav, audio, model.sampling_rate)
                append_jsonl(rows_path, {"phase": "bucket", "bucket": bucket, "text": t, "rep": rep,
                                         "chars": len(text), "words": len(text.split()),
                                         "wav": str(wav.relative_to(run)), **stats})
                print(f"[{bucket}] t{t} r{rep} {len(text)} chars → {stats['audio_s']}s audio in "
                      f"{stats['inference_s']}s (rtf {stats['rtf']})", flush=True)

    # --- soak: many requests on ONE prompt ----------------------------------------------------------------------
    soak = spec["soak"]
    n = args.soak if args.soak is not None else soak["requests"]
    probe_start, _ = generate(soak["probe_text"], 424242)
    pool = [t for name in soak["buckets"] for t in spec["buckets"][name]["texts"]]
    for i in range(n):
        text = pool[i % len(pool)]
        audio, stats = generate(text, 5000 + i)
        row = {"phase": "soak", "i": i, "chars": len(text), **stats}
        if i % 20 == 0:
            wav = run / "soak" / f"i{i:03d}.wav"
            write_wav_like_demo(wav, audio, model.sampling_rate)
            row["wav"] = str(wav.relative_to(run))
        append_jsonl(rows_path, row)
        if i % 20 == 0:
            print(f"[soak] {i}/{n} {stats['inference_s']}s alloc {stats['alloc_after_gib']} GiB rss {stats['rss_gib']}",
                  flush=True)
    probe_end, _ = generate(soak["probe_text"], 424242)
    write_wav_like_demo(run / "soak" / "probe_start.wav", probe_start, model.sampling_rate)
    write_wav_like_demo(run / "soak" / "probe_end.wav", probe_end, model.sampling_rate)
    report["soak_probe"] = {"same_length": probe_start.shape == probe_end.shape,
                            "max_abs_diff": float(np.max(np.abs(probe_start - probe_end)))
                            if probe_start.shape == probe_end.shape else None}
    report["gpu_after_main"] = gpu_snapshot()

    # --- startup in fresh processes (main model released first so each probe has the GPU alone) ----------------
    del model, prompt
    gc.collect()
    torch.cuda.empty_cache()
    probes = []
    for _ in range(args.startup_runs):
        started = time.perf_counter()
        out = subprocess.run([sys.executable, __file__, "--ref", str(ref), "--startup-probe", str(ref)],
                             capture_output=True, text=True, encoding="utf-8", cwd=str(BENCH))
        line = next((l for l in out.stdout.splitlines()[::-1] if l.startswith("{")), None)
        probes.append({**(json.loads(line) if line else {"error": out.stderr[-500:]}),
                       "process_wall_s": round(time.perf_counter() - started, 3)})
        print("startup:", probes[-1], flush=True)
    report["startup"] = probes

    # --- summary ------------------------------------------------------------------------------------------------
    rows = read_jsonl(rows_path)
    summary = {}
    for bucket in wanted:
        rs = [r for r in rows if r.get("bucket") == bucket]
        summary[bucket] = {"chars": describe([r["chars"] for r in rs]), "audio_s": describe([r["audio_s"] for r in rs]),
                           "inference_s": describe([r["inference_s"] for r in rs]), "rtf": describe([r["rtf"] for r in rs]),
                           "peak_alloc_gib": max(r["peak_alloc_gib"] for r in rs),
                           "peak_reserved_gib": max(r["peak_reserved_gib"] for r in rs),
                           "encode_ms": describe([r["encode_ms"] for r in rs])}
    soak_rows = [r for r in rows if r["phase"] == "soak"]
    if soak_rows:
        k = max(1, len(soak_rows) // 10)
        first, last = soak_rows[:k], soak_rows[-k:]
        summary["soak"] = {
            "requests": len(soak_rows), "inference_s": describe([r["inference_s"] for r in soak_rows]),
            "rtf": describe([r["rtf"] for r in soak_rows]),
            "rtf_first_10pct_median": pct([r["rtf"] for r in first], 0.5),
            "rtf_last_10pct_median": pct([r["rtf"] for r in last], 0.5),
            "alloc_after_first_gib": soak_rows[0]["alloc_after_gib"], "alloc_after_last_gib": soak_rows[-1]["alloc_after_gib"],
            "alloc_after_max_gib": max(r["alloc_after_gib"] for r in soak_rows),
            "peak_reserved_max_gib": max(r["peak_reserved_gib"] for r in soak_rows),
            "rss_first_gib": soak_rows[0]["rss_gib"], "rss_last_gib": soak_rows[-1]["rss_gib"],
            "probe": report["soak_probe"]}
    summary["startup"] = {k: describe([p.get(k) for p in probes])
                          for k in ("import_s", "load_s", "first_gen_s", "second_gen_s", "process_wall_s")}
    report["summary"] = summary
    report["gpu_end"] = gpu_snapshot()
    (run / "perf_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"run folder: {run}")


if __name__ == "__main__":
    main()
