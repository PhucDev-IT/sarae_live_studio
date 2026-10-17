"""Sarae Windows Agent: local detector and outbound cloud heartbeat.

Run from backend directory: python -m agent.main
Cloud/backend may be on another host. The agent does not expose an inbound port.
"""
import json
import os
import platform
import signal
import threading
import time
import urllib.error
import urllib.request
from app.verification_monitor import start, status
from app.config import settings

def main():
    server=settings.sarae_cloud_api_url.rstrip("/")
    if not server:
        raise SystemExit("Set SARAE_CLOUD_API_URL=https://your-host/api")
    if not settings.sarae_agent_key:
        raise SystemExit("Set SARAE_AGENT_KEY in backend/.env")
    if not server.startswith(("https://","http://localhost:","http://127.0.0.1:")):
        raise SystemExit("Cloud URL phải dùng HTTPS (ngoại trừ localhost dev)")
    agent_id=settings.sarae_agent_id or platform.node() or "windows-agent"
    stop_event=threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:signal.signal(sig,lambda *_:stop_event.set())
        except (ValueError,OSError):pass
    if settings.sarae_agent_autostart_monitor:
        try:print("CAPTCHA Monitor:",start())
        except Exception as exc:print("CAPTCHA Monitor start failed:",exc)
    print("Sarae Agent connecting to",server,"as",agent_id)
    while not stop_event.is_set():
        try:
            local=status()
            report={"agent_id":agent_id,"hostname":platform.node(),
                    "verification":{"state":local["state"],"events":local["events"][:10]}}
            data=json.dumps(report).encode("utf-8")
            req=urllib.request.Request(server+"/agents/heartbeat", data=data,method="POST",
                        headers={"Content-Type":"application/json",
                                 "X-Sarae-Agent-Key":settings.sarae_agent_key})
            with urllib.request.urlopen(req,timeout=10) as response:
                response.read()
            print("Heartbeat OK",time.strftime("%H:%M:%S"),local["state"]["status"])
        except Exception as exc:
            print("Heartbeat failed:",str(exc)[:200])
        stop_event.wait(10)

if __name__=="__main__":
    main()
