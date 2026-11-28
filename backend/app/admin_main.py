"""Admin voice page for the home PC: `uvicorn app.admin_main:app --host 127.0.0.1 --port 8001` (start-admin.cmd).

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


PAGE = r"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quản lý giọng Sarae</title>
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
</style></head><body><main>
<h1>Quản lý giọng clone</h1>
<p class="sub">Chỉ mở trên PC server. Mọi khách có license đều dùng được các giọng ở đây. <span id="server"></span></p>

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
</script></body></html>"""
