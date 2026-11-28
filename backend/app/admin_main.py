"""Admin page for the home PC: customers (license, devices, priority) and cloned voices. `uvicorn app.admin_main:app --host 127.0.0.1 --port 8001` (start-admin.cmd).

Only the admin clones and manages voices (docs/HYBRID_STUDIO_SERVER.md); every licensed customer sees them all
through the gateway. This app is never behind cloudflared: the tunnel only routes to the gateway on :8000.
Defence in depth anyway:
- loopback peers only, and a Host header of 127.0.0.1/localhost on this port (blocks DNS rebinding);
- every /admin/api call needs `X-Sarae-Admin: 1`, a custom header a foreign page cannot send without a CORS
  preflight, and this app answers no preflight (blocks CSRF from other sites open in the same browser).
The voice server token stays in this process, like in the gateway.
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
import re

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app import tts_server

log = logging.getLogger(__name__)

PORT = int(os.environ.get("SARAE_ADMIN_PORT", "8001"))
VOICE_ID = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
MAX_WAV_BYTES = 5 * 1024 * 1024          # sarae-voice-server max_wav_bytes
MAX_BODY_BYTES = 8 * 1024 * 1024          # base64 of a 5 MB WAV plus the other fields
MAX_PREVIEW_TEXT = 450
LOOPBACK = {"127.0.0.1", "::1", "localhost"}

app = FastAPI(title="Sarae Voice Admin", docs_url=None, redoc_url=None, openapi_url=None)


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


@app.middleware("http")
async def local_only(request: Request, call_next):
    peer = request.client.host if request.client else ""
    host = (request.headers.get("host") or "").lower()
    if peer not in LOOPBACK or host not in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}:
        return _error(403, "Trang quản lý giọng chỉ mở trên chính PC server (127.0.0.1).")
    if request.url.path.startswith("/admin/api/"):
        if request.headers.get("x-sarae-admin") != "1":
            return _error(403, "Thiếu header X-Sarae-Admin.")
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
            return _error(413, "File gửi lên quá lớn (tối đa 5 MB WAV).")
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def _voice_error(exc: Exception) -> HTTPException:
    if isinstance(exc, tts_server.TtsUnavailable):
        return HTTPException(503, f"Voice server chưa chạy hoặc chưa sẵn sàng ({exc.code}). Hãy mở sarae-voice-server.")
    if isinstance(exc, tts_server.TtsError):
        if exc.code == "VOICE_NOT_FOUND":
            return HTTPException(404, "Không tìm thấy giọng này.")
        if exc.status in (400, 413, 422):
            return HTTPException(422, f"Voice server từ chối: {exc.message or exc.code}")
        return HTTPException(502, f"Voice server lỗi ({exc.code}).")
    return HTTPException(500, "Lỗi không xác định.")


def _check_voice_id(voice_id: str) -> str:
    if not VOICE_ID.match(voice_id or ""):
        raise HTTPException(422, "Mã giọng chỉ gồm chữ không dấu, số, _ hoặc -, dài 3 đến 64 ký tự.")
    return voice_id


def _summary(profile: dict) -> dict:
    """Admin view of a profile: everything useful to manage it, never paths or tokens."""
    wav = profile.get("wav") if isinstance(profile.get("wav"), dict) else {}
    transcript = profile.get("transcript") if isinstance(profile.get("transcript"), dict) else {}
    qc = profile.get("qc") if isinstance(profile.get("qc"), dict) else {}
    warnings = qc.get("warnings") if isinstance(qc.get("warnings"), list) else []
    return {"voice_id": profile.get("voice_id"), "name": profile.get("name") or profile.get("voice_id"),
            "rev": profile.get("rev") or "", "updated_at": profile.get("updated_at"),
            "duration_s": wav.get("duration_s"), "sample_rate": wav.get("sample_rate"),
            "transcript_mode": transcript.get("mode"), "manual_text": transcript.get("manual_text"),
            "asr_text": transcript.get("asr_text"), "ready": profile.get("prompt") is not None,
            "warnings": [str(w)[:200] for w in warnings[:10]]}


@app.get("/", response_class=HTMLResponse)
def page():
    return HTMLResponse(PAGE)


@app.get("/admin/api/voices")
def voices():
    health = tts_server.ready(max_age=0)
    try:
        listed = [_summary(p) for p in tts_server.list_voices() if VOICE_ID.match(str(p.get("voice_id", "")))]
    except (tts_server.TtsUnavailable, tts_server.TtsError) as exc:
        return {"online": False, "state": health.get("state"), "error": _voice_error(exc).detail, "voices": []}
    return {"online": bool(health.get("online")) and health.get("state") == "ready", "state": health.get("state"),
            "error": None, "voices": listed}


@app.post("/admin/api/voices")
def create_voice(body: dict = Body(...)):
    """Upload a reference WAV (1-20 s) to clone. `replace` must be true to overwrite an existing voice id."""
    voice_id = _check_voice_id(str(body.get("voice_id") or "").strip())
    name = str(body.get("name") or "").strip()[:100] or voice_id
    transcript = str(body.get("transcript") or "").strip()[:1000] or None
    try:
        wav = base64.b64decode(str(body.get("wav_base64") or ""), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "File WAV không đọc được.")
    if len(wav) < 44 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise HTTPException(422, "Cần file WAV (RIFF/WAVE). Hãy xuất lại file mẫu sang .wav.")
    if len(wav) > MAX_WAV_BYTES:
        raise HTTPException(413, "File WAV quá lớn (tối đa 5 MB, nên dài 5 đến 20 giây).")
    try:
        if not body.get("replace") and tts_server.get_voice(voice_id) is not None:
            raise HTTPException(409, "Mã giọng này đã có. Chọn mã khác hoặc tick 'Thay giọng cũ'.")
        profile = tts_server.put_voice(voice_id, wav, transcript, name, owner_ref="admin")
    except (tts_server.TtsUnavailable, tts_server.TtsError) as exc:
        raise _voice_error(exc) from None
    log.info("voice saved id=%s bytes=%s", voice_id, len(wav))
    return _summary(profile)


@app.put("/admin/api/voices/{voice_id}/transcript")
def set_transcript(voice_id: str, body: dict = Body(...)):
    _check_voice_id(voice_id)
    transcript = body.get("transcript")
    if transcript is not None and (not isinstance(transcript, str) or len(transcript) > 1000):
        raise HTTPException(422, "Transcript tối đa 1000 ký tự.")
    try:
        return _summary(tts_server.put_transcript(voice_id, (transcript or "").strip() or None))
    except (tts_server.TtsUnavailable, tts_server.TtsError) as exc:
        raise _voice_error(exc) from None


@app.delete("/admin/api/voices/{voice_id}", status_code=204)
def delete_voice(voice_id: str):
    _check_voice_id(voice_id)
    try:
        tts_server.delete_voice(voice_id)
    except (tts_server.TtsUnavailable, tts_server.TtsError) as exc:
        raise _voice_error(exc) from None
    log.info("voice deleted id=%s", voice_id)
    return Response(status_code=204)


@app.post("/admin/api/voices/{voice_id}/preview")
def preview(voice_id: str, body: dict = Body(...)):
    """Same preset as a LIVE sentence (live_fast), so the admin hears what customers will hear."""
    _check_voice_id(voice_id)
    text = str(body.get("text") or "").strip()
    if not text or len(text) > MAX_PREVIEW_TEXT:
        raise HTTPException(422, f"Câu nghe thử cần 1 đến {MAX_PREVIEW_TEXT} ký tự.")
    try:
        audio, _rev = tts_server.synthesize_live(voice_id, text, timeout=90, max_bytes=10 * 1024 * 1024)
    except (tts_server.TtsUnavailable, tts_server.TtsError) as exc:
        raise _voice_error(exc) from None
    return Response(audio, media_type="audio/wav")


# --- customers (same operations as the CLI tools.studio_admin) ---------------------------------------------------
def _admin():
    from tools import studio_admin          # backend/ is the working directory, as for the CLI
    return studio_admin


def _run(operation):
    admin = _admin()
    try:
        return operation(admin)
    except admin.AdminError as exc:
        raise HTTPException(422, str(exc)) from None


def _expiry(admin, body: dict):
    """`expires`: "YYYY-MM-DD" (end of that day, Vietnam time) or null/"" for no expiry."""
    value = body.get("expires")
    return admin.expiry_from_date(str(value)) if value else None


@app.get("/admin/api/customers")
def customers(days: int = 7):
    from app import studio_gateway
    admin = _admin()
    priority = studio_gateway.priority_owners()
    usage: dict[str, dict] = {}
    for row in studio_gateway.usage_rows(None, max(1, min(days, 90))):
        total = usage.setdefault(row["owner_id"], {"requests": 0, "failures": 0, "chars": 0})
        for key in total:
            total[key] += row[key]
    result = []
    for r in admin.customers():
        expires = r["expires_at"]
        result.append({"email": r["email"], "status": admin.status_text(r), "enabled": bool(r["enabled"]),
                       "devices": r["devices"], "max_devices": r["max_agents"], "expires_at": expires,
                       "expires_text": admin.show_expiry(expires), "priority": r["id"] in priority,
                       "usage": usage.get(r["id"], {"requests": 0, "failures": 0, "chars": 0})})
    return {"customers": result, "days": days}


@app.post("/admin/api/customers")
def create_customer(body: dict = Body(...)):
    """Create the customer and hand back a first activation key (shown once)."""
    email = str(body.get("email") or "")
    try:
        max_devices = int(body.get("max_devices") or 1)
    except (TypeError, ValueError):
        raise HTTPException(422, "Số máy phải là số.") from None
    key = _run(lambda a: (a.create_customer(email, max_devices, _expiry(a, body)), a.issue_key(email))[1])
    log.info("customer created")
    return {"email": email.strip().lower(), "activation_key": key}


@app.post("/admin/api/customers/{email}/keys")
def new_key(email: str):
    return {"activation_key": _run(lambda a: a.issue_key(email))}


@app.patch("/admin/api/customers/{email}")
def update_customer(email: str, body: dict = Body(...)):
    """Any of: `expires` (YYYY-MM-DD or null), `max_devices`, `enabled`, `priority`."""
    from app import studio_gateway

    def apply(admin):
        if "expires" in body:
            admin.set_expiry(email, _expiry(admin, body))
        if "max_devices" in body:
            try:
                admin.set_max_devices(email, int(body["max_devices"]))
            except (TypeError, ValueError):
                raise admin.AdminError("Số máy phải là số.") from None
        if "enabled" in body:
            admin.set_enabled(email, bool(body["enabled"]))
        if "priority" in body:
            with admin.db_open() as db:
                owner_id = admin.owner_by_email(db, email)["id"]
            studio_gateway.set_owner_priority(owner_id, 1 if body["priority"] else 0)
    _run(apply)
    return {"ok": True}


@app.get("/admin/api/customers/{email}/devices")
def customer_devices(email: str):
    admin = _admin()
    rows = _run(lambda a: a.devices(email))
    return {"devices": [{"agent_id": r["id"], "hostname": r["hostname"], "platform": r["platform"],
                         "enabled": bool(r["enabled"]), "activated": admin.show_expiry(r["created_at"])} for r in rows]}


@app.delete("/admin/api/devices/{agent_id}", status_code=204)
def revoke_device(agent_id: str):
    _run(lambda a: a.revoke_device(agent_id))
    return Response(status_code=204)


PAGE = r"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sarae Admin</title>
<style>
:root{color-scheme:light dark;--bg:#f4f5f8;--surface:#fff;--surface-2:#f8f9fb;--text:#14171c;--muted:#667085;--line:#e4e7ec;--line-2:#eef0f3;
--accent:#5b4fe9;--accent-2:#4b3fd8;--accent-soft:#eeecfd;--accent-text:#4338ca;
--ok:#12805c;--ok-soft:#e3f6ee;--warn:#b54708;--warn-soft:#fef3e2;--danger:#d92d20;--danger-soft:#fdecea;
--shadow:0 1px 2px rgba(16,24,40,.05),0 1px 3px rgba(16,24,40,.06);--shadow-lg:0 24px 48px -12px rgba(16,24,40,.28);--r:14px}
@media (prefers-color-scheme:dark){:root{--bg:#0d0f14;--surface:#161a21;--surface-2:#1c212a;--text:#e8eaef;--muted:#98a1b0;--line:#2a303b;--line-2:#222731;
--accent:#7c6cf5;--accent-2:#8f81f7;--accent-soft:#25223f;--accent-text:#b4acfa;--ok:#4cc38a;--ok-soft:#14291f;--warn:#f0a646;--warn-soft:#2e2312;--danger:#f97066;--danger-soft:#33191a;
--shadow:0 1px 2px rgba(0,0,0,.35);--shadow-lg:0 24px 48px -12px rgba(0,0,0,.7)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 "Segoe UI",system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
code{font:12px/1.4 ui-monospace,"Cascadia Code",Consolas,monospace;background:var(--surface-2);border:1px solid var(--line-2);padding:1px 6px;border-radius:5px}
svg.i{width:16px;height:16px;flex:none}
.muted{color:var(--muted)}.sm{font-size:12px}.ok{color:var(--ok)}.warn{color:var(--warn)}.err{color:var(--danger)}
.spacer{flex:1}[hidden]{display:none!important}

/* top bar */
.top{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--surface) 88%,transparent);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.top-in{max-width:1240px;margin:0 auto;padding:12px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px}
.logo{width:36px;height:36px;border-radius:11px;background:linear-gradient(135deg,#6d5ef0,#c04df0);display:grid;place-items:center;color:#fff;font-weight:800;font-size:17px;box-shadow:0 6px 16px -6px #6d5ef0}
.brand b{font-size:15px;display:block;line-height:1.2}.brand small{color:var(--muted);font-size:12px}
.tabs{display:flex;gap:2px;background:var(--surface-2);border:1px solid var(--line);padding:3px;border-radius:11px}
.tabs button{border:0;background:none;height:32px;padding:0 14px;border-radius:8px;color:var(--muted);font-weight:600}
.tabs button:hover{background:none;color:var(--text)}
.tabs button[aria-selected=true]{background:var(--surface);color:var(--text);box-shadow:var(--shadow)}
.count{font-size:11px;font-weight:700;background:var(--line);color:var(--muted);border-radius:99px;padding:0 7px;min-width:22px;text-align:center;line-height:18px}
.tabs button[aria-selected=true] .count{background:var(--accent-soft);color:var(--accent-text)}
main{max-width:1240px;margin:0 auto;padding:24px}

/* stats */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:20px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;box-shadow:var(--shadow);display:flex;gap:14px;align-items:center}
.stat .ic{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;flex:none}
.stat .ic svg{width:20px;height:20px}
.ic.c1{background:var(--accent-soft);color:var(--accent-text)}.ic.c2{background:var(--ok-soft);color:var(--ok)}.ic.c3{background:var(--warn-soft);color:var(--warn)}.ic.c4{background:#e6f0ff;color:#2563eb}
@media (prefers-color-scheme:dark){.ic.c4{background:#16233a;color:#7aa7ff}}
.stat .v{font-size:22px;font-weight:700;line-height:1.15;letter-spacing:-.01em}.stat .l{color:var(--muted);font-size:12.5px}

/* cards */
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);margin-bottom:20px}
.card-h{display:flex;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--line-2);flex-wrap:wrap}
.card-h h2{font-size:15px;margin:0;line-height:1.3}.card-h p{margin:2px 0 0;color:var(--muted);font-size:12.5px}
.card-b{padding:20px}

/* forms */
label{display:block;font-weight:600;font-size:12.5px;margin:0 0 6px}
.hint{font-weight:400;color:var(--muted)}
.grid-c{display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:14px;align-items:end}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.stack>*+*{margin-top:14px}
input[type=text],input[type=number],input[type=date],input[type=search],textarea{width:100%;height:38px;padding:0 12px;border:1px solid var(--line);border-radius:9px;background:var(--surface);color:var(--text);font:inherit;transition:border-color .15s,box-shadow .15s}
textarea{height:auto;min-height:76px;padding:9px 12px;resize:vertical;line-height:1.5}
input:focus,textarea:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.search{position:relative;width:260px;max-width:100%}.search svg{position:absolute;left:11px;top:11px;color:var(--muted)}.search input{padding-left:34px;height:36px}
.check{display:inline-flex;align-items:center;gap:8px;font-weight:500;font-size:13px;cursor:pointer;margin:0}
.check input{width:16px;height:16px;accent-color:var(--accent);margin:0}

/* buttons */
button{font:inherit;font-size:13px;font-weight:600;height:34px;padding:0 12px;border-radius:9px;border:1px solid var(--line);background:var(--surface);color:var(--text);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:6px;white-space:nowrap;transition:background .15s,border-color .15s,color .15s,box-shadow .15s}
button:hover{background:var(--surface-2)}
button:focus-visible{outline:0;box-shadow:0 0 0 3px var(--accent-soft);border-color:var(--accent)}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff;box-shadow:0 2px 6px -2px var(--accent)}button.primary:hover{background:var(--accent-2)}
button.soft{background:var(--accent-soft);border-color:transparent;color:var(--accent-text)}
button.danger{color:var(--danger)}button.danger:hover{background:var(--danger-soft);border-color:color-mix(in srgb,var(--danger) 40%,transparent)}
button.ghost{border-color:transparent;background:none}button.ghost:hover{background:var(--surface-2)}
button.lg{height:38px;padding:0 16px}
button.icon{width:34px;padding:0}
button:disabled{opacity:.5;cursor:default;box-shadow:none}
.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}

/* badges, alerts */
.badge{display:inline-flex;align-items:center;gap:6px;padding:0 10px;border-radius:99px;font-size:12px;font-weight:600;line-height:22px;white-space:nowrap}
.badge::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.badge.ok{background:var(--ok-soft);color:var(--ok)}.badge.warn{background:var(--warn-soft);color:var(--warn)}.badge.err{background:var(--danger-soft);color:var(--danger)}.badge.muted{background:var(--surface-2);color:var(--muted);border:1px solid var(--line)}
.badge.live::before{animation:pulse 1.8s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,currentColor 60%,transparent)}70%{box-shadow:0 0 0 6px transparent}100%{box-shadow:0 0 0 0 transparent}}
.chip{display:inline-block;font-size:11px;font-weight:600;padding:0 8px;border-radius:6px;background:var(--surface-2);border:1px solid var(--line);color:var(--muted);line-height:18px;margin-left:6px}
.alert{display:flex;gap:10px;align-items:flex-start;padding:10px 14px;border-radius:10px;background:var(--danger-soft);color:var(--danger);font-size:13px;margin:0 0 14px}
.alert.warn{background:var(--warn-soft);color:var(--warn)}.alert.info{background:var(--accent-soft);color:var(--accent-text)}
.alert svg{margin-top:2px}
.note{margin:12px 0 0;font-size:13px;min-height:0}.note:empty{display:none}

/* table */
.table{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600;background:var(--surface-2);padding:10px 16px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:12px 16px;border-bottom:1px solid var(--line-2);vertical-align:middle;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:color-mix(in srgb,var(--surface-2) 70%,transparent)}
tr.dirty td{background:color-mix(in srgb,var(--accent-soft) 60%,transparent)!important}
tr.locked .who .em{color:var(--muted);text-decoration:line-through}
.who{display:flex;align-items:center;gap:10px}.em{font-weight:600}
.av{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:13px;color:#fff;flex:none}
td input[type=number]{width:62px;height:32px;padding:0 8px}
td input[type=date]{width:148px;height:32px;padding:0 8px}
.dev{display:flex;align-items:center;gap:8px}
.bar{width:44px;height:6px;border-radius:99px;background:var(--line);overflow:hidden}.bar i{display:block;height:100%;background:var(--accent);border-radius:99px}
.bar.full i{background:var(--warn)}
.acts{display:flex;gap:6px;justify-content:flex-end}
.acts button{height:32px;padding:0 10px}.acts button.icon{width:32px;padding:0}
td.load{padding:40px;text-align:center;color:var(--muted)}

/* switch */
.switch{position:relative;display:inline-flex;align-items:center;gap:8px;cursor:pointer;font-size:12.5px;font-weight:500;color:var(--muted);margin:0}
.switch input{position:absolute;opacity:0;width:1px;height:1px}
.switch .t{width:38px;height:22px;border-radius:99px;background:var(--line);position:relative;transition:background .2s;flex:none}
.switch .t::after{content:"";position:absolute;top:3px;left:3px;width:16px;height:16px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.25);transition:transform .2s}
.switch input:checked+.t{background:var(--accent)}.switch input:checked+.t::after{transform:translateX(16px)}
.switch input:focus-visible+.t{box-shadow:0 0 0 3px var(--accent-soft)}
.switch input:checked~.lb{color:var(--accent-text)}

/* voices */
.voices{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:16px}
.voice{border:1px solid var(--line);border-radius:12px;padding:16px;background:var(--surface);transition:border-color .15s,box-shadow .15s}
.voice:hover{border-color:color-mix(in srgb,var(--accent) 35%,var(--line));box-shadow:var(--shadow)}
.voice header{display:flex;gap:12px;align-items:center;margin-bottom:14px}
.vav{width:42px;height:42px;border-radius:12px;background:linear-gradient(135deg,var(--accent-soft),color-mix(in srgb,#c04df0 18%,var(--surface)));color:var(--accent-text);display:grid;place-items:center;flex:none}
.vav svg{width:20px;height:20px}
.vt{min-width:0;flex:1}.vt strong{display:block;font-size:15px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.voice .actions{margin-top:12px}
.out:not(:empty){margin-top:12px;padding:12px;border-radius:10px;background:var(--surface-2);border:1px solid var(--line-2)}
.out p{margin:0 0 8px;font-size:12.5px}.out a{color:var(--accent-text);font-weight:600;font-size:13px}
audio{width:100%;margin:4px 0 8px;height:40px}
.drop{display:flex;align-items:center;gap:14px;padding:16px;border:1.5px dashed var(--line);border-radius:12px;background:var(--surface-2);cursor:pointer;margin:0;font-weight:400;transition:border-color .15s,background .15s}
.drop:hover,.drop.over{border-color:var(--accent);background:var(--accent-soft)}
.drop .dic{width:42px;height:42px;border-radius:12px;background:var(--surface);border:1px solid var(--line);display:grid;place-items:center;color:var(--accent-text);flex:none}
.drop strong{display:block;font-size:13.5px}
.drop input{position:absolute;opacity:0;width:1px;height:1px}
.sample{display:flex;gap:10px;align-items:center;flex:1;min-width:280px}
.sample label{margin:0;white-space:nowrap}

/* empty, dialogs, toasts */
.empty{display:flex;flex-direction:column;align-items:center;gap:4px;padding:36px 16px;text-align:center}
.empty .eic{width:48px;height:48px;border-radius:14px;background:var(--surface-2);border:1px solid var(--line);display:grid;place-items:center;color:var(--muted);margin-bottom:8px}
.empty .eic svg{width:22px;height:22px}
dialog{border:0;border-radius:18px;padding:0;width:min(620px,calc(100vw - 32px));background:var(--surface);color:var(--text);box-shadow:var(--shadow-lg)}
dialog::backdrop{background:rgba(13,15,20,.5);backdrop-filter:blur(3px)}
dialog[open]{animation:pop .18s ease}
@keyframes pop{from{opacity:0;transform:translateY(8px) scale(.98)}}
.dlg-h{display:flex;align-items:center;gap:12px;padding:18px 20px;border-bottom:1px solid var(--line-2)}
.dlg-h h3{margin:0;font-size:16px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis}
.dlg-b{padding:20px}.dlg-f{display:flex;justify-content:flex-end;gap:8px;padding:0 20px 20px}
.keyval{display:flex;gap:10px;align-items:center;padding:14px 16px;border-radius:12px;background:var(--accent-soft);border:1px dashed var(--accent)}
.keyval code{flex:1;background:none;border:0;padding:0;font-size:14px;font-weight:600;color:var(--accent-text);word-break:break-all}
.drow{display:flex;gap:12px;align-items:center;padding:12px 0;border-bottom:1px solid var(--line-2)}.drow:last-child{border-bottom:0}
.drow .dic{width:38px;height:38px;border-radius:10px;background:var(--surface-2);border:1px solid var(--line);display:grid;place-items:center;color:var(--muted);flex:none}
.drow .grow{flex:1;min-width:0}.drow code{display:inline-block;margin-top:4px;max-width:100%;overflow:hidden;text-overflow:ellipsis}
#toasts{position:fixed;right:20px;bottom:20px;display:flex;flex-direction:column;gap:8px;z-index:50;pointer-events:none}
.toast{display:flex;gap:10px;align-items:center;background:var(--text);color:var(--bg);padding:11px 16px;border-radius:12px;box-shadow:var(--shadow-lg);font-size:13px;font-weight:500;max-width:420px;animation:tin .2s ease;transition:opacity .25s,transform .25s}
.toast.t-ok svg{color:#4cc38a}.toast.t-err{background:var(--danger);color:#fff}
.toast.out{opacity:0;transform:translateY(6px)}
@keyframes tin{from{opacity:0;transform:translateY(10px)}}
.spin{width:16px;height:16px;border:2px solid var(--line);border-top-color:var(--accent);border-radius:50%;display:inline-block;animation:sp .7s linear infinite;vertical-align:-3px;margin-right:8px}
@keyframes sp{to{transform:rotate(360deg)}}

@media (max-width:960px){.stats{grid-template-columns:1fr 1fr}.grid-c{grid-template-columns:1fr 1fr}}
@media (max-width:640px){.top-in,main{padding-left:16px;padding-right:16px}.stats,.grid-c,.grid-2{grid-template-columns:1fr}.voices{grid-template-columns:1fr}.search{width:100%}.tabs{order:3;width:100%}.tabs button{flex:1}}
</style></head><body>
<header class="top"><div class="top-in">
 <div class="brand"><div class="logo">S</div><div><b>Sarae Admin</b><small>Chỉ mở trên PC server</small></div></div>
 <span class="spacer"></span>
 <nav class="tabs" role="tablist">
  <button role="tab" data-tab="customers" aria-selected="true">Khách hàng <span class="count" id="ccount">–</span></button>
  <button role="tab" data-tab="voices" aria-selected="false">Giọng clone <span class="count" id="vcount">–</span></button>
 </nav>
 <span id="server"><span class="badge muted">Đang kiểm tra voice server…</span></span>
</div></header>

<main>
<div data-panel="customers">
 <div class="stats">
  <div class="stat"><div class="ic c1" data-i="users"></div><div><div class="v" id="s-total">–</div><div class="l">Tổng khách</div></div></div>
  <div class="stat"><div class="ic c2" data-i="shield"></div><div><div class="v" id="s-ok">–</div><div class="l">License hợp lệ</div></div></div>
  <div class="stat"><div class="ic c3" data-i="zap"></div><div><div class="v" id="s-prio">–</div><div class="l">Tài khoản ưu tiên</div></div></div>
  <div class="stat"><div class="ic c4" data-i="activity"></div><div><div class="v" id="s-req">–</div><div class="l" id="s-req-sub">Câu TTS 7 ngày</div></div></div>
 </div>

 <section class="card">
  <div class="card-h"><div><h2>Thêm khách</h2><p>Tạo tài khoản và cấp ngay mã kích hoạt cho máy đầu tiên.</p></div></div>
  <div class="card-b"><div class="grid-c">
   <div><label for="cemail">Email</label><input id="cemail" type="text" maxlength="254" placeholder="khach@shop.vn" autocomplete="off"></div>
   <div><label for="cmax">Số máy tối đa</label><input id="cmax" type="number" min="1" max="100" value="1"></div>
   <div><label for="cexp">Hết hạn <span class="hint">(trống = vô thời hạn)</span></label><input id="cexp" type="date"></div>
   <div><button class="primary lg" id="ccreate" data-i="plus">Tạo khách + cấp mã</button></div>
  </div></div>
 </section>

 <section class="card">
  <div class="card-h">
   <div><h2>Danh sách khách</h2><p><b>Ưu tiên</b>: câu TTS của tài khoản được tạo trước câu đang chờ của khách khác. Usage tính 7 ngày gần nhất.</p></div>
   <span class="spacer"></span>
   <div class="search" data-i="search"><input id="csearch" type="search" placeholder="Tìm theo email…" aria-label="Tìm khách"></div>
   <button id="creload" class="icon" title="Làm mới" aria-label="Làm mới" data-i="refresh"></button>
  </div>
  <div class="table"><table>
   <thead><tr><th>Khách hàng</th><th>Trạng thái</th><th>Máy / tối đa</th><th>Hết hạn</th><th>Ưu tiên</th><th>Usage 7 ngày</th><th style="text-align:right">Thao tác</th></tr></thead>
   <tbody id="clist"><tr><td colspan="7" class="load"><span class="spin"></span>Đang tải…</td></tr></tbody>
  </table></div>
  <div id="cnone" hidden><div class="empty"><strong>Không có khách nào khớp</strong><span class="muted">Thử từ khóa khác.</span></div></div>
 </section>
</div>

<div data-panel="voices" hidden>
 <div id="verr"></div>
 <div class="alert info" data-i="info"><span>Mọi khách có license đều dùng được các giọng ở đây.</span></div>

 <section class="card">
  <div class="card-h"><div><h2>Thêm giọng mới</h2><p>Server sẽ clone giọng từ file mẫu, mất khoảng một phút.</p></div></div>
  <div class="card-b stack">
   <div class="grid-2">
    <div><label for="name">Tên hiển thị</label><input id="name" type="text" maxlength="100" placeholder="Giọng Linh Phương"></div>
    <div><label for="vid">Mã giọng <span class="hint">(tự sinh từ tên)</span></label><input id="vid" type="text" maxlength="64" placeholder="linh_phuong"></div>
   </div>
   <label class="drop" id="drop"><span class="dic" data-i="upload"></span><span><strong id="wavname">Chọn hoặc kéo thả file WAV mẫu</strong><span class="muted sm">1 đến 20 giây, giọng rõ, không nhạc nền, tối đa 5 MB</span></span><input id="wav" type="file" accept=".wav,audio/wav"></label>
   <div><label for="tr">Transcript <span class="hint">(không bắt buộc, để trống thì server tự nhận dạng bằng Whisper)</span></label><textarea id="tr" maxlength="1000" placeholder="Nội dung câu nói trong file mẫu…"></textarea></div>
   <div class="actions"><label class="check"><input id="replace" type="checkbox"> Thay giọng cũ nếu mã đã tồn tại</label><span class="spacer"></span><button class="primary lg" id="create" data-i="plus">Tạo giọng</button></div>
   <p id="notice" class="note" role="status"></p>
  </div>
 </section>

 <section class="card">
  <div class="card-h">
   <h2>Danh sách giọng</h2>
   <div class="sample"><label for="sample" class="hint">Câu nghe thử</label><input id="sample" type="text" maxlength="450" value="Dạ chị ơi, mẫu này còn đủ size, chị cho em xin chiều cao cân nặng để em tư vấn nha."></div>
   <button id="reload" class="icon" title="Làm mới" aria-label="Làm mới" data-i="refresh"></button>
  </div>
  <div class="card-b"><div id="list"><p class="muted"><span class="spin"></span>Đang tải…</p></div></div>
 </section>
</div>
</main>

<dialog id="keydlg" aria-labelledby="keytitle">
 <div class="dlg-h"><h3 id="keytitle">Mã kích hoạt mới</h3></div>
 <div class="dlg-b">
  <p style="margin:0 0 12px">Mã cho <b id="keyemail"></b>. Mã chỉ hiện <b>một lần</b> và dùng cho <b>một máy</b>.</p>
  <div class="keyval"><code id="keytext"></code><button id="copykey" class="soft"></button></div>
 </div>
 <div class="dlg-f"><button class="primary" id="keyclose">Đã lưu mã, đóng</button></div>
</dialog>

<dialog id="devdlg" aria-labelledby="devtitle">
 <div class="dlg-h"><h3 id="devtitle">Máy</h3><form method="dialog"><button class="ghost icon" aria-label="Đóng" data-i="x"></button></form></div>
 <div class="dlg-b" id="devbody"></div>
</dialog>

<div id="toasts" aria-live="polite"></div>

<script>
const H={"X-Sarae-Admin":"1","Content-Type":"application/json"};
const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const P={users:'<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
 shield:'<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',zap:'<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
 activity:'<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',refresh:'<path d="M21 12a9 9 0 1 1-2.64-6.36L21 8"/><path d="M21 3v5h-5"/>',plus:'<path d="M12 5v14M5 12h14"/>',
 key:'<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',monitor:'<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
 lock:'<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',unlock:'<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
 check:'<path d="M20 6 9 17l-5-5"/>',play:'<path d="m7 4 12 8-12 8V4z"/>',trash:'<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
 search:'<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',upload:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
 copy:'<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',x:'<path d="M18 6 6 18M6 6l12 12"/>',
 wave:'<path d="M3 10v4M7 6v12M11 3v18M15 7v10M19 10v4"/>',mic:'<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v3"/>',
 alert:'<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',info:'<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
 download:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>'};
const I=n=>`<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[n]}</svg>`;
document.querySelectorAll("[data-i]").forEach(e=>e.insertAdjacentHTML("afterbegin",I(e.dataset.i)));
const num=n=>Number(n||0).toLocaleString("vi-VN");
const empty=(i,t,s)=>`<div class="empty"><div class="eic">${I(i)}</div><strong>${t}</strong><span class="muted">${s}</span></div>`;
const alertBox=(t,cls)=>`<div class="alert ${cls||""}">${I("alert")}<span>${esc(t)}</span></div>`;
function toast(t,kind){if(!t)return;const el=document.createElement("div");el.className="toast t-"+kind;el.innerHTML=I(kind==="err"?"alert":"check")+"<span></span>";
 el.lastChild.textContent=t;$("#toasts").append(el);setTimeout(()=>{el.classList.add("out");setTimeout(()=>el.remove(),300)},kind==="err"?6000:3500)}
function notice(t,cls){const n=$("#notice");n.innerHTML=cls==="muted"?'<span class="spin"></span>'+esc(t):esc(t);n.className="note "+(cls||"");if(cls==="ok"||cls==="err")toast(t,cls)}
async function api(method,path,body){const r=await fetch(path,{method,headers:H,body:body?JSON.stringify(body):undefined});
 if(!r.ok){let d="HTTP "+r.status;try{d=(await r.json()).detail||d}catch(e){}throw new Error(d)}return r}

// --- tabs ---
document.querySelectorAll(".tabs button").forEach(b=>b.addEventListener("click",()=>{
 document.querySelectorAll(".tabs button").forEach(x=>x.setAttribute("aria-selected",x===b));
 document.querySelectorAll("[data-panel]").forEach(p=>p.hidden=p.dataset.panel!==b.dataset.tab)}));

// --- voices ---
function slug(s){return s.normalize("NFD").replace(/[̀-ͯ]/g,"").replace(/đ/g,"d").replace(/Đ/g,"D").toLowerCase().replace(/[^a-z0-9]+/g,"_").replace(/^_+|_+$/g,"").slice(0,64)}
let idEdited=false;$("#vid").addEventListener("input",()=>idEdited=true);
$("#name").addEventListener("input",()=>{if(!idEdited)$("#vid").value=slug($("#name").value)});
function showFile(){const f=$("#wav").files[0];$("#wavname").textContent=f?`${f.name} · ${(f.size/1048576).toFixed(2)} MB`:"Chọn hoặc kéo thả file WAV mẫu"}
$("#wav").addEventListener("change",showFile);
const drop=$("#drop");
["dragenter","dragover"].forEach(t=>drop.addEventListener(t,e=>{e.preventDefault();drop.classList.add("over")}));
["dragleave","drop"].forEach(t=>drop.addEventListener(t,()=>drop.classList.remove("over")));
drop.addEventListener("drop",e=>{e.preventDefault();if(e.dataTransfer.files.length){$("#wav").files=e.dataTransfer.files;showFile()}});
async function load(){const list=$("#list");
 try{const data=await (await api("GET","/admin/api/voices")).json();
  $("#server").innerHTML=data.online?'<span class="badge ok live">Voice server sẵn sàng</span>':`<span class="badge err" title="${esc(data.error||"")}">Voice server ${esc(data.state||"tắt")}</span>`;
  $("#verr").innerHTML=!data.online&&data.error?alertBox(data.error):"";
  $("#vcount").textContent=data.voices.length;
  if(!data.voices.length){list.innerHTML=empty("mic","Chưa có giọng nào","Thêm giọng đầu tiên bằng form phía trên.");return}
  list.innerHTML='<div class="voices">'+data.voices.map(v=>`<article class="voice" data-id="${esc(v.voice_id)}">
   <header><div class="vav">${I("wave")}</div><div class="vt"><strong title="${esc(v.name)}">${esc(v.name)}</strong>
    <span class="muted sm"><code>${esc(v.voice_id)}</code>${v.duration_s?" · "+Number(v.duration_s).toFixed(1)+" giây":""}${v.sample_rate?" · "+Math.round(v.sample_rate/1000)+" kHz":""}</span></div>
    ${v.ready?'<span class="badge ok">Sẵn sàng</span>':'<span class="badge warn">Chưa sẵn sàng</span>'}</header>
   ${v.warnings.length?alertBox("Cảnh báo mẫu: "+v.warnings.join("; "),"warn"):""}
   <label>Transcript <span class="chip">${v.transcript_mode==="manual"?"Nhập tay":"Whisper"}</span></label>
   <textarea class="tr" maxlength="1000">${esc(v.transcript_mode==="manual"?v.manual_text:v.asr_text)}</textarea>
   <div class="actions"><button class="soft play">${I("play")}Nghe thử</button><button class="save">${I("check")}Lưu</button><button class="asr" title="Bỏ transcript nhập tay, dùng lại kết quả Whisper">${I("refresh")}Dùng lại Whisper</button>
    <span class="spacer"></span><button class="ghost danger del">${I("trash")}Xóa</button></div>
   <div class="out"></div></article>`).join("")+'</div>';
 }catch(e){list.innerHTML=alertBox(e.message)}}
$("#list").addEventListener("click",async ev=>{const b=ev.target.closest("button");if(!b)return;const box=b.closest(".voice");const id=box.dataset.id;const out=box.querySelector(".out");
 b.disabled=true;try{
  if(b.classList.contains("play")){out.innerHTML='<p class="muted"><span class="spin"></span>Đang tạo giọng…</p>';
   const blob=await (await api("POST",`/admin/api/voices/${id}/preview`,{text:$("#sample").value})).blob();
   // Never autoplay: OBS on this PC may capture desktop audio, so playing during a LIVE would reach viewers.
   const url=URL.createObjectURL(blob);out.innerHTML=`<p class="warn">${I("alert")} Đừng bấm phát khi đang LIVE: OBS có thể thu cả loa máy. Có thể tải file về nghe bằng tai nghe hoặc điện thoại.</p>`;
   const a=document.createElement("audio");a.controls=true;a.preload="none";a.src=url;out.append(a);
   const d=document.createElement("a");d.href=url;d.download=`nghe-thu-${id}.wav`;d.textContent="Tải file WAV";out.append(d)}
  if(b.classList.contains("save")){await api("PUT",`/admin/api/voices/${id}/transcript`,{transcript:box.querySelector(".tr").value});toast("Đã lưu transcript.","ok");await load()}
  if(b.classList.contains("asr")){await api("PUT",`/admin/api/voices/${id}/transcript`,{transcript:null});toast("Đã chuyển về transcript Whisper.","ok");await load()}
  if(b.classList.contains("del")&&confirm(`Xóa giọng "${id}"? Khách đang chọn giọng này sẽ chuyển sang giọng Edge. Không hoàn tác được.`)){await api("DELETE",`/admin/api/voices/${id}`);toast(`Đã xóa giọng ${id}.`,"ok");await load()}
 }catch(e){out.innerHTML=alertBox(e.message)}finally{b.disabled=false}});
$("#create").addEventListener("click",async()=>{const f=$("#wav").files[0];if(!f){notice("Chọn file WAV mẫu.","err");return}
 if(f.size>5*1024*1024){notice("File WAV quá lớn (tối đa 5 MB).","err");return}
 const btn=$("#create");btn.disabled=true;notice("Đang tải lên và chuẩn bị giọng (có thể mất một phút)…","muted");
 try{const buf=new Uint8Array(await f.arrayBuffer());let bin="";for(let i=0;i<buf.length;i+=32768)bin+=String.fromCharCode.apply(null,buf.subarray(i,i+32768));
  const v=await (await api("POST","/admin/api/voices",{voice_id:$("#vid").value.trim(),name:$("#name").value.trim(),transcript:$("#tr").value,replace:$("#replace").checked,wav_base64:btoa(bin)})).json();
  notice(`Đã tạo giọng "${v.name}".`,"ok");$("#wav").value="";showFile();$("#tr").value="";idEdited=false;await load()
 }catch(e){notice(e.message,"err")}finally{btn.disabled=false}});
$("#reload").addEventListener("click",load);load();

// --- customers ---
const enc=encodeURIComponent;
function cnotice(t,cls){toast(t,cls)}
const keydlg=$("#keydlg"),devdlg=$("#devdlg");
// The key is shown once: Esc must not dismiss it by accident, only the explicit button.
keydlg.addEventListener("cancel",e=>e.preventDefault());
$("#keyclose").onclick=()=>keydlg.close();
$("#copykey").onclick=()=>navigator.clipboard.writeText($("#keytext").textContent).then(()=>{$("#copykey").innerHTML=I("check")+"Đã chép"},()=>{});
function showKey(email,key){$("#keyemail").textContent=email;$("#keytext").textContent=key;$("#copykey").innerHTML=I("copy")+"Sao chép";keydlg.showModal()}
const isoDate=iso=>{if(!iso)return"";const d=new Date(new Date(iso).getTime()+7*3600e3);return d.toISOString().slice(0,10)};
function left(iso){if(!iso)return'<div class="sm muted">Vô thời hạn</div>';const d=Math.ceil((new Date(iso)-Date.now())/864e5);
 return d<0?`<div class="sm err">Quá hạn ${-d} ngày</div>`:`<div class="sm ${d<=7?"warn":"muted"}">Còn ${d} ngày</div>`}
function avatar(email){let h=0;for(const c of email)h=(h*31+c.charCodeAt(0))%360;return`<span class="av" style="background:hsl(${h} 55% 50%)">${esc((email[0]||"?").toUpperCase())}</span>`}
function applyFilter(){const q=$("#csearch").value.trim().toLowerCase();let shown=0;
 $("#clist").querySelectorAll("tr[data-email]").forEach(r=>{const m=r.dataset.email.includes(q);r.hidden=!m;shown+=m});
 $("#cnone").hidden=!q||shown>0}
$("#csearch").addEventListener("input",applyFilter);
async function loadCustomers(){const body=$("#clist");
 try{const data=await (await api("GET","/admin/api/customers?days=7")).json();const cs=data.customers;
  $("#ccount").textContent=cs.length;$("#s-total").textContent=num(cs.length);
  $("#s-ok").textContent=num(cs.filter(c=>c.status==="hợp lệ").length);$("#s-prio").textContent=num(cs.filter(c=>c.priority).length);
  const req=cs.reduce((s,c)=>s+c.usage.requests,0),chars=cs.reduce((s,c)=>s+c.usage.chars,0);
  $("#s-req").textContent=num(req);$("#s-req-sub").textContent=`Câu TTS 7 ngày · ${num(chars)} ký tự`;
  if(!cs.length){body.innerHTML=`<tr><td colspan="7">${empty("users","Chưa có khách nào","Tạo khách đầu tiên bằng form phía trên.")}</td></tr>`;applyFilter();return}
  body.innerHTML=cs.map(c=>`<tr data-email="${esc(c.email)}" class="${c.enabled?"":"locked"}">
   <td><div class="who">${avatar(c.email)}<span class="em">${esc(c.email)}</span></div></td>
   <td><span class="badge ${c.status==="hợp lệ"?"ok":c.status==="hết hạn"?"warn":"err"}">${esc(c.status)}</span></td>
   <td><div class="dev"><span class="bar ${c.devices>=c.max_devices?"full":""}"><i style="width:${Math.min(100,c.devices/Math.max(1,c.max_devices)*100)}%"></i></span><b>${c.devices}</b><span class="muted">/</span><input class="cmax" type="number" min="1" max="100" value="${c.max_devices}" aria-label="Số máy tối đa"></div></td>
   <td><input class="cexp" type="date" value="${isoDate(c.expires_at)}" aria-label="Ngày hết hạn" title="${esc(c.expires_text)}">${left(c.expires_at)}</td>
   <td><label class="switch"><input class="cprio" type="checkbox" ${c.priority?"checked":""}><span class="t"></span><span class="lb">${c.priority?"Có":"Không"}</span></label></td>
   <td><div><b>${num(c.usage.requests)}</b> câu</div><div class="sm muted">${num(c.usage.chars)} ký tự${c.usage.failures?` · <span class="err">${num(c.usage.failures)} lỗi</span>`:""}</div></td>
   <td><div class="acts"><button class="csave" disabled title="Lưu số máy và ngày hết hạn">${I("check")}Lưu</button><button class="ckey icon" title="Cấp thêm mã kích hoạt" aria-label="Cấp mã">${I("key")}</button><button class="cdev icon" title="Xem các máy đã kích hoạt" aria-label="Máy">${I("monitor")}</button>
    <button class="clock icon ${c.enabled?"danger":"soft"}" data-lock="${c.enabled?1:0}" title="${c.enabled?"Khóa license":"Mở khóa license"}" aria-label="${c.enabled?"Khóa":"Mở khóa"}">${I(c.enabled?"lock":"unlock")}</button></div></td></tr>`).join("");
  applyFilter();
 }catch(e){body.innerHTML=`<tr><td colspan="7" style="padding:16px">${alertBox(e.message)}</td></tr>`}}
$("#ccreate").addEventListener("click",async()=>{const btn=$("#ccreate");btn.disabled=true;
 try{const r=await (await api("POST","/admin/api/customers",{email:$("#cemail").value.trim(),max_devices:Number($("#cmax").value||1),expires:$("#cexp").value||null})).json();
  showKey(r.email,r.activation_key);cnotice(`Đã tạo khách ${r.email}.`,"ok");$("#cemail").value="";await loadCustomers()
 }catch(e){cnotice(e.message,"err")}finally{btn.disabled=false}});
function markDirty(ev){if(!ev.target.matches(".cmax,.cexp"))return;const r=ev.target.closest("tr");r.classList.add("dirty");const s=r.querySelector(".csave");s.disabled=false;s.classList.add("primary")}
$("#clist").addEventListener("input",markDirty);
$("#clist").addEventListener("click",async ev=>{const b=ev.target.closest("button");if(!b)return;const row=b.closest("tr");const email=row.dataset.email;
 b.disabled=true;try{
  if(b.classList.contains("csave")){await api("PATCH",`/admin/api/customers/${enc(email)}`,{max_devices:Number(row.querySelector(".cmax").value),expires:row.querySelector(".cexp").value||null,priority:row.querySelector(".cprio").checked});cnotice(`Đã lưu ${email}.`,"ok");await loadCustomers()}
  if(b.classList.contains("ckey")){const r=await (await api("POST",`/admin/api/customers/${enc(email)}/keys`)).json();showKey(email,r.activation_key)}
  if(b.classList.contains("clock")){const lock=b.dataset.lock==="1";if(!lock||confirm(`Khóa license của ${email}? Studio của khách sẽ bị khóa ở lần kiểm tra tới (phiên đang LIVE vẫn chạy hết).`)){await api("PATCH",`/admin/api/customers/${enc(email)}`,{enabled:!lock});cnotice(`${lock?"Đã khóa":"Đã mở khóa"} ${email}.`,"ok");await loadCustomers()}}
  if(b.classList.contains("cdev"))await showDevices(email);
 }catch(e){cnotice(e.message,"err")}finally{if(b.isConnected&&!(b.classList.contains("csave")&&!row.classList.contains("dirty")))b.disabled=false}});
$("#clist").addEventListener("change",async ev=>{markDirty(ev);const box=ev.target.closest(".cprio");if(!box)return;const email=box.closest("tr").dataset.email;
 try{await api("PATCH",`/admin/api/customers/${enc(email)}`,{priority:box.checked});cnotice(`${box.checked?"Đã bật":"Đã tắt"} ưu tiên cho ${email}.`,"ok");await loadCustomers()}catch(e){cnotice(e.message,"err")}});
async function showDevices(email){const data=await (await api("GET",`/admin/api/customers/${enc(email)}/devices`)).json();const box=$("#devbody");
 $("#devtitle").textContent=`Máy của ${email}`;
 box.innerHTML=data.devices.length?data.devices.map(d=>`<div class="drow" data-agent="${esc(d.agent_id)}"><div class="dic">${I("monitor")}</div>
   <div class="grow"><strong>${esc(d.hostname||"Không rõ tên máy")}</strong><div class="sm muted">${d.platform?esc(d.platform)+" · ":""}Kích hoạt ${esc(d.activated)}</div><code>${esc(d.agent_id)}</code></div>
   ${d.enabled?'<span class="badge ok">Đang dùng</span><button class="danger drevoke">Gỡ máy</button>':'<span class="badge muted">Đã gỡ</span>'}</div>`).join("")
  :empty("monitor","Chưa có máy nào","Khách này chưa kích hoạt máy nào.");
 box.querySelectorAll(".drevoke").forEach(btn=>btn.onclick=async()=>{const id=btn.closest(".drow").dataset.agent;
  if(!confirm("Gỡ máy này? Máy đó phải nhập mã kích hoạt mới, và khách được trả lại một suất máy."))return;
  btn.disabled=true;try{await api("DELETE",`/admin/api/devices/${enc(id)}`);cnotice("Đã gỡ máy.","ok");await showDevices(email);await loadCustomers()}catch(e){cnotice(e.message,"err");btn.disabled=false}});
 if(!devdlg.open)devdlg.showModal()}
$("#creload").addEventListener("click",loadCustomers);loadCustomers();
</script></body></html>"""
