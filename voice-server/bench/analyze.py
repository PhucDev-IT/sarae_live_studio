"""Phase 0 analysis: levels, Whisper word check (head/tail loss), timings, identity checks and a blind listening sheet.

    .venv\\Scripts\\python.exe bench\\analyze.py --run-name <run>

Writes into the run folder: asr.jsonl (cached transcriptions), metrics.csv, summary.md, listening\\ (randomly
named copies + key.json + sheet.csv). The generated WAVs are only read, never changed.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf

from audio_metrics import audio_stats, word_report
from common import RUNS, append_jsonl, golden, read_jsonl

ASR_MODEL = "openai/whisper-large-v3-turbo"


def transcriber():
    import torch
    from transformers import pipeline
    pipe = pipeline("automatic-speech-recognition", model=ASR_MODEL, dtype=torch.float16, device="cuda:0")

    def run(path: Path) -> str:
        audio, sr = sf.read(path, dtype="float32")
        long = len(audio) / sr > 30
        result = pipe({"raw": audio, "sampling_rate": sr}, return_timestamps=long,
                      generate_kwargs={"language": "vi", "task": "transcribe"})
        return result["text"].strip()
    return run


def mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.fmean(values), 3) if values else None


def rate(values):
    values = list(values)
    return f"{sum(values)}/{len(values)}" if values else "-"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    run = RUNS / args.run_name
    texts = {i["id"]: i for i in golden()}
    rows = read_jsonl(run / "library.jsonl") + read_jsonl(run / "demo.jsonl")
    rows = [r for r in rows if r.get("wav")]

    asr_path = run / "asr.jsonl"
    cached = {r["wav"]: r["text"] for r in read_jsonl(asr_path)}
    todo = [r for r in rows if r["wav"] not in cached]
    if todo:
        transcribe = transcriber()
        for r in todo:
            cached[r["wav"]] = transcribe(run / r["wav"])
            append_jsonl(asr_path, {"wav": r["wav"], "text": cached[r["wav"]]})
            print(f"asr {r['wav']}: {cached[r['wav']]}", flush=True)

    metrics = []
    for r in rows:
        audio, sr = sf.read(run / r["wav"], dtype="int16")
        stats = audio_stats(audio, sr)
        words = word_report(texts[r["item"]]["text"], cached[r["wav"]])
        voiced = stats["duration_s"] - (stats["leading_silence_ms"] + stats["trailing_silence_ms"]) / 1000
        metrics.append({**r, **stats, **words, "sample_rate": sr, "asr_text": cached[r["wav"]],
                        "words_per_voiced_s": round(words["ref_words"] / voiced, 2) if voiced > 0 else None})
    fields = list(dict.fromkeys(k for m in metrics for k in m))
    with (run / "metrics.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(metrics)

    by_arm = defaultdict(list)
    for m in metrics:
        by_arm[m["arm"]].append(m)
    lines = [f"# Phase 0 summary — run `{args.run_name}`", ""]
    lib_env = run / "library_env.json"
    if lib_env.is_file():
        env = json.loads(lib_env.read_text(encoding="utf-8"))
        lines += ["## Environment and one-off timings", "", "```json",
                  json.dumps({k: env.get(k) for k in ("python", "packages", "checkpoint", "checkpoint_revision",
                                                      "asr_revision", "gpu", "cuda", "vram_free_gib_at_start",
                                                      "model_load_s", "vram_after_load_gib", "asr_load_s",
                                                      "vram_after_asr_gib", "generation_peak_alloc_gib",
                                                      "sampling_rate", "checks")},
                             ensure_ascii=False, indent=2, default=str), "```", ""]
        for prompt in sorted((run / "prompts").glob("*.json")):
            p = json.loads(prompt.read_text(encoding="utf-8"))
            lines.append(f"- prompt `{p['arm']}`: {p['prompt_s']} s (ASR={p['asr_used']}), "
                         f"tokens {p['tokens_shape']} ≈ {p['tokens_seconds']} s, file {p['file_bytes']} B, "
                         f"ref_text: “{p['ref_text_used']}”")
        lines.append("")
    demo_env = run / "demo_env.json"
    if demo_env.is_file():
        lines += ["Demo upload check: `" + json.dumps(json.loads(demo_env.read_text())["upload_check"]) + "`", ""]

    lines += ["## Per arm (all short items; long reported separately)", "",
              "| Arm | n | audio s | infer s (lib) / wall s (demo) | RTF warm | WER | head missing | head deleted | "
              "tail missing | lead sil ms | trail sil ms | last 100 ms dB | words/voiced s | clipped |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm, ms in sorted(by_arm.items()):
        short = [m for m in ms if m["category"] != "long"]
        warm = [m for m in short if not m.get("first_call")]
        lines.append("| " + " | ".join(str(x) for x in (
            arm, len(short), mean(m["audio_s"] if "audio_s" in m else m["duration_s"] for m in short),
            mean(m.get("inference_s", m.get("wall_s")) for m in short), mean(m.get("rtf") for m in warm),
            mean(m["wer"] for m in short), rate(m["head_missing"] > 0 for m in short),
            rate(m["head_deleted"] > 0 for m in short), rate(m["tail_missing"] > 0 for m in short),
            mean(m["leading_silence_ms"] for m in short), mean(m["trailing_silence_ms"] for m in short),
            mean(m["last_100ms_db"] for m in short), mean(m["words_per_voiced_s"] for m in short),
            sum(m["clipped_samples"] > 0 for m in short))) + " |")
    lines += ["", "## Per category (mean WER · head missing · duration s)", "",
              "| Category | " + " | ".join(sorted(by_arm)) + " |", "|---|" + "---|" * len(by_arm)]
    for cat in dict.fromkeys(i["category"] for i in texts.values()):
        cells = []
        for arm in sorted(by_arm):
            ms = [m for m in by_arm[arm] if m["category"] == cat]
            cells.append(f"{mean(m['wer'] for m in ms)} · {rate(m['head_missing'] > 0 for m in ms)} · "
                         f"{mean(m['duration_s'] for m in ms)}" if ms else "-")
        lines.append(f"| {cat} | " + " | ".join(cells) + " |")

    # Same seed, same prompt: is S-ASRTXT sample-identical to S-ASR?
    pairs = []
    lib = {(m["arm"], m["item"], m["rep"]): m for m in metrics}
    for (arm, item, rep), m in lib.items():
        if arm == "S-ASRTXT" and ("S-ASR", item, rep) in lib:
            a, _ = sf.read(run / m["wav"], dtype="int16")
            b, _ = sf.read(run / lib[("S-ASR", item, rep)]["wav"], dtype="int16")
            pairs.append(int(np.max(np.abs(a.astype(int) - b.astype(int)))) if a.shape == b.shape else "len")
    if pairs:
        lines += ["", f"S-ASRTXT vs S-ASR (same seed) max |Δ| per file (int16): {pairs}"]

    lines += ["", "## Head/tail misses heard by Whisper (check by ear: ASR itself can be wrong)", ""]
    for m in metrics:
        if m["head_missing"] or m["tail_missing"]:
            lines.append(f"- {m['arm']} {m['item']} r{m['rep']}: head {m['head_missing']}, tail {m['tail_missing']} "
                         f"— “{m['asr_text']}”")
    (run / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Blind listening sheet: rep 0 of every arm per item, random names, key kept apart.
    listen = run / "listening"
    if listen.exists():
        shutil.rmtree(listen)
    listen.mkdir()
    rng = random.Random(args.seed)
    picks = [m for m in metrics if m["rep"] == 0]
    rng.shuffle(picks)
    key = {}
    with (listen / "sheet.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["file", "item", "text", "score_1_5", "first_words_ok", "ending_ok", "notes"])
        for n, m in enumerate(sorted(picks, key=lambda m: m["item"]), 1):
            name = f"{m['item']}_{n:03d}{Path(m['wav']).suffix}"
            shutil.copy2(run / m["wav"], listen / name)
            key[name] = {"arm": m["arm"], "wav": m["wav"]}
            writer.writerow([name, m["item"], texts[m["item"]]["text"], "", "", "", ""])
    (listen / "key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    print((run / "summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
