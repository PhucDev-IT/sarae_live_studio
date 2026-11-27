import io
import json
import shutil
import wave
from pathlib import Path
import httpx
import pytest
from sarae_agent.studio.voice_server import LocalVoiceServer
from sarae_agent.errors import AgentError
from test_studio_replies import studio, isolated_environment, desk_for, comment

PROFILE={"voice_id":"linh_phuong","name":"Linh Phương","rev":"revision1","prompt":{"cache":"ram"}}

@pytest.fixture
def voice_root(tmp_path,monkeypatch):
    root=tmp_path/"voice-server"
    root.mkdir()
    (root/".env").write_text("SARAE_VOICE_ORCHESTRATOR_TOKENS=test-local-token",encoding="utf-8")
    monkeypatch.delenv("SARAE_VOICE_DATA_DIR",raising=False)
    monkeypatch.delenv("SARAE_STUDIO_VOICE_URL",raising=False)
    monkeypatch.delenv("SARAE_VOICE_ORCHESTRATOR_TOKENS",raising=False)
    monkeypatch.delenv("SARAE_VOICE_PORT",raising=False)
    folder=root/"data"/"voices"/PROFILE["voice_id"]
    folder.mkdir(parents=True)
    (folder/"profile.json").write_text(json.dumps(PROFILE),encoding="utf-8")
    return root

def test_lists_saved_cloned_voice_when_server_is_offline(voice_root):
    server=LocalVoiceServer(voice_root,transport=httpx.MockTransport(lambda req:httpx.Response(503)))
    result=server.refresh()
    local=next(v for v in result["voice_options"] if v["provider"]=="local_voice")
    assert local["name"]=="Linh Phương" and local["id"]=="local:linh_phuong" and local["ready"] is False
    assert "test-local-token" not in json.dumps(result)

def test_refresh_authenticates_only_to_local_server_and_reads_names(voice_root):
    seen=[]
    def handle(req):
        seen.append(req)
        return httpx.Response(200,json={"voices":[PROFILE,{**PROFILE,"voice_id":"st_design","name":"ST DESIGN 1"}]})
    server=LocalVoiceServer(voice_root,transport=httpx.MockTransport(handle))
    result=server.refresh()
    assert result["voice_server"]["online"] is True
    assert [v["name"] for v in result["voice_options"] if v["provider"]=="local_voice"]==["Linh Phương","ST DESIGN 1"]
    assert seen[1].headers["Authorization"]=="Bearer test-local-token"

def test_remote_voice_endpoint_is_rejected_without_sending_credentials(voice_root,monkeypatch):
    monkeypatch.setenv("SARAE_STUDIO_VOICE_URL","https://remote.example")
    calls=[]
    server=LocalVoiceServer(voice_root,transport=httpx.MockTransport(lambda req:calls.append(req)))
    assert server.refresh()["voice_server"]["online"] is False and calls==[]


def test_start_does_not_spawn_a_second_ready_voice_server(voice_root,monkeypatch):
    folder=voice_root/".venv"/"Scripts"
    folder.mkdir(parents=True)
    (folder/"python.exe").touch()
    server=LocalVoiceServer(voice_root,transport=httpx.MockTransport(lambda req:httpx.Response(200,json={"voices":[PROFILE]})))
    def forbidden(*args,**kwargs): raise AssertionError("Must reuse running server")
    monkeypatch.setattr("sarae_agent.studio.voice_server.subprocess.Popen",forbidden)
    server.start()
    assert server.catalog()["voice_server"]["online"] is True

@pytest.mark.skipif(not shutil.which("ffmpeg"),reason="FFmpeg is required")
def test_synthesis_uses_selected_voice_and_produces_real_mp3(voice_root,tmp_path):
    wav=io.BytesIO()
    with wave.open(wav,"wb") as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(24000);output.writeframes(b"\x00\x00"*2400)
    requests=[]
    def handle(req):
        requests.append(req)
        return httpx.Response(200,content=wav.getvalue())
    server=LocalVoiceServer(voice_root,transport=httpx.MockTransport(handle))
    output=tmp_path/"speech.mp3"
    server.synthesize("Xin chào","linh_phuong","live_fast",{"rate":0},output,None,lambda:None)
    payload=json.loads(requests[0].content)
    assert payload["voice_id"]=="linh_phuong" and payload["preset"]=="live_fast"
    assert output.stat().st_size>0 and not output.read_bytes().startswith(b"RIFF")

def test_local_voice_choice_is_persisted_and_used_by_reply_desk(studio,voice_root):
    server=LocalVoiceServer(voice_root,transport=httpx.MockTransport(lambda req:httpx.Response(200,json={"voices":[PROFILE]})))
    server.refresh()
    desk=desk_for(studio)
    desk.voice_server=server
    desk.configure({"voice":"local:linh_phuong","call_name":False})
    entry=desk.ingest(comment("giá bao nhiêu"),simulated=True)
    desk.answer(desk._entry(desk._queue.popleft()))
    desk.speak({"id":entry["id"]})
    call=desk.voice.calls[0]
    assert call["voice"]=="linh_phuong" and call["provider"]=="local_voice" and call["settings"]["revision"]=="revision1"
    assert desk_for(studio).settings()["voice"]=="local:linh_phuong"

def test_offline_cloned_voice_reports_failed_delivery(studio,voice_root):
    desk=desk_for(studio)
    desk.voice_server=LocalVoiceServer(voice_root)
    desk.configure({"voice":"local:linh_phuong"})
    entry=desk.ingest(comment("giá bao nhiêu"),simulated=True)
    desk.answer(desk._entry(desk._queue.popleft()))
    result=desk.speak({"id":entry["id"]})
    entry=result["entry"]
    assert entry["voice"]["status"]=="FAILED" and desk.voice.calls==[]
