"""Phase 0 library arms: call the official OmniVoice library the way omnivoice-demo does, and measure.

    .venv\\Scripts\\python.exe bench\\run_library.py --ref C:\\path\\voice_ref_pause_7s_test.wav [--cut-ref C:\\path\\voice_ref.wav]

Arms (all share the demo's model load and generation config; only the prompt differs):
  S-ASR     create_voice_clone_prompt(ref, ref_text=None)  -> Whisper transcript, exactly the demo with an empty
            Reference Text box. The prompt is created once and reused, like the planned service.
  S-ASRTXT  create_voice_clone_prompt(ref, ref_text=<S-ASR transcript>): the "ASR once, stored, re-encoded later"
            path of docs/TTS_SERVER.md 8.1. Expected to give the very same prompt for clips <= 20 s.
  S-MAN     only with --manual-transcript-file.
  R-CUT     --cut-ref (the reference whose end was cut), ASR: reproduces the first-word loss case.
Library arms use a fixed seed per (sentence, repetition), the same in every arm, so they compare in pairs.
Nothing is post-processed here beyond what the library itself does with the demo defaults.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from common import (CHECKPOINT, append_jsonl, environment, golden, keep_reference, new_run, seed_for,
                    write_wav_like_demo)
from audio_metrics import reference_qc


def demo_config():
    """The OmniVoiceGenerationConfig that omnivoice/cli/demo.py _gen_core builds from the UI defaults."""
    from omnivoice import OmniVoiceGenerationConfig
    return OmniVoiceGenerationConfig(num_step=32, guidance_scale=2.0, denoise=True,
                                     preprocess_prompt=True, postprocess_output=True)


def synced(torch, fn):
    torch.cuda.synchronize()
    started = time.perf_counter()
    out = fn()
    torch.cuda.synchronize()
    return out, time.perf_counter() - started


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--cut-ref", type=Path)
    ap.add_argument("--manual-transcript-file", type=Path)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--asrtxt-reps", type=int, default=1, help="S-ASRTXT is a check of prompt identity")
    ap.add_argument("--items", default="", help="comma separated ids; default: all")
    ap.add_argument("--skip-long", action="store_true")
    ap.add_argument("--run-name")
    args = ap.parse_args()

    run = new_run(args.run_name)
    out = run / "library.jsonl"
    items = [i for i in golden() if not args.items or i["id"] in args.items.split(",")]
    refs = {"main": keep_reference(run, "main", args.ref)}
    if args.cut_ref:
        refs["cut"] = keep_reference(run, "cut", args.cut_ref)
    qc = {}
    for label, info in refs.items():
        audio, sr = sf.read(run / info["copy"], dtype="int16")
        qc[label] = {**info, **reference_qc(audio, sr)}
    (run / "refs" / "qc.json").write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8")

    import torch
    from omnivoice import OmniVoice, VoiceClonePrompt
    from omnivoice.utils.common import fix_random_seed

    env = environment()
    started = time.perf_counter()
    model = OmniVoice.from_pretrained(CHECKPOINT, device_map="cuda:0", dtype=torch.float16)  # as the demo
    torch.cuda.synchronize()
    env["model_load_s"] = round(time.perf_counter() - started, 2)
    env["vram_after_load_gib"] = round(torch.cuda.memory_allocated() / 2**30, 3)
    env["sampling_rate"] = model.sampling_rate
    config = demo_config()
    env["generation_config"] = {k: getattr(config, k) for k in vars(config)}
    print(f"model loaded in {env['model_load_s']} s, sr={model.sampling_rate}", flush=True)

    _, asr_load_s = synced(torch, model.load_asr_model)
    env["asr_load_s"] = round(asr_load_s, 2)
    env["vram_after_asr_gib"] = round(torch.cuda.memory_allocated() / 2**30, 3)

    prompts_dir = run / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    first_call = [True]
    peak = [0.0]

    def make_prompt(arm: str, ref: Path, ref_text: str | None):
        prompt, seconds = synced(torch, lambda: model.create_voice_clone_prompt(ref_audio=str(ref), ref_text=ref_text))
        info = {"arm": arm, "ref": str(ref), "ref_text_given": ref_text, "ref_text_used": prompt.ref_text,
                "asr_used": ref_text is None, "prompt_s": round(seconds, 3), "ref_rms": prompt.ref_rms,
                "tokens_shape": list(prompt.ref_audio_tokens.shape),
                "tokens_seconds": round(prompt.ref_audio_tokens.shape[-1] * 960 / model.sampling_rate, 3)}
        path = prompts_dir / f"{arm}.pt"
        _, save_s = synced(torch, lambda: prompt.save(str(path)))
        info.update(save_s=round(save_s, 4), file=str(path.relative_to(run)), file_bytes=path.stat().st_size)
        (prompts_dir / f"{arm}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{arm}] prompt {seconds:.2f}s ref_text={prompt.ref_text!r}", flush=True)
        return prompt, info

    def generate(prompt, text: str, seed: int):
        fix_random_seed(seed)
        torch.cuda.reset_peak_memory_stats()
        audio, seconds = synced(torch, lambda: model.generate(text=text.strip(), language=None,
                                                              generation_config=config,
                                                              voice_clone_prompt=prompt)[0])
        stats = {"inference_s": round(seconds, 3), "audio_s": round(len(audio) / model.sampling_rate, 3),
                 "peak_alloc_gib": round(torch.cuda.max_memory_allocated() / 2**30, 3),
                 "peak_reserved_gib": round(torch.cuda.max_memory_reserved() / 2**30, 3),
                 "first_call": first_call[0]}
        stats["rtf"] = round(stats["inference_s"] / max(stats["audio_s"], 1e-6), 3)
        first_call[0] = False
        peak[0] = max(peak[0], stats["peak_alloc_gib"])
        return audio, stats

    def run_arm(arm: str, prompt, reps: int, include_long: bool, keep: dict | None = None):
        for item in items:
            if item["category"] == "long" and not include_long:
                continue
            for rep in range(1 if item["category"] == "long" else reps):
                seed = seed_for(item["id"], rep)
                audio, stats = generate(prompt, item["text"], seed)
                wav = run / arm / f"{item['id']}_r{rep}.wav"
                write_wav_like_demo(wav, audio, model.sampling_rate)
                if keep is not None:
                    keep[(item["id"], rep)] = audio
                append_jsonl(out, {"arm": arm, "item": item["id"], "category": item["category"], "rep": rep,
                                   "seed": seed, "wav": str(wav.relative_to(run)), **stats})
                print(f"[{arm}] {item['id']} r{rep} {stats['audio_s']}s audio in {stats['inference_s']}s "
                      f"(rtf {stats['rtf']})", flush=True)

    checks = {}
    asr_prompt, asr_info = make_prompt("S-ASR", run / refs["main"]["copy"], None)
    s_asr = {}
    run_arm("S-ASR", asr_prompt, args.reps, not args.skip_long, keep=s_asr)

    # The prompt file reloads to the same tensors and the same audio (what the service cache relies on).
    loaded, load_s = synced(torch, lambda: VoiceClonePrompt.load(str(run / asr_info["file"])))
    first = next(k for k in s_asr if k[1] == 0)
    again, _ = generate(asr_prompt, next(i for i in items if i["id"] == first[0])["text"], seed_for(*first))
    from_file, _ = generate(loaded, next(i for i in items if i["id"] == first[0])["text"], seed_for(*first))
    checks["prompt_load_s"] = round(load_s, 4)
    checks["prompt_file_tokens_equal"] = bool(torch.equal(loaded.ref_audio_tokens.cpu(), asr_prompt.ref_audio_tokens.cpu()))
    checks["prompt_file_text_equal"] = loaded.ref_text == asr_prompt.ref_text
    checks["same_seed_repeat_max_abs_diff"] = float(np.max(np.abs(again - s_asr[first]))) if again.shape == s_asr[first].shape else "length differs"
    checks["prompt_file_audio_max_abs_diff"] = float(np.max(np.abs(from_file - s_asr[first]))) if from_file.shape == s_asr[first].shape else "length differs"

    txt_prompt, _ = make_prompt("S-ASRTXT", run / refs["main"]["copy"], asr_prompt.ref_text)
    checks["asrtxt_tokens_equal"] = bool(torch.equal(txt_prompt.ref_audio_tokens.cpu(), asr_prompt.ref_audio_tokens.cpu()))
    checks["asrtxt_text_equal"] = txt_prompt.ref_text == asr_prompt.ref_text
    run_arm("S-ASRTXT", txt_prompt, args.asrtxt_reps, False)

    if args.manual_transcript_file:
        manual = args.manual_transcript_file.read_text(encoding="utf-8").strip()
        man_prompt, _ = make_prompt("S-MAN", run / refs["main"]["copy"], manual)
        run_arm("S-MAN", man_prompt, args.reps, not args.skip_long)
    if args.cut_ref:
        cut_prompt, _ = make_prompt("R-CUT", run / refs["cut"]["copy"], None)
        run_arm("R-CUT", cut_prompt, args.reps, False)

    env["generation_peak_alloc_gib"] = peak[0]
    (run / "library_env.json").write_text(json.dumps({**env, "checks": checks}, ensure_ascii=False, indent=2,
                                                     default=str), encoding="utf-8")
    print(json.dumps(checks, indent=2), flush=True)
    print(f"run folder: {run}", flush=True)


if __name__ == "__main__":
    main()
