"""Windows agent: outbound HTTPS, CAPTCHA monitoring, local OBS and voice actions."""
import json
import platform
import signal
import threading
import time
import urllib.request
from app.config import settings
from app.verification_monitor import start, stop, status, configure, ConfigBody
from app.ai_voice import speak, SpeakBody
from app.live import state as obs_state

LABELS={
    "idle":"Chưa bật giám sát","starting":"Đang khởi động",
    "watching":"Đang quét màn hình, chưa thấy CAPTCHA",
    "captcha_detected":"Phát hiện CAPTCHA, cần kiểm tra",
    "window_unavailable":"Chưa tìm thấy cửa sổ TikTok đang hiển thị",
    "template_missing_or_invalid":"Không có mẫu nhận diện hợp lệ",
    "template_missing":"Chưa có ảnh mẫu","stopped":"Đã dừng giám sát",
    "error":"Có lỗi khi giám sát"
}
def request_json(url,method,key,data=None):
    body=json.dumps(data,ensure_ascii=False).encode("utf-8") if data is not None else None
    req=urllib.request.Request(url,data=body,method=method,headers={
        "Content-Type":"application/json","X-Sarae-Agent-Key":key})
    with urllib.request.urlopen(req,timeout=12) as response:
        return json.load(response)

def execute(command):
    kind=command["kind"];payload=command.get("payload") or {}
    if kind=="monitor_start":return start()
    if kind=="monitor_stop":return stop()
    if kind=="monitor_config":return configure(ConfigBody.model_validate(payload))
    if kind=="obs_status":return obs_state.status()
    if kind in ("obs_pause","obs_resume","obs_next","obs_previous","obs_stop"):
        from app.live import pause_video, resume_video, next_video, previous_video, stop_video
        handlers={"obs_pause":pause_video,"obs_resume":resume_video,
                  "obs_next":next_video,"obs_previous":previous_video,"obs_stop":stop_video}
        return handlers[kind]()
    if kind=="tts_speak":return speak(SpeakBody.model_validate(payload))
    raise ValueError("Lệnh không được hỗ trợ")

def main():
    server=settings.sarae_cloud_api_url.rstrip("/")
    if not server:raise SystemExit("Chưa cấu hình SARAE_CLOUD_API_URL")
    if not settings.sarae_agent_key:raise SystemExit("Chưa cấu hình SARAE_AGENT_KEY")
    if not server.startswith(("https://","http://localhost:","http://127.0.0.1:")):
        raise SystemExit("Chỉ cho phép HTTPS; HTTP chỉ dùng ở localhost")
    agent_id=settings.sarae_agent_id or platform.node() or "windows-agent"
    stop_event=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):
        try:signal.signal(sig,lambda *_:stop_event.set())
        except (OSError,ValueError):pass
    if settings.sarae_agent_autostart_monitor:
        try:print("Khởi động giám sát CAPTCHA:",start())
        except Exception as exc:print("Không bật được giám sát CAPTCHA:",exc)
    print(f"Agent Windows [{agent_id}] đang kết nối đến {server}")
    last_label=None
    while not stop_event.is_set():
        try:
            local=status()
            st=local["state"]["status"]
            report={"agent_id":agent_id,"hostname":platform.node(),
                    "verification":{"state":local["state"],"config":local["config"],
                                    "events":local["events"][:10]}}
            request_json(server+"/agents/heartbeat","POST",settings.sarae_agent_key,report)
            label=LABELS.get(st,st)
            if label!=last_label:
                print(f"[{time.strftime('%H:%M:%S')}] Đã đồng bộ VPS | CAPTCHA: {label}")
                last_label=label
            pending=request_json(server+"/agents/commands/"+agent_id+"/next","GET",settings.sarae_agent_key).get("command")
            if pending:
                print(f"[{time.strftime('%H:%M:%S')}] Nhận lệnh từ Web: {pending['kind']}")
                success=True
                try:
                    result=json.dumps(execute(pending),ensure_ascii=False,default=str)[:1900]
                except Exception as exc:
                    success=False;result=str(exc)[:1900]
                try:
                    request_json(server+"/agents/commands/ack","POST",settings.sarae_agent_key,
                                 {"agent_id":agent_id,"command_id":pending["command_id"],
                                  "success":success,"result":result})
                except Exception as exc:print("Không xác nhận được kết quả lệnh:",exc)
                print("Thực thi thành công:" if success else "Thực thi thất bại:",result[:200])
        except Exception as exc:
            print(f"[{time.strftime('%H:%M:%S')}] Mất kết nối Backend, sẽ thử lại: {str(exc)[:200]}")
        stop_event.wait(5)

if __name__=="__main__":
    main()
