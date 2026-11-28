"""Studio Core entrypoint (PyInstaller sidecar `sarae-agent-core`).

    sarae-agent-core [--studio] [studio options]    the local Studio Core (Tauri passes --studio --ipc)
    sarae-agent-core --self-check                    verify bundled native deps and build profile, print JSON, exit
    sarae-agent-core --version

In IPC mode stdout carries frames only: the original stdout handle is duplicated for IPC and fd 1 is pointed at
stderr, so stray print() calls or native libraries can never corrupt a frame (see _isolate_stdout).
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys

from sarae_agent import IPC_PROTOCOL_VERSION, __version__


def _isolate_stdout() -> io.BufferedWriter:
    sys.stdout.flush()
    ipc_fd = os.dup(1)
    os.dup2(2, 1)  # native writes to fd 1 now land on stderr
    sys.stdout = io.TextIOWrapper(os.fdopen(os.dup(2), "wb", buffering=0), encoding="utf-8",
                                  errors="replace", line_buffering=True)
    return os.fdopen(ipc_fd, "wb", buffering=0)


def self_check() -> int:
    """Build check (desktop/scripts/build-*.bat): native modules load, DPAPI works, the build profile is right."""
    report: dict = {"version": __version__, "ipc_protocol": IPC_PROTOCOL_VERSION, "python": sys.version,
                    "frozen": bool(getattr(sys, "frozen", False)), "modules": {}}
    for name in ("httpx", "pydantic", "obsws_python", "numpy", "cv2", "edge_tts"):
        try:
            module = __import__(name)
            report["modules"][name] = getattr(module, "__version__", "ok")
        except Exception as exc:  # noqa: BLE001
            report["modules"][name] = f"ERROR {type(exc).__name__}: {exc}"
    try:
        from sarae_agent.activation import dpapi
        report["dpapi_roundtrip"] = dpapi.unprotect(dpapi.protect(b"probe")) == b"probe"
    except Exception as exc:  # noqa: BLE001
        report["dpapi_roundtrip"] = f"ERROR {exc}"
    from sarae_agent import bundled
    from sarae_agent.studio.storage import data_root as studio_data_root
    profile = bundled.load_profile()
    report["build_profile"] = {"channel": profile.get("channel", "source"), "server_url": profile.get("server_url")}
    report["studio_data_root"] = str(studio_data_root())
    # Informational: the PyInstaller output folder has no FFmpeg; the installed app must show both.
    report["tools"] = {name: bundled.bundled_tool(name) or shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    ok = all(not str(v).startswith("ERROR") for v in report["modules"].values()) and report["dpapi_roundtrip"] is True
    report["ok"] = ok
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if ok else 1


def main() -> int:
    # Packaged builds: customer profile (server URL, no dev switches) and bundled FFmpeg on PATH.
    from sarae_agent.bundled import startup

    startup()
    args = sys.argv[1:]
    if args == ["--version"]:
        print(__version__)
        return 0
    if args == ["--self-check"]:
        return self_check()
    if "--studio" in args:
        del sys.argv[sys.argv.index("--studio", 1)]
    from sarae_agent.studio.__main__ import main as studio_main

    return studio_main()


if __name__ == "__main__":
    sys.exit(main())
