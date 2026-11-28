# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Agent Core sidecar (one-file console exe, launched hidden by Tauri).
# Build via desktop/scripts/build-sidecar.bat; outputs go to agent-core/build/dist (git-ignored).
import json
import os
from urllib.parse import urlsplit

from PyInstaller.utils.hooks import collect_submodules

SRC = os.path.abspath(os.path.join(SPECPATH, "..", "src"))
# FFmpeg/FFprobe are NOT packed into this one-file exe (that would extract ~200 MB to %TEMP% on
# every start). The installer ships them as Tauri resources in <install dir>/ffmpeg/ (see
# desktop/scripts/fetch-ffmpeg.ps1); sarae_agent.bundled finds them at runtime.

# Build profile baked into the exe (sarae_agent/build_profile.json):
#   SARAE_BUILD_CHANNEL=customer  -> installer for customers (desktop/scripts/build-windows.bat)
#   anything else / unset         -> "dev" sidecar for tauri dev on a developer machine
CHANNEL = (os.environ.get("SARAE_BUILD_CHANNEL") or "dev").strip().lower()
if CHANNEL not in ("customer", "dev"):
    raise RuntimeError(f"SARAE_BUILD_CHANNEL must be 'customer' or 'dev', got {CHANNEL!r}")
SERVER_URL = (os.environ.get("SARAE_BUILD_SERVER_URL") or "https://api.puns.id.vn").strip().rstrip("/")
_url = urlsplit(SERVER_URL)
if _url.scheme != "https" or not _url.hostname or _url.username or _url.password or _url.query or _url.fragment:
    raise RuntimeError(f"SARAE_BUILD_SERVER_URL must be a plain https:// URL, got {SERVER_URL!r}")
PROFILE_DIR = os.path.join(workpath, "profile", "sarae_agent")
os.makedirs(PROFILE_DIR, exist_ok=True)
PROFILE = os.path.join(PROFILE_DIR, "build_profile.json")
with open(PROFILE, "w", encoding="utf-8") as handle:
    json.dump({"channel": CHANNEL, "server_url": SERVER_URL if CHANNEL == "customer" else None}, handle)
print(f"[spec] build profile: channel={CHANNEL} server_url={SERVER_URL if CHANNEL == 'customer' else '-'}")

hidden = collect_submodules("sarae_agent") + collect_submodules("obsws_python") + collect_submodules("edge_tts")
# Studio reply desk: public LIVE chat reader (generated protobuf modules are imported dynamically).
hidden += collect_submodules("TikTokLive") + collect_submodules("TikTokLiveProto")
# Keep Studio's CLI/service and SQLite (including its native extension/DLL hook) in the
# frozen sidecar even when package discovery misses the separately selected entrypoint.
hidden += ["sarae_agent.studio.__main__", "sarae_agent.studio.service", "sqlite3", "_sqlite3"]

a = Analysis(
    [os.path.join(SRC, "sarae_agent", "__main__.py")],
    pathex=[SRC],
    binaries=[],
    # The OBS overlay renderer page (built from web/ by `npm run build:overlay`), served by sarae_agent.overlay.server.
    datas=[(os.path.join(SRC, "sarae_agent", "overlay", "renderer"), os.path.join("sarae_agent", "overlay", "renderer")),
           (os.path.join(SRC, "sarae_agent", "monitoring", "resources"), os.path.join("sarae_agent", "monitoring", "resources")),
           (PROFILE, "sarae_agent")],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6", "PySide6", "pandas", "scipy"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="sarae-agent-core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed binaries trigger antivirus false positives
    console=True,  # stdio pipes are required; Tauri spawns it with CREATE_NO_WINDOW
    disable_windowed_traceback=False,
    target_arch=None,
)
