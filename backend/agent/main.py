"""Windows agent: outbound HTTPS, CAPTCHA monitoring, local OBS playback and voice actions.

Threads (all outbound, no inbound port):
  - command thread: long-polls GET /agents/commands/{id}/next?wait=25 and acks each result;
  - main loop: heartbeat every 2 s while playing (5 s idle) with `playback` = engine.status(),
    and flushes playback events to POST /agents/events as soon as they happen;
  - media thread: rescans local folders, syncs metadata/thumbnails to /agent-library/sync;
  - engine watchdog (app.playback_engine) advances videos and recovers OBS stalls.
"""
import json
import platform
import signal
import sys
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from app.config import settings
from app.database import DATA_DIR
from app.playback_engine import PlaybackEngine, PlaybackError
from agent.credentials import load as load_credential
from app.verification_monitor import start, stop, status, configure, ConfigBody
from agent.local_library import scan as scan_media, resolve as resolve_media, thumb_base64

LABELS={
    "idle":"Chưa bật giám sát","starting":"Đang khởi động",
    "watching":"Đang quét màn hình, chưa thấy CAPTCHA",
    "captcha_detected":"Phát hiện CAPTCHA, cần kiểm tra",
    "window_unavailable":"Chưa tìm thấy cửa sổ TikTok đang hiển thị",
    "template_missing_or_invalid":"Không có mẫu nhận diện hợp lệ",
    "template_missing":"Chưa có ảnh mẫu","stopped":"Đã dừng giám sát",
    "error":"Có lỗi khi giám sát"
}
POLL_WAIT_SECONDS=25
HEARTBEAT_PLAYING=2
HEARTBEAT_IDLE=5
MEDIA_SYNC_SECONDS=60
THUMBS_PER_SYNC=100
RESULT_LIMIT=60000
# Commands that may take seconds run off the command thread so playback control stays responsive.
SLOW_KINDS={"tts_speak","media_rescan"}

def request_json(url,method,key,data=None,timeout=12):
    body=json.dumps(data,ensure_ascii=False).encode("utf-8") if data is not None else None
    req=urllib.request.Request(url,data=body,method=method,headers={
        "Content-Type":"application/json","X-Sarae-Agent-Key":key})
    with urllib.request.urlopen(req,timeout=timeout) as response:
        return json.load(response)

class EventOutbox:
    """Playback events waiting for POST /agents/events. Seq starts at the clock in ms, so it keeps
    increasing across Agent restarts and the server can drop re-sent duplicates."""
    def __init__(self,limit=1000):
        self.lock=threading.Lock()
        self.items=[]
        self.limit=limit
        self.seq=int(time.time()*1000)
        self.ready=threading.Event()

    def push(self,event:dict):
        with self.lock:
            self.seq+=1
            self.items.append({**event,"seq":self.seq})
            del self.items[:-self.limit]
        self.ready.set()

    def flush(self,server,key,agent_id):
        with self.lock:
            batch=self.items[:200]
        if not batch:return 0
        request_json(server+"/agents/events","POST",key,{"agent_id":agent_id,"events":batch})
        sent={e["seq"] for e in batch}
        with self.lock:
            self.items=[e for e in self.items if e["seq"] not in sent]
            if not self.items:self.ready.clear()
        return len(batch)

outbox=EventOutbox()
engine=PlaybackEngine(resolve=resolve_media,on_event=outbox.push)

class MediaSync:
    def __init__(self):
        self.lock=threading.Lock()
        self.need_thumbs=None  # None = unknown: offer every cached thumbnail once
        self.last_count=0

    def run(self,server,key,agent_id):
        with self.lock:
            files=scan_media()
            videos=[];sent=0
            for item in files:
                video={k:item[k] for k in ("media_id","filename","size_bytes","modified_ns")}
                if item.get("duration_seconds") is not None:video["duration_seconds"]=item["duration_seconds"]
                wanted=self.need_thumbs is None or item["media_id"] in self.need_thumbs
                if wanted and sent<THUMBS_PER_SYNC:
                    data=thumb_base64(item["media_id"])
                    if data:video["thumb_jpeg_base64"]=data;sent+=1
                videos.append(video)
            reply=request_json(server+"/agent-library/sync","POST",key,
                               {"agent_id":agent_id,"videos":videos},timeout=60)
            missing=set(reply.get("thumbs_missing") or [])
            # Only ask again for thumbnails that exist locally; others are not probed yet or failed.
            self.need_thumbs={m for m in missing if thumb_base64(m) is not None}
            self.last_count=len(files)
            return {"count":len(files),"thumbs_sent":sent,"thumbs_pending":len(self.need_thumbs)}

media_sync=MediaSync()

def _compat_local(play):
    """Old `local_playback` heartbeat shape still read by the web Agent page."""
    return {"active":play.get("running",False),"media_ids":play.get("queue_ids",[]),
            "index":play.get("index",0),"count":play.get("count",0),"loop":play.get("loop",False),
            "paused":play.get("paused",False),"needs_manual_resume":False}

def execute(command,context=None):
    kind=command["kind"];payload=command.get("payload") or {}
    if kind=="monitor_start":return start()
    if kind=="monitor_stop":return stop()
    if kind=="monitor_config":return configure(ConfigBody.model_validate(payload))
    if kind=="local_media_play":return engine.start_playlist([payload["media_id"]],loop=False)
    if kind=="local_playlist_start":return engine.start_playlist(payload["media_ids"],loop=payload.get("loop",True))
    if kind in ("local_media_next","obs_next","playback_next"):return engine.next()
    if kind in ("local_media_stop","obs_stop","playback_stop"):return engine.stop()
    if kind in ("obs_pause","playback_pause"):return engine.pause()
    if kind in ("obs_resume","playback_resume"):return engine.resume()
    if kind in ("obs_previous","playback_previous"):return engine.previous()
    if kind=="obs_status":return engine.status()
    if kind=="product_play":
        return engine.product_play(payload["session_id"],payload["product_id"],payload["media_ids"],
                                   payload.get("mode","immediate"))
    if kind=="playback_cancel_pending":return engine.cancel_pending()
    if kind=="playback_select":return engine.select(payload["index"])
    if kind=="playback_seek":return engine.seek(payload["ms"])
    if kind=="obs_configure":
        engine.configure(**{k:payload.get(k) for k in ("host","port","password","input_name",
                                                        "dual_mode","source_a","source_b") if k in payload})
        # Status carries has_password only; the password itself is never echoed back.
        return engine.status()
    if kind=="obs_inputs":
        return engine.list_inputs(payload.get("host","127.0.0.1"),payload.get("port",4455),payload.get("password",""))
    if kind=="media_rescan":
        if not context:raise ValueError("Agent chưa kết nối Cloud")
        return media_sync.run(*context)
    if kind=="tts_speak":
        from agent.tts import speak
        return speak(engine,payload)
    raise ValueError("Lệnh không được hỗ trợ")

class CommandJournal:
    """Commands this Agent has executed (id → outcome), kept on disk so a command delivered again — its ack was
    lost, the Cloud re-delivered a retryable command, or the Agent restarted — is acked with the first outcome
    instead of being executed twice."""

    def __init__(self,path,limit=500):
        self.path=path;self.limit=limit;self.lock=threading.Lock()
        try:self.entries=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,ValueError):self.entries={}

    def get(self,command_id):
        with self.lock:return self.entries.get(command_id)

    def record(self,command_id,success,result):
        with self.lock:
            self.entries[command_id]={"success":success,"result":result,"at":time.time()}
            while len(self.entries)>self.limit:
                self.entries.pop(next(iter(self.entries)))
            tmp=self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.entries,ensure_ascii=False),encoding="utf-8")
            tmp.replace(self.path)

journal=CommandJournal(DATA_DIR/"agent_command_journal.json")
ACK_RETRY_DELAYS=(1,2,4,8,15)

def ack(context,command,success,result):
    """Ack with the claim token; retry on network errors (a late ack is still accepted by the Cloud)."""
    server,key,agent_id=context
    body={"agent_id":agent_id,"command_id":command["command_id"],"claim_token":command.get("claim_token",""),
          "success":success,"result":result}
    for delay in (0,)+ACK_RETRY_DELAYS:
        if delay:time.sleep(delay)
        try:
            request_json(server+"/agents/commands/ack","POST",key,body)
            return True
        except urllib.error.HTTPError as exc:
            # 404/409: the Cloud no longer accepts this ack (cancelled, unknown token); retrying cannot help.
            print("Cloud từ chối xác nhận lệnh:",exc.code)
            return False
        except Exception as exc:
            print("Không gửi được xác nhận lệnh, thử lại:",str(exc)[:160])
    return False

def handle(command,context):
    """Run one command (once, see CommandJournal) and ack it with its JSON result or the error message."""
    print(f"[{time.strftime('%H:%M:%S')}] Nhận lệnh từ Web: {command['kind']} (lần {command.get('attempt',1)})")
    done=journal.get(command["command_id"])
    if done is not None:
        print("Lệnh đã thực thi trước đó; chỉ gửi lại kết quả")
        ack(context,command,done["success"],done["result"])
        return
    success=True
    try:
        result=json.dumps(execute(command,context),ensure_ascii=False,default=str)
        if len(result)>RESULT_LIMIT:
            result=json.dumps({"truncated":True,"preview":result[:1000]},ensure_ascii=False)
    except PlaybackError as exc:
        success=False;result=str(exc.detail)[:1900]
    except Exception as exc:
        success=False;result=str(exc)[:1900]
    # Record before acking: if the ack is lost, a re-delivery must not run the action again.
    journal.record(command["command_id"],success,result)
    ack(context,command,success,result)
    print("Thực thi thành công:" if success else "Thực thi thất bại:",result[:200])

def acquire_command_lock():
    """Only one process per PC may take commands from the Cloud (OS file lock, released when the process exits)."""
    handle=open(DATA_DIR/"agent_commands.lock","a+")
    try:
        handle.seek(0)
        if sys.platform=="win32":
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None

def command_loop(context,stop_event):
    server,key,agent_id=context
    url=server+"/agents/commands/"+urllib.parse.quote(agent_id)+f"/next?wait={POLL_WAIT_SECONDS}"
    while not stop_event.is_set():
        try:
            pending=request_json(url,"GET",key,timeout=POLL_WAIT_SECONDS+10).get("command")
        except Exception as exc:
            print(f"[{time.strftime('%H:%M:%S')}] Kênh lệnh mất kết nối, thử lại: {str(exc)[:160]}")
            stop_event.wait(3)
            continue
        if not pending:continue
        if pending["kind"] in SLOW_KINDS:
            threading.Thread(target=handle,args=(pending,context),daemon=True,
                             name="SaraeAgentSlowCommand").start()
        else:
            handle(pending,context)

def media_loop(context,stop_event):
    while not stop_event.is_set():
        delay=MEDIA_SYNC_SECONDS
        try:
            result=media_sync.run(*context)
            print(f"Đã đồng bộ {result['count']} video local lên danh mục Cloud")
            if result["thumbs_pending"]:delay=5
        except Exception as exc:
            print("Đồng bộ danh mục video thất bại:",str(exc)[:160])
        stop_event.wait(delay)

def main(external_stop:threading.Event|None=None):
    server=settings.sarae_cloud_api_url.rstrip("/")
    if not server:raise SystemExit("Chưa cấu hình SARAE_CLOUD_API_URL")
    try:credential_data=load_credential()
    except Exception as exc:raise SystemExit("Chưa kích hoạt hoặc không đọc được credential: "+str(exc))
    agent_key=credential_data["agent_credential"]
    if not server.startswith(("https://","http://localhost:","http://127.0.0.1:")):
        raise SystemExit("Chỉ cho phép HTTPS; HTTP chỉ dùng ở localhost")
    agent_id=credential_data["agent_id"]
    stop_event=external_stop if external_stop is not None else threading.Event()
    if external_stop is None:
        for sig in (signal.SIGINT,signal.SIGTERM):
            try:signal.signal(sig,lambda *_:stop_event.set())
            except (OSError,ValueError):pass
    if settings.sarae_agent_autostart_monitor:
        try:print("Khởi động giám sát CAPTCHA:",start())
        except Exception as exc:print("Không bật được giám sát CAPTCHA:",exc)
    print(f"Agent Windows [{agent_id}] đang kết nối đến {server}")
    context=(server,agent_key,agent_id)
    # The watchdog keeps running when the cloud link stops: an ongoing LIVE must not freeze.
    engine.start_monitor()
    command_lock=acquire_command_lock()
    if command_lock is None:
        raise SystemExit("Một tiến trình Sarae Agent khác đang xử lý lệnh trên máy này; không chạy song song để tránh thực thi trùng")
    for target,name in ((command_loop,"SaraeAgentCommands"),(media_loop,"SaraeAgentMedia")):
        threading.Thread(target=target,args=(context,stop_event),daemon=True,name=name).start()
    last_label=None
    next_heartbeat=0.0
    retry_events_at=0.0
    while not stop_event.is_set():
        if outbox.ready.is_set() and time.monotonic()>=retry_events_at:
            try:
                outbox.flush(server,agent_key,agent_id)
            except Exception as exc:
                retry_events_at=time.monotonic()+3
                print("Không gửi được sự kiện phát:",str(exc)[:160])
        if time.monotonic()>=next_heartbeat:
            play={}
            try:
                local=status()
                st=local["state"]["status"]
                play=engine.status()
                obs_snapshot={k:play.get(k) for k in ("connected","running","paused","media_state","product_id","index","count","last_status","error")}
                report={"agent_id":agent_id,"hostname":platform.node(),
                        "verification":{"state":local["state"],"config":local["config"],
                                        "events":local["events"][:10]},
                        "obs":obs_snapshot,"local_playback":_compat_local(play),"playback":play}
                request_json(server+"/agents/heartbeat","POST",agent_key,report)
                label=LABELS.get(st,st)
                if label!=last_label:
                    print(f"[{time.strftime('%H:%M:%S')}] Đã đồng bộ VPS | CAPTCHA: {label}")
                    last_label=label
            except Exception as exc:
                print(f"[{time.strftime('%H:%M:%S')}] Mất kết nối Backend, sẽ thử lại: {str(exc)[:200]}")
            next_heartbeat=time.monotonic()+(HEARTBEAT_PLAYING if play.get("running") else HEARTBEAT_IDLE)
        # Wake early for new playback events; otherwise sleep until the next heartbeat (max 1 s for stop).
        timeout=max(0.05,min(1.0,next_heartbeat-time.monotonic()))
        if retry_events_at>time.monotonic():
            stop_event.wait(min(timeout,retry_events_at-time.monotonic()))
        else:
            outbox.ready.wait(timeout)

if __name__=="__main__":
    main()
