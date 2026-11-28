"""Packaged-build helpers: bundled FFmpeg tools and the build profile baked into the sidecar.

The Windows installer ships FFmpeg next to the sidecar (``<install dir>\\ffmpeg\\ffmpeg.exe``, a
Tauri resource) instead of inside the PyInstaller one-file archive, so ~200 MB is not extracted to
%TEMP% on every start. Source/development runs keep using PATH and these helpers do nothing.

``build_profile.json`` is written by ``agent-core/build/sarae-agent-core.spec``. A ``customer``
profile pins the account server URL and removes development-only switches from the environment,
even when someone runs the installed sidecar by hand.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

PROFILE_FILE = "build_profile.json"
TOOLS_FOLDER = "ffmpeg"
# Never honoured by a customer build (the license check must not be skippable).
CUSTOMER_BLOCKED_ENV = ("SARAE_STUDIO_DEV_UNLICENSED",)


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def tool_dirs() -> list[Path]:
    """Folders searched for bundled tools in a packaged build, most specific first."""
    if not _frozen():
        return []
    install = Path(sys.executable).parent
    folders = [install / TOOLS_FOLDER, install]
    if getattr(sys, "_MEIPASS", None):
        folders.insert(0, Path(sys._MEIPASS))
    return folders


def bundled_tool(name: str) -> str | None:
    """Absolute path of a bundled ``ffmpeg``/``ffprobe`` executable, or None (then use PATH)."""
    filename = f"{name}.exe" if os.name == "nt" else name
    for folder in tool_dirs():
        candidate = folder / filename
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


def expose_bundled_tools(environ=None) -> str | None:
    """Prepend the installed FFmpeg folder to PATH so every ``shutil.which("ffmpeg")`` finds it.

    Only the dedicated tools folder is added (never the PyInstaller extraction root), so child
    processes such as Chrome or OBS do not pick up the sidecar's private DLLs.
    """
    environ = os.environ if environ is None else environ
    if not _frozen():
        return None
    folder = Path(sys.executable).parent / TOOLS_FOLDER
    executable = folder / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not executable.is_file():
        return None
    current = environ.get("PATH", "")
    entries = [entry for entry in current.split(os.pathsep) if entry]
    if not any(os.path.normcase(entry) == os.path.normcase(str(folder)) for entry in entries):
        environ["PATH"] = os.pathsep.join([str(folder), *entries])
    return str(folder)


def load_profile(path: Path | None = None) -> dict:
    path = path or Path(__file__).with_name(PROFILE_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _valid_server_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    return (parts.scheme == "https" and bool(parts.hostname) and not parts.username and not parts.password
            and not parts.query and not parts.fragment)


def apply_profile(environ=None, profile: dict | None = None) -> dict:
    """Apply a customer build profile to ``environ``; returns the profile that was read."""
    environ = os.environ if environ is None else environ
    profile = load_profile() if profile is None else profile
    if profile.get("channel") != "customer":
        return profile
    for name in CUSTOMER_BLOCKED_ENV:
        environ.pop(name, None)
    url = profile.get("server_url")
    if not _valid_server_url(url):
        # A broken customer profile must fail closed, never fall back to a developer setting.
        raise RuntimeError("Bản cài đặt thiếu địa chỉ máy chủ Sarae hợp lệ. Hãy cài lại Sarae Live Studio.")
    environ["SARAE_STUDIO_SERVER_URL"] = url
    return profile


def startup(environ=None) -> dict:
    """Entry-point hook: build profile first, then bundled tools on PATH."""
    profile = apply_profile(environ)
    expose_bundled_tools(environ)
    return profile
