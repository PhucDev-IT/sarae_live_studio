"""Side-by-side comparison video of several engine renders on the same audio timeline.

Each input is LABEL=PATH[@OFFSET_S]: OFFSET_S seconds are cut from the start of that render so its
speech lines up with the reference audio (LiveTalking renders start with pipeline delay; MuseTalk's
official output is already aligned, offset 0). The reference WAV becomes the only audio track.
No postprocessing beyond scaling/cropping is applied; the raw renders stay untouched.

    uv run python tools/compare.py --audio q_main.wav --out cmp.mp4 "W2L=a.mp4@1.04" "MT15=b.mp4"
        [--crop 440:440:320:600]   # zoomed mouth/face view instead of the full frame
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

FONT = "C\\:/Windows/Fonts/arial.ttf"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--audio", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--crop", help="w:h:x:y applied to every input before scaling")
    ap.add_argument("--height", type=int, default=960)
    args = ap.parse_args(argv)

    cmd = ["ffmpeg", "-v", "error", "-y"]
    filters, labels = [], []
    for i, spec in enumerate(args.inputs):
        label, _, rest = spec.partition("=")
        path, _, off = rest.partition("@")
        cmd += ["-ss", off or "0", "-i", path]
        chain = f"[{i}:v]setpts=PTS-STARTPTS,fps=25"
        if args.crop:
            chain += f",crop={args.crop}"
        chain += (f",scale=-2:{args.height},drawtext=fontfile='{FONT}':text='{label}':x=12:y=12:fontsize=28:"
                  f"fontcolor=white:box=1:boxcolor=black@0.6[v{i}]")
        filters.append(chain)
        labels.append(f"[v{i}]")
    n = len(args.inputs)
    cmd += ["-i", str(args.audio)]
    filters.append(f"{''.join(labels)}hstack=inputs={n}[v]" if n > 1 else f"{labels[0]}copy[v]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[v]", "-map", f"{n}:a", "-shortest",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", str(args.out)]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
