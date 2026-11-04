"""Phase 0.5 audio set: one WAV per test sentence plus the Phase 0.7 sequences.

Source of speech, in order of preference:
  --from-dir DIR   use DIR/<id>.wav that a person recorded or licensed (no TTS call)
  --voice-id ID    synthesize through the local Sarae Voice Server (voice-server/, G-OmniVoice)

Every output is 16 kHz mono s16 PCM, the format both LiveTalking and MuseTalk feed to Whisper/mel.
Tempo changes use ffmpeg atempo (pitch kept) and are written to manifest.json, so both engines get
byte-identical audio. Do not run --voice-id during a LIVE: the TTS server shares the GPU with it.

    uv run python tools/prepare_audio.py --voice-id shop_main [--out outputs/phase0/audio]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import subprocess
import sys
import urllib.request
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
TESTSET = ROOT / "phase0" / "testsets" / "vi_sentences.json"
VOICE_ENV = ROOT / "voice-server" / ".env"
RATE = 16000


def token_from_env() -> str:
    for line in VOICE_ENV.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "SARAE_VOICE_ORCHESTRATOR_TOKENS":
            return value.strip().strip('"').split(",")[0]
    raise SystemExit(f"SARAE_VOICE_ORCHESTRATOR_TOKENS not found in {VOICE_ENV}")


def synthesize(base: str, token: str, voice_id: str, text: str, seed: int) -> bytes:
    body = json.dumps({"voice_id": voice_id, "text": text, "seed": seed}).encode("utf-8")
    req = urllib.request.Request(f"{base}/v1/tts/synthesize", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.read()


def to_pcm16k(src: Path, dst: Path, tempo: float | None) -> None:
    filters = [f"atempo={tempo}"] if tempo else []
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", str(RATE), "-sample_fmt", "s16"]
    if filters:
        cmd += ["-af", ",".join(filters)]
    subprocess.run(cmd + [str(dst)], check=True)


def read_pcm(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2, path
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def write_pcm(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.astype(np.int16).tobytes())


def silence(ms: int) -> np.ndarray:
    return np.zeros(int(RATE * ms / 1000), dtype=np.int16)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-dir", type=Path)
    src.add_argument("--voice-id")
    ap.add_argument("--server", default="http://127.0.0.1:8780")
    ap.add_argument("--out", type=Path, default=ROOT / "outputs" / "phase0" / "audio")
    ap.add_argument("--seed", type=int, default=20261103)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Vietnamese text on a cp1252 console
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found on PATH")

    spec = json.loads(TESTSET.read_text(encoding="utf-8"))
    raw_dir, sent_dir, seq_dir = args.out / "raw", args.out / "sentences", args.out / "sequences"
    for d in (raw_dir, sent_dir, seq_dir):
        d.mkdir(parents=True, exist_ok=True)
    token = token_from_env() if args.voice_id else ""
    manifest = {"source": f"voice-server:{args.voice_id}" if args.voice_id else f"dir:{args.from_dir}",
                "rate": RATE, "sentences": {}, "sequences": {}}
    pcm: dict[str, np.ndarray] = {}

    for s in spec["sentences"]:
        parts = s["text"].split("|")
        chunks = []
        for i, part in enumerate(parts):
            raw = raw_dir / f"{s['id']}_{i}.wav"
            if args.from_dir:
                given = args.from_dir / (f"{s['id']}.wav" if len(parts) == 1 else f"{s['id']}_{i}.wav")
                shutil.copyfile(given, raw)
            elif not raw.exists():
                raw.write_bytes(synthesize(args.server, token, args.voice_id, part.strip(), args.seed))
            conv = raw.with_suffix(".16k.wav")
            to_pcm16k(raw, conv, s.get("tempo"))
            chunks.append(read_pcm(conv))
            if i < len(parts) - 1:
                chunks.append(silence(s.get("pause_ms", 800)))
        pcm[s["id"]] = np.concatenate(chunks)
        out = sent_dir / f"{s['id']}.wav"
        write_pcm(out, pcm[s["id"]])
        manifest["sentences"][s["id"]] = {"text": s["text"], "tags": s["tags"], "tempo": s.get("tempo"),
                                          "seconds": round(len(pcm[s["id"]]) / RATE, 3),
                                          "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}
        print(f"{s['id']}  {manifest['sentences'][s['id']]['seconds']:6.2f} s  {s['text'][:60]}")

    def build(seq_id: str, items: list[str], gaps: list[int]) -> None:
        # Timeline of speech segments so the review can check mouth motion against silence.
        parts, segs, t = [silence(500)], [], 0.5
        for i, sid in enumerate(items):
            segs.append({"id": sid, "start": round(t, 3), "end": round(t + len(pcm[sid]) / RATE, 3)})
            parts.append(pcm[sid])
            t += len(pcm[sid]) / RATE
            if i < len(items) - 1:
                parts.append(silence(gaps[i]))
                t += gaps[i] / 1000
        parts.append(silence(1500))
        audio = np.concatenate(parts)
        write_pcm(seq_dir / f"{seq_id}.wav", audio)
        manifest["sequences"][seq_id] = {"seconds": round(len(audio) / RATE, 3), "speech": segs}
        print(f"{seq_id}  {len(audio) / RATE:8.2f} s  ({len(items)} items)")

    for q in spec["sequences"]:
        n = len(q["items"]) - 1
        gaps = q.get("gaps_ms") or [q.get("gap_ms", 300)] * n
        gaps = (gaps * (n // max(len(gaps), 1) + 1))[:n]
        build(q["id"], q["items"], gaps)

    end = spec["endurance"]
    rng = random.Random(end["seed"])
    ids = [s["id"] for s in spec["sentences"]]
    items, total = [], 0.0
    while total < end["target_s"]:
        batch = ids[:]
        rng.shuffle(batch)
        for sid in batch:
            items.append(sid)
            total += len(pcm[sid]) / RATE + 1.6
    build(end["id"], items, [rng.randint(300, 3000) for _ in items[1:]])

    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest: {args.out / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
