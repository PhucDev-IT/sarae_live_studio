"""Phase 0 recheck: is a word that Whisper "did not hear" really missing from the TTS audio, or an ASR error?

    .venv\\Scripts\\python.exe bench\\recheck_asr.py --run-name p0_20261102_2251

For every file the analysis flagged (head or tail word not matched), three independent signals:
  1. a second ASR (faster-whisper medium, CPU int8, beam 5) — a different model and decoder than large-v3-turbo;
  2. word timestamps: voiced audio before the first recognized word / after the last one (a dropped or garbled
     word leaves sound with no word on it; an ASR spelling error does not);
  3. similarity of the misheard edge word to the target with tones removed ("shop" vs "sốp" is a spelling, not a loss).
Verdict per edge: asr_error / tts_suspect / listen. Only `listen` and `tts_suspect` need a human ear; they are
copied to recheck\\ with the target text. Generated audio is only read.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import soundfile as sf

from audio_metrics import align, audio_stats, word_report, words
from common import RUNS, golden

GAP_S = 0.35        # voiced audio longer than this with no recognized word on it is suspicious
SIMILAR = 0.5       # tone-free similarity above this = same word misspelled by the ASR


def plain(word: str) -> str:
    s = unicodedata.normalize("NFD", word)
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "d").lower()


def edge_similarity(target: str, hyp: str, head: bool, n: int = 2) -> float:
    """Lowest similarity among the first/last n target words and the hypothesis words aligned to them."""
    ref, hw = words(target), words(hyp)
    pairs = {r: h for op, r, h in align(ref, hw) if r is not None}
    idx = range(min(n, len(ref))) if head else range(max(0, len(ref) - n), len(ref))
    scores = [SequenceMatcher(None, plain(ref[k]), plain(hw[pairs[k]])).ratio() if pairs.get(k) is not None else 0.0
              for k in idx]
    return round(min(scores), 2) if scores else 1.0


def verdict(miss_a: int, miss_b: int, deleted_a: int, deleted_b: int, sim: float, gap: float | None) -> str:
    if deleted_a and deleted_b:
        return "tts_suspect"
    if gap is not None and gap > GAP_S and min(miss_a, miss_b) > 0:
        return "tts_suspect"
    if not miss_a or not miss_b:
        return "asr_error"            # one of two independent ASRs heard the target word
    if sim >= SIMILAR and (gap is None or gap <= GAP_S):
        return "asr_error"            # both misspelled it, but the sound is there and close to the word
    return "listen"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--arms", default="S-ASR,S-ASRTXT,D-ASR,R-CUT")
    args = ap.parse_args()
    run = RUNS / args.run_name
    texts = {i["id"]: i["text"] for i in golden()}
    rows = [r for r in csv.DictReader((run / "metrics.csv").open(encoding="utf-8-sig"))
            if r["arm"] in args.arms.split(",") and (int(r["head_missing"]) or int(r["tail_missing"]))]
    import torch
    import torchaudio
    from faster_whisper import WhisperModel
    fw = WhisperModel("medium", device="cpu", compute_type="int8")

    out, recheck = [], run / "recheck"
    if recheck.exists():
        shutil.rmtree(recheck)
    recheck.mkdir()
    for r in rows:
        wav = run / r["wav"]
        audio, sr = sf.read(wav, dtype="int16")
        stats = audio_stats(audio, sr)
        onset = stats["leading_silence_ms"] / 1000
        speech_end = stats["duration_s"] - stats["trailing_silence_ms"] / 1000
        # faster-whisper takes 16 kHz float32; resampled in memory (the installed PyAV cannot decode for it).
        pcm = torchaudio.functional.resample(torch.from_numpy(audio.astype("float32") / 32768.0), sr, 16000).numpy()
        segments, _ = fw.transcribe(pcm, language="vi", beam_size=5, word_timestamps=True, vad_filter=False,
                                    condition_on_previous_text=False)
        ws = [w for s in segments for w in (s.words or [])]
        fw_text = " ".join(w.word.strip() for w in ws)
        head_gap = round(ws[0].start - onset, 2) if ws else None
        tail_gap = round(speech_end - ws[-1].end, 2) if ws else None
        target = texts[r["item"]]
        a, b = word_report(target, r["asr_text"]), word_report(target, fw_text)
        head = verdict(a["head_missing"], b["head_missing"], a["head_deleted"], b["head_deleted"],
                       max(edge_similarity(target, r["asr_text"], True), edge_similarity(target, fw_text, True)),
                       head_gap) if a["head_missing"] else "ok"
        tail_del_a = a["tail_missing"] and a["hyp_words"] < a["ref_words"]
        tail_del_b = b["tail_missing"] and b["hyp_words"] < b["ref_words"]
        tail = verdict(a["tail_missing"], b["tail_missing"], tail_del_a, tail_del_b,
                       max(edge_similarity(target, r["asr_text"], False), edge_similarity(target, fw_text, False)),
                       tail_gap) if a["tail_missing"] else "ok"
        if a["hyp_words"] > 2 * a["ref_words"] and b["hyp_words"] <= 1.5 * b["ref_words"]:
            head, tail = "asr_hallucination", "asr_hallucination"
        row = {"arm": r["arm"], "item": r["item"], "rep": r["rep"], "wav": r["wav"], "target": target,
               "turbo": r["asr_text"][:200], "medium": fw_text[:200], "head": head, "tail": tail,
               "head_gap_s": head_gap, "tail_gap_s": tail_gap, "wer_turbo": a["wer"], "wer_medium": b["wer"]}
        out.append(row)
        if {head, tail} & {"listen", "tts_suspect"}:
            shutil.copy2(wav, recheck / f"{r['arm']}_{r['item']}_r{r['rep']}.wav")
        print(f"{r['arm']} {r['item']} r{r['rep']}: head={head} tail={tail} gap={head_gap}/{tail_gap} | {fw_text}",
              flush=True)

    with (recheck / "recheck.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["arm"])
        writer.writeheader()
        writer.writerows(out)
    counts = {}
    for row in out:
        for edge in ("head", "tail"):
            key = (row["arm"], edge, row[edge])
            counts[key] = counts.get(key, 0) + 1
    lines = ["# ASR recheck", "", "| Arm | Edge | Verdict | Files |", "|---|---|---|---|"]
    lines += [f"| {a} | {e} | {v} | {c} |" for (a, e, v), c in sorted(counts.items()) if v != "ok"]
    (recheck / "recheck.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(json.dumps({"flagged_files": len(out)}))


if __name__ == "__main__":
    main()
