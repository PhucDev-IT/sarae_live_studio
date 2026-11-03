"""In-memory stand-in for the G-OmniVoice voice server API (voice-server/, docs/TTS_SERVER.md 15.2 and 16.2),
plugged into app.tts_server through httpx.MockTransport. Knobs let tests take it offline, make it not ready,
refuse jobs, report a long job on the GPU, lose its voices (new GPU machine) or answer the audio in various ways."""
import hashlib
import itertools
import json
import re
from datetime import datetime, timedelta, timezone

import httpx

TOKEN = "orchestrator-secret-token"


def _multipart(request: httpx.Request) -> dict:
    boundary = re.search(r'boundary="?([^";]+)"?', request.headers["content-type"]).group(1).encode()
    out = {}
    for part in request.read().split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, body = part.split(b"\r\n\r\n", 1)
        name = re.search(rb'name="([^"]+)"', head)
        if name:
            out[name.group(1).decode()] = body[:-2] if body.endswith(b"\r\n") else body
    return out


class FakeVoiceServer:
    def __init__(self):
        self.online = True
        self.state = "ready"
        self.voices: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.idem: dict[str, str] = {}
        self.leases: dict[tuple[str, str], float] = {}
        self.requests: list[httpx.Request] = []
        self.fail_next_create: tuple[int, str] | None = None
        self.drop_next_create = 0                   # network errors before the request reaches the server
        self.running: dict | None = None            # what /v1/admission/live reports on the GPU
        self.audio_mode = "done"                    # done | pending | redirect | expired | failed | big
        self.audio = b"RIFF" + b"\x00" * 2000
        self.ids = itertools.count(1)
        self.asr_text = "Trời ơi bán hàng gọi là chơi đẹp lắm."

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    # --- helpers --------------------------------------------------------------------------------------
    def _err(self, status: int, code: str, message: str = "") -> httpx.Response:
        return httpx.Response(status, json={"error": {"code": code, "message": message or code}})

    def puts(self) -> list[dict]:
        return [_multipart(r) for r in self.requests if r.method == "PUT" and r.url.path.startswith("/v1/voices/")]

    def admission_view(self) -> dict:
        reason = "LONG_JOB_RUNNING" if self.running and self.running.get("class") in ("long", "benchmark") else None
        return {"live_active": bool(self.leases), "leases": [{"owner_ref": o, "session_id": s} for o, s in self.leases],
                "gpu": {"state": self.state, "busy": bool(self.running), "running": self.running, "queued": {}},
                "ready_for_live": reason is None, "reason": reason}

    # --- handler --------------------------------------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host != "tts.test":
            raise AssertionError(f"request left the configured host: {request.url}")
        if not self.online:
            raise httpx.ConnectError("voice server offline", request=request)
        path, method = request.url.path, request.method
        if path == "/health/ready":
            return httpx.Response(200 if self.state == "ready" else 503,
                                  json={"state": self.state, "error": None, "busy": bool(self.running), "pending": 0})
        if request.headers.get("authorization") != f"Bearer {TOKEN}" and not path.endswith("/audio"):
            return self._err(401, "TTS_AUTH")
        if self.state != "ready" and path.startswith("/v1/") and path != "/v1/status":
            return self._err(503, "TTS_NOT_READY")
        m = re.match(r"^/v1/voices/([A-Za-z0-9_-]+)(/warmup)?$", path)
        if m:
            vid, warm = m.group(1), m.group(2)
            if warm:
                return httpx.Response(200, json={"voice_id": vid, "rev": self.voices[vid]["rev"], "cache": "ram", "ms": 0.4}) \
                    if vid in self.voices else self._err(404, "VOICE_NOT_FOUND")
            if method == "PUT":
                form = _multipart(request)
                transcript = form.get("transcript", b"").decode() or None
                sha = hashlib.sha256(form["wav"]).hexdigest()
                rev = hashlib.sha256(f"{sha}{transcript}".encode()).hexdigest()[:16]
                old = self.voices.get(vid)
                if old and old["owner_ref"] != form["owner_ref"].decode():
                    return self._err(404, "VOICE_NOT_FOUND")
                self.voices[vid] = {"rev": rev, "owner_ref": form["owner_ref"].decode(), "transcript": transcript}
                return httpx.Response(200 if old and old["rev"] == rev else 201, json={
                    "voice_id": vid, "rev": rev, "qc": {"warnings": []},
                    "transcript": {"mode": "manual" if transcript else "asr", "manual_text": transcript,
                                   "asr_text": None if transcript else self.asr_text,
                                   "ref_text_used": transcript or self.asr_text},
                    "prompt": {"compat": "c" + rev[:10], "tokens": 172, "asr_used": not transcript}})
            if method == "GET":
                return httpx.Response(200, json={"voice_id": vid, **self.voices[vid]}) if vid in self.voices \
                    else self._err(404, "VOICE_NOT_FOUND")
            if method == "DELETE":
                self.voices.pop(vid, None)
                return httpx.Response(204)
        if path == "/v1/tts/synthesize":
            body = json.loads(request.content)
            if body["voice_id"] not in self.voices:
                return self._err(404, "VOICE_NOT_FOUND")
            return httpx.Response(200, content=self.audio, headers={"content-type": "audio/wav",
                                                                     "X-Inference-Ms": "2300.0", "X-Audio-Ms": "3100.0"})
        if path == "/v1/tts/jobs" and method == "POST":
            if self.drop_next_create:
                self.drop_next_create -= 1
                raise httpx.ConnectError("dropped", request=request)
            if self.fail_next_create:
                status, code = self.fail_next_create
                self.fail_next_create = None
                return self._err(status, code)
            body = json.loads(request.content)
            key = request.headers.get("idempotency-key")
            if key in self.idem:
                return httpx.Response(200, json={**self.jobs[self.idem[key]], "created": "replay"})
            voice = self.voices.get(body["voice_id"])
            if not voice or voice["owner_ref"] != body["owner_ref"]:
                return self._err(404, "VOICE_NOT_FOUND")
            job_id = f"b00t{next(self.ids):02d}JobIdentifier0000"
            exp = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
            job = {"job_id": job_id, "state": "queued", "priority": body["priority"], "tags": body["tags"],
                   "voice_id": body["voice_id"], "voice_rev": voice["rev"], "deadline_at": body["deadline_at"],
                   "reader_agent_id": body["reader_agent_id"], "owner_ref": body["owner_ref"],
                   "chars": len(body["text"]), "read_token": f"readtok.{job_id}.sig", "read_token_expires_at": exp}
            self.jobs[job_id] = job
            if key:
                self.idem[key] = job_id
            return httpx.Response(202, json={**job, "created": "new"})
        if path == "/v1/tts/jobs" and method == "DELETE":
            tag = request.url.params["tag"]
            hit = [j for j in self.jobs.values() if tag in j["tags"] and j["state"] not in ("cancelled", "done")]
            for j in hit:
                j["state"] = "cancelled"
            return httpx.Response(200, json={"cancelled": len(hit)})
        m = re.match(r"^/v1/tts/jobs/([A-Za-z0-9_-]+)(/audio)?$", path)
        if m:
            job = self.jobs.get(m.group(1))
            if job is None:
                return self._err(404, "JOB_NOT_FOUND")
            if m.group(2):
                if request.headers.get("authorization") != f"Bearer {TOKEN}":
                    return self._err(401, "TTS_AUTH")
                if job["state"] == "cancelled":
                    return self._err(410, "JOB_CANCELLED")
                mode = self.audio_mode
                if mode == "pending":
                    return httpx.Response(202, json={"state": "running"})
                if mode == "redirect":
                    return httpx.Response(302, headers={"location": "http://evil.example/steal"})
                if mode == "expired":
                    return self._err(410, "JOB_EXPIRED")
                if mode == "failed":
                    return self._err(409, "JOB_FAILED")
                return httpx.Response(200, content=self.audio, headers={"content-type": "audio/wav"})
            if method == "DELETE":
                job["state"] = "cancelled"
                return httpx.Response(200, json={k: v for k, v in job.items() if k != "read_token"})
            return httpx.Response(200, json={k: v for k, v in job.items() if k != "read_token"})
        if path == "/v1/admission/live":
            if method == "PUT":
                body = json.loads(request.content)
                self.leases[(body["owner_ref"], body["session_id"])] = body["ttl_s"]
            elif method == "DELETE":
                self.leases.pop((request.url.params["owner_ref"], request.url.params["session_id"]), None)
            return httpx.Response(200, json=self.admission_view())
        if path == "/v1/status":
            return httpx.Response(200, json={"state": self.state})
        return self._err(404, "NOT_FOUND")
