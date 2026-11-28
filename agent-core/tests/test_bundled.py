"""Packaged-build helpers: customer build profile and the FFmpeg shipped beside the sidecar."""
import json
import os

import pytest

from sarae_agent import bundled
from sarae_agent.studio import media_thumbnails as frames
from sarae_agent.studio import service as service_module

EXE = ".exe" if os.name == "nt" else ""


def install(tmp_path, monkeypatch, *, tools=("ffmpeg", "ffprobe")):
    """Fake installed layout: <install>/sarae-agent-core.exe + <install>/ffmpeg/<tool>.exe."""
    folder = tmp_path / "install" / "ffmpeg"
    folder.mkdir(parents=True)
    for name in tools:
        tool = folder / f"{name}{EXE}"
        tool.write_bytes(b"tool")
        tool.chmod(0o755)
    monkeypatch.setattr(bundled.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bundled.sys, "executable", str(tmp_path / "install" / f"sarae-agent-core{EXE}"))
    monkeypatch.setattr(bundled.sys, "_MEIPASS", str(tmp_path / "mei"), raising=False)
    return folder


def test_customer_profile_pins_server_and_drops_dev_switch():
    environ = {"SARAE_STUDIO_DEV_UNLICENSED": "1", "SARAE_STUDIO_SERVER_URL": "http://127.0.0.1:9000"}
    bundled.apply_profile(environ, {"channel": "customer", "server_url": "https://api.puns.id.vn"})
    assert environ == {"SARAE_STUDIO_SERVER_URL": "https://api.puns.id.vn"}


@pytest.mark.parametrize("url", [None, "", "http://api.puns.id.vn", "https://user:pw@api.puns.id.vn",
                                 "https://api.puns.id.vn/?x=1", "ftp://api.puns.id.vn"])
def test_customer_profile_fails_closed_on_bad_server_url(url):
    environ = {"SARAE_STUDIO_DEV_UNLICENSED": "1"}
    with pytest.raises(RuntimeError):
        bundled.apply_profile(environ, {"channel": "customer", "server_url": url})
    assert "SARAE_STUDIO_DEV_UNLICENSED" not in environ


@pytest.mark.parametrize("profile", [{}, {"channel": "dev", "server_url": None}])
def test_source_and_dev_builds_keep_the_environment(profile):
    environ = {"SARAE_STUDIO_DEV_UNLICENSED": "1", "SARAE_STUDIO_SERVER_URL": "http://127.0.0.1:9000"}
    bundled.apply_profile(environ, profile)
    assert environ == {"SARAE_STUDIO_DEV_UNLICENSED": "1", "SARAE_STUDIO_SERVER_URL": "http://127.0.0.1:9000"}


def test_load_profile_reads_baked_file_and_ignores_missing_or_broken(tmp_path):
    path = tmp_path / bundled.PROFILE_FILE
    assert bundled.load_profile(path) == {}
    path.write_text("not json", encoding="utf-8")
    assert bundled.load_profile(path) == {}
    path.write_text(json.dumps({"channel": "customer", "server_url": "https://api.puns.id.vn"}), encoding="utf-8")
    assert bundled.load_profile(path)["channel"] == "customer"


def test_source_checkout_has_no_baked_profile():
    # build_profile.json only exists inside a PyInstaller build; a committed one would silently
    # turn developer runs into customer runs (or the reverse).
    assert not (bundled.Path(bundled.__file__).with_name(bundled.PROFILE_FILE)).exists()


def test_installed_tools_are_found_and_put_on_path(tmp_path, monkeypatch):
    folder = install(tmp_path, monkeypatch)
    assert bundled.bundled_tool("ffmpeg") == str(folder / f"ffmpeg{EXE}")
    assert bundled.bundled_tool("ffprobe") == str(folder / f"ffprobe{EXE}")
    environ = {"PATH": os.pathsep.join(["C:\\Windows", "C:\\Other"])}
    assert bundled.expose_bundled_tools(environ) == str(folder)
    assert environ["PATH"].split(os.pathsep) == [str(folder), "C:\\Windows", "C:\\Other"]
    bundled.expose_bundled_tools(environ)  # idempotent
    assert environ["PATH"].split(os.pathsep).count(str(folder)) == 1


def test_path_is_untouched_without_installed_tools_or_from_source(tmp_path, monkeypatch):
    install(tmp_path, monkeypatch, tools=())
    environ = {"PATH": "C:\\Windows"}
    assert bundled.expose_bundled_tools(environ) is None
    assert environ == {"PATH": "C:\\Windows"}
    monkeypatch.setattr(bundled.sys, "frozen", False)
    assert bundled.tool_dirs() == []
    assert bundled.bundled_tool("ffmpeg") is None


def test_studio_uses_the_installed_ffmpeg_before_path(tmp_path, monkeypatch):
    folder = install(tmp_path, monkeypatch)
    monkeypatch.setattr(frames.shutil, "which", lambda _: pytest.fail("PATH must not be searched"))
    assert frames.resolve_ffmpeg() == str(folder / f"ffmpeg{EXE}")


def test_video_probe_uses_the_installed_ffprobe(tmp_path, monkeypatch):
    folder = install(tmp_path, monkeypatch)
    monkeypatch.setattr(service_module.shutil, "which", lambda _: pytest.fail("PATH must not be searched"))
    seen = {}

    def run(args, **kwargs):
        seen["executable"] = args[0]
        raise service_module.subprocess.TimeoutExpired(args, 15)

    monkeypatch.setattr(service_module.subprocess, "run", run)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\0" * 32)
    with pytest.raises(service_module.StudioError):
        service_module.probe_video(video)
    assert seen["executable"] == str(folder / f"ffprobe{EXE}")
