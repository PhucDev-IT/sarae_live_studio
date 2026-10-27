"""Offline source/encoded-output checks for the owned NativeAudioBridge run.

Requires numpy and ffmpeg. No device access. Never treats harness output as
evidence that another application's microphone input was replaced.
"""

import json
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".tools/native-bridge"
RATE = 12000


def decode(path):
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-map", "0:a:0", "-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(RATE),
         "-ac", "1", "pipe:1"], capture_output=True, timeout=35, check=True,
    )
    return np.frombuffer(result.stdout, dtype="<i2").astype(np.float64) / 32768


def raw(path):
    samples = np.fromfile(path, dtype="<i2").reshape(-1, 2)[:, 0]
    return samples[::4].astype(np.float64) / 32768


def rms(samples):
    return float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0


def match(query, reference):
    """Normalized best lag of a short encoded-output window in a source."""
    if len(query) >= len(reference) or rms(query) < 0.001:
        return {"correlation": 0.0, "sourceOffsetSeconds": None}
    n = 1 << (len(query) + len(reference) - 2).bit_length()
    convolution = np.fft.irfft(
        np.fft.rfft(reference, n) * np.fft.rfft(query[::-1], n), n,
    )[len(query)-1:len(reference)]
    squares = np.r_[0.0, np.cumsum(reference * reference)]
    energy = squares[len(query):] - squares[:-len(query)]
    denominator = np.sqrt(np.maximum(energy * np.sum(query * query), 1e-12))
    scores = convolution / denominator
    index = int(np.argmax(np.abs(scores)))
    return {"correlation": round(float(scores[index]), 4),
            "sourceOffsetSeconds": round(index/RATE, 3)}


def window(samples, at, duration=0.75):
    return samples[round(at*RATE):round((at+duration)*RATE)]


def silence_runs(samples, threshold=0.0005):
    chunks = [rms(samples[i:i+RATE//100]) for i in range(0, len(samples), RATE//100)]
    runs, beginning = [], None
    for index, value in enumerate(chunks + [1.0]):
        if value < threshold and beginning is None:
            beginning = index
        if value >= threshold and beginning is not None:
            runs.append({"startSeconds": round(beginning/100, 2),
                         "durationSeconds": round((index-beginning)/100, 2)})
            beginning = None
    return sorted(runs, key=lambda row: row["durationSeconds"], reverse=True)[:8]


def events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def metrics(path, source_a, source_b, positions):
    encoded = decode(path)
    rows = []
    for at in positions:
        part = window(encoded, at)
        a, b = match(part, source_a), match(part, source_b)
        rows.append({"outputSeconds": at, "rms": round(rms(part), 5),
                     "A": a, "B": b,
                     "best": "A" if abs(a["correlation"]) > abs(b["correlation"]) else "B"})
    return {"durationSeconds": round(len(encoded)/RATE, 3), "rms": round(rms(encoded), 5),
            "longestNearSilentRuns": silence_runs(encoded), "windows": rows}


def main():
    a, b = decode(OUT/"source.mp4"), decode(OUT/"sourceB.mp4")
    modes = OUT/"bridge-modes/bridge-modes"
    aba = OUT/"bridge-aba/bridge-aba"
    long = OUT/"bridge-aba-long/bridge-aba-long"
    mic_events, aba_events = events(modes/"events.jsonl"), events(aba/"events.jsonl")
    long_events = events(long/"events.jsonl")
    start = next(x["emissionNs"] for x in aba_events if x["event"] == "record_state" and x["state"] == "RECORDING")
    commits = [{"source": x["source"], "generation": x["generation"],
                "secondsAfterRecordStart": round((x["commitNs"]-start)/1e9, 3)}
               for x in aba_events if x["event"] == "bridge_source_commit"]
    mode_records = {x["mode"]: x for x in mic_events if x["event"] == "record_complete"}
    aba_record = next(x for x in aba_events if x["event"] == "record_complete")
    mic_original = (modes/"MICROPHONE-mic.s16").read_bytes()
    mic_delivered = (modes/"MICROPHONE-delivered.s16").read_bytes()
    muted_delivered = np.fromfile(modes/"MUTED-delivered.s16", dtype="<i2")
    muted_encoded = decode(modes/"MUTED.mp4")
    report = {
        "scope": "Sarae-owned OpenSL recorder only; no TikTok attachment",
        "sourceASeconds": round(len(a)/RATE, 3), "sourceBSeconds": round(len(b)/RATE, 3),
        "sourceComparison": match(window(a, 0.8, 0.75), b),
        "microphone": {"rawExactPassThrough": mic_original == mic_delivered,
                       "encodedRms": round(rms(decode(modes/"MICROPHONE.mp4")), 5),
                       "bridge": mode_records["MICROPHONE"]["bridge"]},
        "muted": {"rawNonzeroSamples": int(np.count_nonzero(muted_delivered)),
                  "encodedRms": round(rms(muted_encoded), 6),
                  "bridge": mode_records["MUTED"]["bridge"]},
        "video": metrics(modes/"VIDEO.mp4", a, b, [1.0, 2.5, 5.0, 7.5]),
        "videoBridge": mode_records["VIDEO"]["bridge"],
        "aba": metrics(aba/"VIDEO_ABA.mp4", a, b,
                       [0.8, 2.0, 3.0, 4.7, 5.7, 6.8, 8.8, 9.8, 10.7]),
        "abaCommits": commits, "abaBridge": aba_record["bridge"],
        "abaMissingPcmFrames": sum(x.get("missingFrames", 0) for x in aba_events if x["event"] == "feeder_block"),
        "abaMaxCallbackWorkUs": round(max(x.get("callbackWorkNs", 0) for x in aba_events)/1000, 2),
        "abaEnqueueFailures": aba_record["stats"][3],
        "abaLong": metrics(long/"VIDEO_ABA.mp4", decode(OUT/"source-long.mp4"),
                           decode(OUT/"sourceB-long.mp4"),
                           [0.8, 2.0, 3.0, 4.7, 5.7, 6.8, 8.8, 9.8, 10.7]),
        "abaLongMissingPcmFrames": sum(x.get("missingFrames", 0) for x in long_events if x["event"] == "feeder_block"),
        "abaLongBridge": next(x["bridge"] for x in long_events if x["event"] == "record_complete"),
    }
    (OUT/"analysis.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
