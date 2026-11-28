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
:root{--bg:#f6f7f9;--card:#fff;--text:#1b1f24;--muted:#626b76;--line:#dde1e6;--accent:#0b6bcb;--danger:#c62828;--ok:#2e7d32;--warn:#b26a00}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--card:#1a1f25;--text:#e6e9ed;--muted:#9aa4ae;--line:#2c333b;--accent:#5aa6f0;--danger:#ef6b6b;--ok:#6cc070;--warn:#e0a040}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px}h1{font-size:20px;margin:0 0 4px}p.sub{color:var(--muted);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
h2{font-size:15px;margin:0 0 12px}label{display:block;font-weight:600;margin:10px 0 4px}
input[type=text],textarea{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--text);font:inherit}
textarea{min-height:60px;resize:vertical}.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:640px){.row{grid-template-columns:1fr}}
button{font:inherit;padding:7px 12px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}button.danger{color:var(--danger)}
button:disabled{opacity:.5;cursor:default}.muted{color:var(--muted)}.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid currentColor}
.ok{color:var(--ok)}.warn{color:var(--warn)}.err{color:var(--danger)}
.voice{border-top:1px solid var(--line);padding:14px 0}.voice:first-child{border-top:0}
.voice header{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.voice header strong{font-size:15px}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}#notice{min-height:20px;margin:8px 0 0}
audio{width:100%;margin-top:8px}code{font-size:12px}
.tabs{display:flex;gap:4px;margin:0 0 16px;border-bottom:1px solid var(--line)}
.tabs button{border:0;border-bottom:2px solid transparent;border-radius:0;background:none;padding:8px 14px;color:var(--muted)}
.tabs button[aria-selected=true]{color:var(--text);border-bottom-color:var(--accent);font-weight:600}
.row3{display:grid;grid-template-columns:2fr 1fr 1fr;gap:12px}@media (max-width:640px){.row3{grid-template-columns:1fr}}
input[type=number],input[type=date]{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--text);font:inherit}
.table{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:8px 6px;border-top:1px solid var(--line);vertical-align:middle;white-space:nowrap}
th{color:var(--muted);font-weight:600;border-top:0}td input[type=number]{width:64px;padding:4px 6px}td input[type=date]{width:150px;padding:4px 6px}
.keybox{margin-top:12px;padding:10px 12px;border:1px dashed var(--accent);border-radius:6px;word-break:break-all}
.switch{display:inline-flex;align-items:center;gap:6px;cursor:pointer}
</style></head><body><main>
<h1>Sarae Admin</h1>
<p class="sub">Chỉ mở trên PC server. <span id="server"></span></p>
<nav class="tabs" role="tablist"><button role="tab" data-tab="customers" aria-selected="true">Khách hàng</button><button role="tab" data-tab="voices" aria-selected="false">Giọng clone</button></nav>

<div data-panel="customers">
<section class="card"><h2>Thêm khách</h2>
<div class="row3"><div><label for="cemail">Email</label><input id="cemail" type="text" maxlength="254" placeholder="khach@shop.vn"></div>
<div><label for="cmax">Số máy tối đa</label><input id="cmax" type="number" min="1" max="100" value="1"></div>
<div><label for="cexp">Hết hạn <span class="muted">(để trống = vô thời hạn)</span></label><input id="cexp" type="date"></div></div>
<div class="actions"><button class="primary" id="ccreate">Tạo khách + cấp mã</button></div>
<div id="ckey"></div><p id="cnotice" role="status"></p></section>

<section class="card"><h2>Danh sách khách <button id="creload" style="margin-left:8px">Làm mới</button></h2>
<p class="muted" style="margin-top:-6px">Ưu tiên: câu TTS của tài khoản này được tạo trước các câu đang chờ của khách khác. Usage tính 7 ngày gần nhất.</p>
<div class="table"><table><thead><tr><th>Email</th><th>Trạng thái</th><th>Máy</th><th>Hết hạn</th><th>Ưu tiên</th><th>Usage 7 ngày</th><th></th></tr></thead><tbody id="clist"><tr><td colspan="7" class="muted">Đang tải…</td></tr></tbody></table></div>
<div id="cdetail"></div></section>
</div>

<div data-panel="voices" hidden>
<p class="muted">Mọi khách có license đều dùng được các giọng ở đây.</p>

<section class="card"><h2>Thêm giọng mới</h2>
<div class="row"><div><label for="name">Tên hiển thị</label><input id="name" type="text" maxlength="100" placeholder="Giọng Linh Phương"></div>
<div><label for="vid">Mã giọng</label><input id="vid" type="text" maxlength="64" placeholder="linh_phuong"></div></div>
<label for="wav">File WAV mẫu <span class="muted">(1 đến 20 giây, giọng rõ, không nhạc nền, tối đa 5 MB)</span></label><input id="wav" type="file" accept=".wav,audio/wav">
<label for="tr">Transcript <span class="muted">(không bắt buộc, để trống thì server tự nhận dạng bằng Whisper)</span></label><textarea id="tr" maxlength="1000"></textarea>
<label style="font-weight:400"><input id="replace" type="checkbox"> Thay giọng cũ nếu mã đã tồn tại</label>
<div class="actions"><button class="primary" id="create">Tạo giọng</button></div>
<p id="notice" role="status"></p></section>

<section class="card"><h2>Danh sách giọng <button id="reload" style="margin-left:8px">Làm mới</button></h2>
<label for="sample">Câu nghe thử</label><input id="sample" type="text" maxlength="450" value="Dạ chị ơi, mẫu này còn đủ size, chị cho em xin chiều cao cân nặng để em tư vấn nha.">
<div id="list" style="margin-top:12px"><p class="muted">Đang tải…</p></div></section>
</div>
</main><script>
const H={"X-Sarae-Admin":"1","Content-Type":"application/json"};
const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function notice(t,cls){const n=$("#notice");n.textContent=t;n.className=cls||""}
async function api(method,path,body){const r=await fetch(path,{method,headers:H,body:body?JSON.stringify(body):undefined});
 if(!r.ok){let d="HTTP "+r.status;try{d=(await r.json()).detail||d}catch(e){}throw new Error(d)}return r}
function slug(s){return s.normalize("NFD").replace(/[̀-ͯ]/g,"").replace(/đ/g,"d").replace(/Đ/g,"D").toLowerCase().replace(/[^a-z0-9]+/g,"_").replace(/^_+|_+$/g,"").slice(0,64)}
let idEdited=false;$("#vid").addEventListener("input",()=>idEdited=true);
$("#name").addEventListener("input",()=>{if(!idEdited)$("#vid").value=slug($("#name").value)});
async function load(){const list=$("#list");
 try{const data=await (await api("GET","/admin/api/voices")).json();
  $("#server").innerHTML=data.online?'<span class="pill ok">Voice server sẵn sàng</span>':'<span class="pill err">Voice server '+esc(data.state||"tắt")+'</span> '+esc(data.error||"");
  if(!data.voices.length){list.innerHTML='<p class="muted">Chưa có giọng nào.</p>';return}
  list.innerHTML=data.voices.map(v=>`<div class="voice" data-id="${esc(v.voice_id)}"><header><strong>${esc(v.name)}</strong><code>${esc(v.voice_id)}</code>
   ${v.ready?'<span class="pill ok">sẵn sàng</span>':'<span class="pill warn">chưa sẵn sàng</span>'}
   <span class="muted">${v.duration_s?Number(v.duration_s).toFixed(1)+" giây":""}</span></header>
   ${v.warnings.length?`<p class="warn">Cảnh báo mẫu: ${v.warnings.map(esc).join("; ")}</p>`:""}
   <label>Transcript <span class="muted">(${v.transcript_mode==="manual"?"nhập tay":"Whisper"})</span></label>
   <textarea class="tr" maxlength="1000">${esc(v.transcript_mode==="manual"?v.manual_text:v.asr_text)}</textarea>
   <div class="actions"><button class="play">Nghe thử</button><button class="save">Lưu transcript</button><button class="asr">Dùng lại Whisper</button><button class="danger del">Xóa</button></div>
   <div class="out"></div></div>`).join("");
 }catch(e){list.innerHTML='<p class="err">'+esc(e.message)+'</p>'}}
$("#list").addEventListener("click",async ev=>{const b=ev.target.closest("button");if(!b)return;const box=b.closest(".voice");const id=box.dataset.id;const out=box.querySelector(".out");
 b.disabled=true;try{
  if(b.classList.contains("play")){out.innerHTML='<p class="muted">Đang tạo giọng…</p>';
   const blob=await (await api("POST",`/admin/api/voices/${id}/preview`,{text:$("#sample").value})).blob();
   // Never autoplay: OBS on this PC may capture desktop audio, so playing during a LIVE would reach viewers.
   const url=URL.createObjectURL(blob);out.innerHTML='<p class="warn">Đừng bấm phát khi đang LIVE: OBS có thể thu cả loa máy. Có thể tải file về nghe bằng tai nghe hoặc điện thoại.</p>';
   const a=document.createElement("audio");a.controls=true;a.preload="none";a.src=url;out.append(a);
   const d=document.createElement("a");d.href=url;d.download=`nghe-thu-${id}.wav`;d.textContent="Tải file WAV";out.append(d)}
  if(b.classList.contains("save")){await api("PUT",`/admin/api/voices/${id}/transcript`,{transcript:box.querySelector(".tr").value});await load()}
  if(b.classList.contains("asr")){await api("PUT",`/admin/api/voices/${id}/transcript`,{transcript:null});await load()}
  if(b.classList.contains("del")&&confirm(`Xóa giọng "${id}"? Khách đang chọn giọng này sẽ chuyển sang giọng Edge. Không hoàn tác được.`)){await api("DELETE",`/admin/api/voices/${id}`);await load()}
 }catch(e){out.innerHTML='<p class="err">'+esc(e.message)+'</p>'}finally{b.disabled=false}});
$("#create").addEventListener("click",async()=>{const f=$("#wav").files[0];if(!f){notice("Chọn file WAV mẫu.","err");return}
 if(f.size>5*1024*1024){notice("File WAV quá lớn (tối đa 5 MB).","err");return}
 const btn=$("#create");btn.disabled=true;notice("Đang tải lên và chuẩn bị giọng (có thể mất một phút)…","muted");
 try{const buf=new Uint8Array(await f.arrayBuffer());let bin="";for(let i=0;i<buf.length;i+=32768)bin+=String.fromCharCode.apply(null,buf.subarray(i,i+32768));
  const v=await (await api("POST","/admin/api/voices",{voice_id:$("#vid").value.trim(),name:$("#name").value.trim(),transcript:$("#tr").value,replace:$("#replace").checked,wav_base64:btoa(bin)})).json();
  notice(`Đã tạo giọng "${v.name}".`,"ok");$("#wav").value="";$("#tr").value="";idEdited=false;await load()
 }catch(e){notice(e.message,"err")}finally{btn.disabled=false}});
$("#reload").addEventListener("click",load);load();

// --- tabs ---
document.querySelectorAll(".tabs button").forEach(b=>b.addEventListener("click",()=>{
 document.querySelectorAll(".tabs button").forEach(x=>x.setAttribute("aria-selected",x===b));
 document.querySelectorAll("[data-panel]").forEach(p=>p.hidden=p.dataset.panel!==b.dataset.tab)}));

// --- customers ---
const enc=encodeURIComponent;
function cnotice(t,cls){const n=$("#cnotice");n.textContent=t;n.className=cls||""}
function showKey(email,key){$("#ckey").innerHTML=`<div class="keybox"><strong>Mã kích hoạt cho ${esc(email)}</strong> (chỉ hiện một lần, dùng cho một máy):<br><code id="keytext">${esc(key)}</code> <button id="copykey">Sao chép</button></div>`;
 $("#copykey").onclick=()=>navigator.clipboard.writeText(key).then(()=>$("#copykey").textContent="Đã chép",()=>{})}
const isoDate=iso=>{if(!iso)return"";const d=new Date(new Date(iso).getTime()+7*3600e3);return d.toISOString().slice(0,10)};
async function loadCustomers(){const body=$("#clist");
 try{const data=await (await api("GET","/admin/api/customers?days=7")).json();
  if(!data.customers.length){body.innerHTML='<tr><td colspan="7" class="muted">Chưa có khách nào.</td></tr>';return}
  body.innerHTML=data.customers.map(c=>`<tr data-email="${esc(c.email)}">
   <td>${esc(c.email)}</td>
   <td><span class="pill ${c.status==="hợp lệ"?"ok":c.status==="hết hạn"?"warn":"err"}">${esc(c.status)}</span></td>
   <td>${c.devices} / <input class="cmax" type="number" min="1" max="100" value="${c.max_devices}" aria-label="Số máy tối đa"></td>
   <td><input class="cexp" type="date" value="${isoDate(c.expires_at)}" aria-label="Ngày hết hạn" title="${esc(c.expires_text)}"></td>
   <td><label class="switch"><input class="cprio" type="checkbox" ${c.priority?"checked":""}> ${c.priority?"Có":"Không"}</label></td>
   <td class="muted">${c.usage.requests} câu · ${c.usage.chars} ký tự${c.usage.failures?` · ${c.usage.failures} lỗi`:""}</td>
   <td><button class="csave">Lưu</button> <button class="ckey">Cấp mã</button> <button class="cdev">Máy</button> <button class="${c.enabled?"danger ":""}clock">${c.enabled?"Khóa":"Mở khóa"}</button></td></tr>`).join("");
 }catch(e){body.innerHTML=`<tr><td colspan="7" class="err">${esc(e.message)}</td></tr>`}}
$("#ccreate").addEventListener("click",async()=>{const btn=$("#ccreate");btn.disabled=true;cnotice("");
 try{const r=await (await api("POST","/admin/api/customers",{email:$("#cemail").value.trim(),max_devices:Number($("#cmax").value||1),expires:$("#cexp").value||null})).json();
  showKey(r.email,r.activation_key);cnotice(`Đã tạo khách ${r.email}.`,"ok");$("#cemail").value="";await loadCustomers()
 }catch(e){cnotice(e.message,"err")}finally{btn.disabled=false}});
$("#clist").addEventListener("click",async ev=>{const b=ev.target.closest("button");if(!b)return;const row=b.closest("tr");const email=row.dataset.email;
 b.disabled=true;cnotice("");try{
  if(b.classList.contains("csave")){await api("PATCH",`/admin/api/customers/${enc(email)}`,{max_devices:Number(row.querySelector(".cmax").value),expires:row.querySelector(".cexp").value||null,priority:row.querySelector(".cprio").checked});cnotice(`Đã lưu ${email}.`,"ok");await loadCustomers()}
  if(b.classList.contains("ckey")){const r=await (await api("POST",`/admin/api/customers/${enc(email)}/keys`)).json();showKey(email,r.activation_key)}
  if(b.classList.contains("clock")){const lock=b.textContent==="Khóa";if(!lock||confirm(`Khóa license của ${email}? Studio của khách sẽ bị khóa ở lần kiểm tra tới (phiên đang LIVE vẫn chạy hết).`)){await api("PATCH",`/admin/api/customers/${enc(email)}`,{enabled:!lock});await loadCustomers()}}
  if(b.classList.contains("cdev"))await showDevices(email);
 }catch(e){cnotice(e.message,"err")}finally{b.disabled=false}});
$("#clist").addEventListener("change",async ev=>{const box=ev.target.closest(".cprio");if(!box)return;const email=box.closest("tr").dataset.email;
 try{await api("PATCH",`/admin/api/customers/${enc(email)}`,{priority:box.checked});cnotice(`${box.checked?"Đã bật":"Đã tắt"} ưu tiên cho ${email}.`,"ok");await loadCustomers()}catch(e){cnotice(e.message,"err")}});
async function showDevices(email){const box=$("#cdetail");const data=await (await api("GET",`/admin/api/customers/${enc(email)}/devices`)).json();
 box.innerHTML=`<h2 style="margin-top:16px">Máy của ${esc(email)}</h2>`+(data.devices.length?`<div class="table"><table><thead><tr><th>Tên máy</th><th>Mã máy</th><th>Kích hoạt</th><th>Trạng thái</th><th></th></tr></thead><tbody>${data.devices.map(d=>`<tr data-agent="${esc(d.agent_id)}"><td>${esc(d.hostname)}</td><td><code>${esc(d.agent_id)}</code></td><td>${esc(d.activated)}</td><td>${d.enabled?'<span class="pill ok">đang dùng</span>':'<span class="pill">đã gỡ</span>'}</td><td>${d.enabled?'<button class="danger drevoke">Gỡ máy</button>':""}</td></tr>`).join("")}</tbody></table></div>`:'<p class="muted">Khách này chưa kích hoạt máy nào.</p>');
 box.querySelectorAll(".drevoke").forEach(btn=>btn.onclick=async()=>{const id=btn.closest("tr").dataset.agent;
  if(!confirm("Gỡ máy này? Máy đó phải nhập mã kích hoạt mới, và khách được trả lại một suất máy."))return;
  try{await api("DELETE",`/admin/api/devices/${enc(id)}`);await showDevices(email);await loadCustomers()}catch(e){cnotice(e.message,"err")}})}
$("#creload").addEventListener("click",loadCustomers);loadCustomers();
</script></body></html>"""
