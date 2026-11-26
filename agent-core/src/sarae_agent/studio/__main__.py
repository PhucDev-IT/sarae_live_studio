"""Standalone local studio runtime: JSON Lines IPC or loopback web development."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from sarae_agent import IPC_PROTOCOL_VERSION, __version__
from sarae_agent.errors import AgentError
from sarae_agent.ipc.server import IpcServer
from sarae_agent.studio.service import StudioError, StudioService
from sarae_agent.studio.storage import data_root
from sarae_agent.studio.transport import METHODS, StudioHTTPServer, StudioTransport


class Params(BaseModel):
    model_config = ConfigDict(extra="allow")


class RuntimeLock:
    """One controller per data directory, held by an OS lock until exit/crash."""
    def __init__(self, data_dir: Path):
        self._file = (data_dir / ".runtime.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._file.seek(0)
                if not self._file.read(1):
                    self._file.write(b"0")
                    self._file.flush()
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            raise StudioError("CORE_ALREADY_RUNNING", "Studio đang mở thư mục dữ liệu này ở tiến trình khác.") from exc

    def close(self):
        self._file.close()


def run(args: argparse.Namespace) -> int:
    # Keep stdout isolated in native mode, including accidental library prints.
    output = None
    if args.ipc:
        from sarae_agent.__main__ import _isolate_stdout
        output = _isolate_stdout()
    service = None
    lock = None
    http = None
    ipc = None
    try:
        root = data_root(Path(args.data_dir) if args.data_dir else None)
        lock = RuntimeLock(root)
        service = StudioService(root)
        transport = StudioTransport(service, native=args.ipc)
        http = StudioHTTPServer(transport, 0 if args.ipc else args.port)
        http_thread = threading.Thread(target=http.serve_forever, name="studio-http", daemon=True)
        http_thread.start()
        cleanup_lock = threading.Lock()

        def stop():
            if not cleanup_lock.acquire(blocking=False):
                return
            transport.stopped.set()
            http.shutdown()

        def parent_watch():
            from sarae_agent.windows.win32 import is_process_alive
            watched = {pid for pid in (args.parent_pid, os.getppid()) if pid and pid > 4}
            while not transport.stopped.wait(1):
                if any(not is_process_alive(pid) for pid in watched):
                    stop()
                    # stdin may be blocked after a hard GUI crash.
                    os._exit(0)

        if args.parent_pid or args.ipc:
            threading.Thread(target=parent_watch, name="studio-parent", daemon=True).start()
        if args.ipc:
            ipc = IpcServer(sys.stdin.buffer, output)
            transport.ipc_emit = ipc.emit

            def dispatch(method, params):
                try:
                    return transport.dispatch(method, params.model_dump())
                except StudioError as exc:
                    raise AgentError(exc.code, exc.message, exc.details) from exc

            for method in METHODS:
                ipc.register(method, lambda params, method=method: dispatch(method, params), Params)
            ipc.register("system.ping", lambda _: {"pong": True})
            ipc.register("system.status", lambda _: transport.dispatch("studio.status", {}))
            ipc.register("system.methods", lambda _: {"methods": ipc.methods, "cloud_command_kinds": []})
            ipc.register("system.shutdown", lambda _: {"accepted": True})
            ipc.emit("core.ready", {"version": __version__, "ipc_protocol": IPC_PROTOCOL_VERSION,
                                   "pid": os.getpid(), "methods": ipc.methods,
                                   "status": transport.dispatch("studio.status", {})})
            reason = ipc.serve(lambda: True)
            ipc.finish_requests()
            stop()
            ipc.emit("core.stopped", {"reason": reason})
        else:
            def on_signal(_signum, _frame):
                transport.stopped.set()
            signal.signal(signal.SIGINT, on_signal)
            signal.signal(signal.SIGTERM, on_signal)
            print(json.dumps({"event": "studio.ready", "base_url": transport.base_url,
                              "version": __version__, "data_dir": str(root)}), flush=True)
            transport.stopped.wait()
            stop()
        return 0
    except Exception as exc:
        if output:
            from sarae_agent.ipc import protocol
            output.write(protocol.encode(protocol.event("core.error", {
                "code": getattr(exc, "code", "CORE_INIT_FAILED"), "message": str(exc)[:500], "fatal": True,
            })))
            output.flush()
        else:
            print(f"Studio: {exc}", file=sys.stderr)
        return 3 if getattr(exc, "code", "") == "CORE_ALREADY_RUNNING" else 2
    finally:
        if ipc:
            ipc.close()
        if http:
            http.server_close()
        if service:
            service.close()
        if lock:
            lock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sarae-local-studio")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--ipc", action="store_true")
    mode.add_argument("--http", action="store_true")
    parser.add_argument("--data-dir")
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--port", type=int, default=8793)
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
