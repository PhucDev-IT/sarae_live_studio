# -*- mode: python ; coding: utf-8 -*-
# Python Agent Core sidecar for the Tauri desktop app (desktop/src-tauri/binaries).
# Build from backend/: .venv\Scripts\python.exe -m PyInstaller --noconfirm sarae-agent-core.spec
from PyInstaller.utils.hooks import collect_submodules

# agent.gui is the retired PySide6 GUI; Tauri + React replaces it.
hiddenimports = collect_submodules('app') + collect_submodules('agent', filter=lambda name: name != 'agent.gui')
# Imported lazily inside functions; listed so the frozen core never misses them at runtime.
hiddenimports += ['win32crypt', 'win32gui', 'win32process', 'psutil', 'mss', 'cv2', 'numpy', 'edge_tts', 'obsws_python']

a = Analysis(
    ['agent/ipc.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6', 'tkinter', 'matplotlib', 'IPython', 'pytest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='sarae-agent-core',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # Console subsystem keeps stdin/stdout pipes reliable; Tauri spawns it with CREATE_NO_WINDOW.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
