"""Real FFmpeg/FFprobe coverage for local OBS media preparation."""
import shutil
import subprocess

import pytest

from sarae_agent.media.probe import obs_file, probe_media

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="FFmpeg and FFprobe required")


def make_video(path, *, size="108x192", fps=25, audio=True, rotation=0):
    command = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
               f"testsrc2=size={size}:rate={fps}:duration=0.5"]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.5"]
    command += ["-c:v", "mpeg4", "-q:v", "5"]
    if audio:
        command += ["-c:a", "aac", "-shortest"]
    command += [str(path)]
    subprocess.run(command, check=True, capture_output=True)
    if rotation:
        rotated = path.with_name("rotated" + path.suffix)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-display_rotation:v:0", str(rotation),
                        "-i", str(path), "-c", "copy", str(rotated)], check=True, capture_output=True)
        return rotated
    return path


@pytest.mark.parametrize("suffix,size,fps,audio,rotation", [
    (".mp4", "108x192", 25, True, 0),
    (".mov", "192x108", 30, True, 90),
    (".mp4", "192x108", 24, False, 0),
    (".mov", "192x108", 60, False, 180),
])
def test_probe_and_prepare(tmp_path, monkeypatch, suffix, size, fps, audio, rotation):
    monkeypatch.setenv("SARAE_AGENT_HOME", str(tmp_path / "agent"))
    source = make_video(tmp_path / ("source" + suffix), size=size, fps=fps,
                        audio=audio, rotation=rotation)
    before = source.read_bytes()
    info = probe_media(source)
    assert info.rotation == rotation
    assert info.audio is audio
    assert info.fps == f"{fps}/1"
    if audio:
        assert info.sample_rate == 48000 and info.channels == 1
    ready = obs_file(source, info)
    rendered = probe_media(ready)
    assert rendered.rotation == 0 and rendered.audio is audio
    assert (rendered.width, rendered.height) == ((info.height, info.width) if rotation in (90, 270)
                                                  else (info.width, info.height))
    assert source.read_bytes() == before
    assert obs_file(source, info) == ready
