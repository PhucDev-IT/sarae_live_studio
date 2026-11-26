# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Agent Core sidecar (one-file console exe, launched hidden by Tauri).
# Build via desktop/scripts/build-sidecar.bat; outputs go to agent-core/build/dist (git-ignored).
import os
import shutil

from PyInstaller.utils.hooks import collect_submodules

SRC = os.path.abspath(os.path.join(SPECPATH, "..", "src"))
# Video validation must work on a clean installed machine, without FFprobe on PATH.
# Build prerequisite: put ffprobe on PATH or set SARAE_FFPROBE to its executable.
FFPROBE = os.environ.get("SARAE_FFPROBE") or shutil.which("ffprobe")
if not FFPROBE or not os.path.isfile(FFPROBE):
    raise RuntimeError("FFprobe is required: install it on PATH or set SARAE_FFPROBE to ffprobe.exe")

hidden = collect_submodules("sarae_agent") + collect_submodules("obsws_python") + collect_submodules("edge_tts")
# Studio reply desk: public LIVE chat reader (generated protobuf modules are imported dynamically).
hidden += collect_submodules("TikTokLive") + collect_submodules("TikTokLiveProto")
# Keep Studio's CLI/service and SQLite (including its native extension/DLL hook) in the
# frozen sidecar even when package discovery misses the separately selected entrypoint.
hidden += ["sarae_agent.studio.__main__", "sarae_agent.studio.service", "sqlite3", "_sqlite3"]

a = Analysis(
    [os.path.join(SRC, "sarae_agent", "__main__.py")],
    pathex=[SRC],
    binaries=[(os.path.abspath(FFPROBE), ".")],
    # The OBS overlay renderer page (built from web/ by `npm run build:overlay`), served by sarae_agent.overlay.server.
    datas=[(os.path.join(SRC, "sarae_agent", "overlay", "renderer"), os.path.join("sarae_agent", "overlay", "renderer")),
           (os.path.join(SRC, "sarae_agent", "monitoring", "resources"), os.path.join("sarae_agent", "monitoring", "resources"))],
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
