"""Opt-in Phase 6: real Web, HTTP Backend, GPU, packaged Tauri Agent and OBS.

Run from repository root with Backend Python, SARAE_PHASE6_E2E=1. All owner/Agent
credentials and databases are fixture data. Never starts a stream/recording or LIVE.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'agent-core/src'),str(ROOT/'backend'),str(ROOT/'backend/tests')]
sys.path.append(str(ROOT/'agent-core/.venv/Lib/site-packages'))
from support import SaasTestCase


@unittest.skipUnless(os.environ.get('SARAE_PHASE6_E2E')=='1','explicit Phase6 desktop/OBS opt-in required')
class FullApplicationTests(SaasTestCase):
    def test_full_application(self):
        import httpx,obsws_python,uvicorn
        from playwright.sync_api import sync_playwright,expect
        from fastapi.middleware.cors import CORSMiddleware
        from app import identity,storage_providers,tts_server,tts_orchestrator,voice_profiles,cloud_agents
        from app.main import app
        from app.config import settings
        from app.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from memory_storage import MemoryStorageProvider,MemoryStore
        from sarae_agent.config import ConfigStore
        from sarae_agent.activation.service import ActivationService
        from sarae_agent.activation.secrets import obs_secret_file
        out=ROOT/'.phase6';out.mkdir(exist_ok=True)
        # A real application handles concurrent HTTP/event requests. The shared in-memory
        # StaticPool used by small unit fixtures is unsuitable for this long-running test.
        old_engine=self.engine
        self.engine=create_engine(f"sqlite:///{(out/('app-db-'+self.owner_a['id']+'.sqlite3')).as_posix()}",connect_args={'check_same_thread':False,'timeout':30})
        Base.metadata.create_all(self.engine)
        from app.shop_schema import upgrade
        upgrade(self.engine)
        self.Session=sessionmaker(bind=self.engine,autoflush=False,autocommit=False)
        for module in ('app.voice_profiles','app.tts_orchestrator','app.live_sessions'):
            override=patch(module+'.SessionLocal',self.Session);override.start();self.addCleanup(override.stop)
        self.addCleanup(old_engine.dispose)
        app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5176'],allow_methods=['*'],allow_headers=['*'],expose_headers=['X-Sarae-Error','X-Voice-Revision'])
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8794,lifespan='off',log_level='warning'))
        thread=threading.Thread(target=server.run,daemon=True)
        c=json.loads((Path(os.environ['APPDATA'])/'obs-studio/plugin_config/obs-websocket/config.json').read_text())
        obs=obsws_python.ReqClient(host='127.0.0.1',port=c['server_port'],password=c.get('server_password',''),timeout=4)
        self.assertFalse(obs.send('GetStreamStatus',raw=True)['outputActive'])
        self.assertFalse(obs.send('GetRecordStatus',raw=True)['outputActive'])
        original=obs.send('GetInputSettings',{'inputName':'Sarae Voice'},raw=True)['inputSettings']
        muted=obs.send('GetInputMute',{'inputName':'Sarae Voice'},raw=True)['inputMuted']
        volumes={n:obs.send('GetInputVolume',{'inputName':n},raw=True)['inputVolumeDb'] for n in ('Sarae Video A','Sarae Video B')}
        MemoryStorageProvider.store=MemoryStore();storage_providers.register('MEMORY',MemoryStorageProvider)
        home=out/('agent-home-'+self.owner_a['id'][:8]);gui=out/('app-'+self.owner_a['id'][:8]);gui.mkdir(exist_ok=True)
        shutil.copy2(ROOT/'desktop/src-tauri/target/debug/sarae-agent-desktop.exe',gui/'sarae-agent-desktop.exe')
        shutil.copy2(out/'packaged/sarae-agent-core.exe',gui/'sarae-agent-core.exe')
        process=None;profile_id=None;restarted_backend=None
        measurements=json.loads((out/'measurements.json').read_text(encoding='utf-8')) if (out/'measurements.json').exists() else []
        (out/'obs-before.json').write_text(json.dumps({'settings':original,'muted':muted,'volumes':volumes}),encoding='utf-8')
        with patch.multiple(settings,sarae_tts_server_url='http://127.0.0.1:8783',sarae_tts_server_token='phase6-local-test-only',
                sarae_tts_agent_url='http://127.0.0.1:8783',sarae_tts_timeout_seconds=30,sarae_storage_provider='MEMORY'),patch.dict(os.environ,SARAE_AGENT_HOME=str(home)):
            try:
                tts_server.set_transport(None);voice_profiles._verified.clear();thread.start()
                for _ in range(100):
                    if server.started:break
                    time.sleep(.05)
                key=identity.issue_key(self.owner_a['id'],authorization=self.ADMIN['Authorization'])['activation_key']
                activated=ActivationService().activate('http://127.0.0.1:8794/api',key)
                aid=activated['agent_id']
                cfg=ConfigStore();cfg.update({'cloud':{'api_url':'http://127.0.0.1:8794/api','heartbeat_seconds':3,'poll_seconds':1},
                    'agent':{'autostart':True},'monitor':{'autostart':False},'overlay':{'enabled':False},'obs':{'voice_input':'Sarae Voice','media_input':'Sarae Video A','auto_connect':True}})
                obs_secret_file.save({'password':c.get('server_password','')})
                def launch():
                    env={**os.environ,'SARAE_AGENT_HOME':str(home),'WEBVIEW2_USER_DATA_FOLDER':str(out/'webview')}
                    return subprocess.Popen([str(gui/'sarae-agent-desktop.exe')],cwd=gui,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
                process=launch()
                for _ in range(240):
                    if tts_orchestrator.supports_omnivoice(aid):break
                    time.sleep(.5)
                else:self.fail('packaged desktop Agent never reported OmniVoice heartbeat')
                print('PHASE6_GUI_RUNNING',flush=True);(out/'gui-ready').touch()
                if os.environ.get('SARAE_PHASE6_HOLD_UI')=='1':
                    for _ in range(360):
                        if (out/'release-ui').exists():break
                        time.sleep(.5)
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(channel='msedge',headless=True)
                    try:
                        page=browser.new_page(viewport={'width':1280,'height':1000});page.set_default_timeout(15000)
                        page.add_init_script("sessionStorage.setItem('sarae-auth-token',"+json.dumps(self.A['Authorization'].removeprefix('Bearer '))+")")
                        page.on('dialog',lambda d:d.accept());page.goto('http://127.0.0.1:5176/#/ai')
                        page.get_by_label('Provider',exact=True).first.select_option('omnivoice')
                        page.get_by_role('button',name='Tạo giọng',exact=True).click()
                        dlg=page.get_by_role('dialog');dlg.get_by_label('Tên giọng').fill('Phase6 chuẩn')
                        dlg.get_by_label('WAV giọng mẫu').set_input_files(os.environ.get('SARAE_TTS_REAL_REF','C:/Users/Phuc/Downloads/voice_ref_pause_7s_test.wav'))
                        dlg.get_by_role('button',name='Tạo giọng',exact=True).click()
                        expect(page.locator('article').filter(has_text='Phase6 chuẩn')).to_be_visible(timeout=90000)
                        profile_id=self.client.get('/api/ai/voice/profiles').json()['profiles'][0]['id']
                        page.get_by_label('Giọng mặc định').select_option(profile_id)
                        page.get_by_role('button',name='Nghe thử',exact=True).click()
                        expect(page.get_by_label('Giọng nghe thử')).to_be_visible(timeout=60000)
                        self.assertEqual(self.client.get('/api/ai/voice/config').json()['provider'],'edge')
                        page.get_by_role('button',name='Lưu giọng nói',exact=True).click()
                        page.get_by_role('button',name='Kiểm tra & đồng bộ TTS').click()
                        expect(page.get_by_text('TTS sẵn sàng tại thời điểm kiểm tra',exact=True)).to_be_visible(timeout=60000)
                        def finished(uid,timeout=65):
                            end=time.monotonic()+timeout
                            while time.monotonic()<end:
                                rows=self.client.get('/api/ai/voice/utterances',params={'agent_id':aid}).json()['utterances']
                                row=next((r for r in rows if r['id']==uid),None)
                                if row and row['state'] in ('PLAYED','FAILED','EXPIRED','CANCELLED'):
                                    self.assertEqual(row['state'],'PLAYED',row);return row
                                time.sleep(.1)
                            self.fail('utterance did not finish: '+uid)
                        # First manual command is submitted from the actual Web screen.
                        page.get_by_role('button',name='Phát qua OBS (giọng đã lưu)').click()
                        end=time.monotonic()+65
                        while time.monotonic()<end:
                            rows=self.client.get('/api/ai/voice/utterances',params={'agent_id':aid}).json()['utterances']
                            if rows:break
                            time.sleep(.1)
                        finished(rows[0]['id'])
                        page.get_by_text('Trạng thái phát G-OmniVoice',exact=True).click()
                        expect(page.get_by_text('Đã phát xong',exact=True)).to_be_visible(timeout=15000)
                        print('PHASE6_WEB_TO_OBS_PASS',flush=True)
                        if os.environ.get('SARAE_PHASE6_RESTART_BACKEND')=='1':
                            process.terminate();process.wait(10);time.sleep(5)
                            queued=tts_orchestrator.speak(self.owner_a['id'],aid,text='Chị Lan ơi, Backend đã kết nối lại nha.',intro=None,
                                ttl_seconds=60,duck=True,session_id=None,reply_id=None,source='manual')
                            uid=queued['utterances'][0]['id'];job_id=queued['utterances'][0]['tts_job_id']
                            page.close();server.should_exit=True;thread.join(5);self.assertFalse(thread.is_alive())
                            data=out/('backend-restart-'+self.owner_a['id'][:8]);data.mkdir()
                            raw=self.engine.raw_connection();target=sqlite3.connect(data/'sarae_live.db')
                            raw.driver_connection.backup(target);target.close();raw.close()
                            for src,name in ((identity.DB,'identity.sqlite3'),(cloud_agents.DB,'agents.sqlite3')):
                                src_db=sqlite3.connect(src);dst_db=sqlite3.connect(data/name);src_db.backup(dst_db);dst_db.close();src_db.close()
                            env={**os.environ,'SARAE_DATA_DIR':str(data),'SARAE_TTS_SERVER_URL':'http://127.0.0.1:8783',
                                'SARAE_TTS_SERVER_TOKEN':'phase6-local-test-only','SARAE_TTS_AGENT_URL':'http://127.0.0.1:8783'}
                            restarted_backend=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8794','--lifespan','off','--log-level','warning'],
                                cwd=ROOT/'backend',env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
                            for _ in range(100):
                                try:
                                    if httpx.get('http://127.0.0.1:8794/openapi.json',timeout=1).status_code==200:break
                                except httpx.TransportError:pass
                                time.sleep(.1)
                            process=launch()
                            with httpx.Client(base_url='http://127.0.0.1:8794',headers=self.A,timeout=5) as client:
                                for _ in range(300):
                                    rows=client.get('/api/ai/voice/utterances',params={'agent_id':aid}).json()['utterances']
                                    row=next(r for r in rows if r['id']==uid)
                                    if row['state'] in ('PLAYED','FAILED','EXPIRED','CANCELLED'):break
                                    time.sleep(.1)
                                self.assertEqual(row['state'],'PLAYED',row);self.assertEqual(row['tts_job_id'],job_id)
                            db=sqlite3.connect(data/'agents.sqlite3')
                            ev=[json.loads(x[0]) for x in db.execute('SELECT payload FROM agent_events WHERE agent_id=?',(aid,))];db.close()
                            clip=next(e['detail'] for e in ev if e['type']=='tts_playback' and e['detail'].get('utterance_id')==uid and e['detail'].get('status')=='playing')
                            self.assertEqual(clip['audio_path'],'relay')
                            (out/'backend-restart-result.json').write_text(json.dumps({'restart':'PASS','packaged_agent_reconnected':True,'path':'relay','job_not_recreated':True,'state':'PLAYED'}),encoding='utf-8')
                            print('PHASE6_BACKEND_RESTART_PASS',flush=True)
                            return
                        if os.environ.get('SARAE_PHASE6_RESTART_VOICE_PID'):
                            url=settings.sarae_tts_server_url
                            import psutil
                            from datetime import datetime,timedelta,timezone
                            victim=psutil.Process(int(os.environ['SARAE_PHASE6_RESTART_VOICE_PID']))
                            self.assertEqual(Path(victim.environ()['SARAE_VOICE_DATA_DIR']).resolve(),(out/'voice').resolve())
                            long=tts_server.create_job(idempotency_key='restart-long-'+profile_id,voice_id=profile_id,
                                text=('Chị Lan ơi, áo này có đủ màu và đủ size nha. '*30),priority='long',
                                deadline_at=(datetime.now(timezone.utc)+timedelta(seconds=120)).isoformat(),reader_agent_id=aid,owner_ref=self.owner_a['id'],tags=['phase6-restart'])
                            for _ in range(100):
                                if tts_server.get_job(long['job_id'])['state']=='running':break
                                time.sleep(.05)
                            queued=tts_orchestrator.speak(self.owner_a['id'],aid,text='Job cũ sau restart không được tạo lại nha.',intro=None,
                                ttl_seconds=60,duck=True,session_id=None,reply_id=None,source='manual')
                            uid=queued['utterances'][0]['id'];job_id=queued['utterances'][0]['tts_job_id']
                            self.assertEqual(tts_server.get_job(job_id)['state'],'queued')
                            victim.kill();victim.wait(10)
                            env={**os.environ,'SARAE_VOICE_ORCHESTRATOR_TOKENS':'phase6-local-test-only',
                                'SARAE_VOICE_PORT':'8783','SARAE_VOICE_DATA_DIR':str(out/'voice'),'HF_HUB_OFFLINE':'1'}
                            output=(out/'voice-restart.log').open('w',encoding='utf-8')
                            replacement=subprocess.Popen([str(ROOT/'voice-server/.venv/Scripts/python.exe'),'-m','sarae_voice'],cwd=ROOT/'voice-server',env=env,stdout=output,stderr=output,creationflags=subprocess.CREATE_NO_WINDOW)
                            try:
                                for _ in range(240):
                                    try:
                                        if httpx.get(url+'/health/ready',timeout=2).status_code==200:break
                                    except httpx.TransportError:pass
                                    time.sleep(.5)
                                else:self.fail('voice-server did not recover')
                                r=httpx.get(url+'/v1/tts/jobs/'+job_id,headers={'Authorization':'Bearer phase6-local-test-only'},timeout=5)
                                self.assertEqual(r.status_code,410,r.text)
                                for _ in range(200):
                                    with self.Session() as db:row=db.get(tts_orchestrator.TtsUtterance,uid);state=row.state;held_job=row.tts_job_id
                                    if state in ('FAILED','EXPIRED','CANCELLED'):break
                                    time.sleep(.1)
                                self.assertIn(state,('FAILED','EXPIRED','CANCELLED'))
                                self.assertEqual(held_job,job_id)
                                (out/'voice-restart-result.json').write_text(json.dumps({'restart':'PASS','old_job_status':410,'utterance':state,'job_not_recreated':True}),encoding='utf-8')
                                print('PHASE6_VOICE_RESTART_PASS',state,flush=True)
                                if profile_id:
                                    tts_server.delete_voice(profile_id)
                                    profile_id=None
                            finally:
                                (out/'voice/server.stop').touch()
                                replacement.wait(45);output.close()
                            # Restart rehearsal has its own cleanup, no further GPU work in this invocation.
                            return
                        if os.environ.get('SARAE_PHASE6_FAULTS')=='1':
                            from datetime import datetime,timedelta,timezone
                            fault_results={}
                            def terminal(uid,wanted,timeout=70):
                                end=time.monotonic()+timeout
                                while time.monotonic()<end:
                                    with self.Session() as db:row=db.get(tts_orchestrator.TtsUtterance,uid);state=row.state
                                    if state in ('PLAYED','FAILED','EXPIRED','CANCELLED'):
                                        self.assertIn(state,wanted);return state
                                    time.sleep(.1)
                                self.fail('fault case did not terminate '+uid)
                            url=settings.sarae_tts_server_url
                            settings.sarae_tts_server_url='http://127.0.0.1:18783';tts_server.set_transport(None)
                            self.assertEqual(self.client.post('/api/ai/voice/speak',json={'agent_id':aid,'text':'Máy TTS offline ạ.'}).status_code,503)
                            settings.sarae_tts_server_url=url;tts_server.set_transport(None);fault_results['offline_before_job']='PASS'
                            settings.sarae_tts_agent_url='http://127.0.0.1:18783'
                            r=tts_orchestrator.speak(self.owner_a['id'],aid,text='Dạ chị Lan, đường relay vẫn hoạt động nha.',intro=None,ttl_seconds=60,duck=True,session_id=None,reply_id=None,source='manual')
                            terminal(r['utterances'][0]['id'],('PLAYED',));fault_results['direct_unreachable_relay']='PASS'
                            original_create=cloud_agents.create_command
                            def publish_during_outage(*args,**kwargs):
                                settings.sarae_tts_server_url='http://127.0.0.1:18783'
                                return original_create(*args,**kwargs)
                            with patch.object(cloud_agents,'create_command',publish_during_outage):
                                r=tts_orchestrator.speak(self.owner_a['id'],aid,text='Câu này không được fallback nha.',intro=None,ttl_seconds=60,duck=True,session_id=None,reply_id=None,source='manual')
                            terminal(r['utterances'][0]['id'],('FAILED',));fault_results['relay_unreachable_none']='PASS'
                            settings.sarae_tts_server_url=url;settings.sarae_tts_agent_url=url;tts_server.set_transport(None)
                            # A real long GPU job blocks a 5-second utterance: it must expire, never play.
                            long=tts_server.create_job(idempotency_key='phase6-long-'+profile_id,voice_id=profile_id,
                                text=('Dạ chị Lan, mẫu áo này có chất vải mềm và còn đủ size nhé. '*24),priority='long',
                                deadline_at=(datetime.now(timezone.utc)+timedelta(seconds=120)).isoformat(),reader_agent_id=aid,owner_ref=self.owner_a['id'],tags=['phase6-long'])
                            for _ in range(100):
                                if tts_server.get_job(long['job_id'])['state']=='running':break
                                time.sleep(.05)
                            lease=tts_server.put_lease(self.owner_a['id'],'phase6',60)
                            self.assertFalse(lease['ready_for_live']);fault_results['long_job_readiness']='PASS'
                            r=tts_orchestrator.speak(self.owner_a['id'],aid,text='Câu chờ quá hạn phải bỏ nha.',intro=None,ttl_seconds=5,duck=True,session_id=None,reply_id=None,source='manual')
                            terminal(r['utterances'][0]['id'],('EXPIRED',));fault_results['expired_while_queued']='PASS'
                            r=tts_orchestrator.speak(self.owner_a['id'],aid,text='Câu đã clear không được phát lại ạ.',intro=None,ttl_seconds=60,duck=True,session_id=None,reply_id=None,source='manual')
                            tts_orchestrator.cancel_agent(self.owner_a['id'],aid)
                            terminal(r['utterances'][0]['id'],('CANCELLED',));fault_results['clear_waiting_audio']='PASS'
                            # Crash GUI only in this isolated home while its next utterance is accepted/waiting.
                            r=tts_orchestrator.speak(self.owner_a['id'],aid,text='Câu không chắc sau crash không phát lại nhé.',intro=None,ttl_seconds=60,duck=True,session_id=None,reply_id=None,source='manual')
                            uid=r['utterances'][0]['id']
                            for _ in range(100):
                                with cloud_agents._db() as db:
                                    ev=[json.loads(x['payload']) for x in db.execute('SELECT payload FROM agent_events WHERE agent_id=?',(aid,))]
                                if any(e['type']=='tts_playback' and e['detail'].get('utterance_id')==uid and e['detail'].get('status')=='waiting_audio' for e in ev):break
                                time.sleep(.05)
                            else:self.fail('Agent did not accept crash-window utterance')
                            with cloud_agents._db() as db:payload=json.loads(db.execute('SELECT payload FROM agent_commands WHERE command_id=?',(r['command']['command_id'],)).fetchone()['payload'])
                            process.terminate();process.wait(10);time.sleep(5);process=launch();time.sleep(7)
                            duplicate=cloud_agents.create_command(aid,self.owner_a['id'],'tts_speak',payload,idempotency_key='phase6-duplicate-'+uid)
                            for _ in range(150):
                                view=cloud_agents.get_command(duplicate['command_id'],aid)
                                if view['status'] in ('SUCCESS','FAILED'):break
                                time.sleep(.1)
                            self.assertEqual(view['status'],'SUCCESS',view)
                            self.assertIn('duplicate',str(view['result']));fault_results['packaged_agent_crash_dedup']='PASS'
                            tts_server.delete_lease(self.owner_a['id'],'phase6')
                            tts_server.cancel_job(long['job_id'])
                            (out/'fault-results.json').write_text(json.dumps(fault_results,indent=2),encoding='utf-8')
                            print('PHASE6_FAULTS',fault_results,flush=True)
                        count=int(os.environ.get('SARAE_PHASE6_SAMPLES','30'))
                        lines=['Dạ chị Lan, áo này hai trăm nghìn đồng nha.','Chị Hoa lấy size Mờ màu xanh nhé.','Mẫu váy này còn hàng ạ.']
                        scenarios=os.environ.get('SARAE_PHASE6_SCENARIOS','miss_direct,hit_direct,hit_relay,intro_body,prepared,reconnect').split(',')
                        for scenario in scenarios:
                            settings.sarae_tts_agent_url='' if scenario=='hit_relay' else 'http://127.0.0.1:8783'
                            n=count if scenario in ('miss_direct','hit_direct','hit_relay') else min(count,5)
                            if scenario=='hit_relay':
                                # Warm content once per reference profile; classify every sample by actual source.
                                for line in lines:tts_server.synthesize(profile_id,line,'demo_default')
                            if scenario=='reconnect':
                                process.terminate();process.wait(10);time.sleep(4);process=launch();time.sleep(5)
                            for i in range(n):
                                from app.speech_text import spoken
                                text=lines[i%len(lines)] if scenario!='miss_direct' else spoken(f'Dạ chị Lan, mẫu thứ {i+1} còn hàng nha.')
                                text=os.environ.get('SARAE_PHASE6_TEXT',text)
                                if scenario=='prepared':tts_orchestrator.prepare(self.owner_a['id'],aid,[text],None);time.sleep(1)
                                started=time.time()
                                result=tts_orchestrator.speak(self.owner_a['id'],aid,text=text,intro='Chị Lan ơi,' if scenario=='intro_body' else None,
                                    ttl_seconds=60,duck=True,session_id=None,reply_id=None,source='manual')
                                orchestrated=time.time();body=result['utterances'][0];uid=body['id'];finished(uid)
                                with cloud_agents._db() as db:
                                    ev=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM agent_events WHERE agent_id=? ORDER BY seq',(aid,))]
                                    command=db.execute('SELECT * FROM agent_commands WHERE command_id=?',(result['command']['command_id'],)).fetchone()
                                playing=next(e['detail'] for e in ev if e['type']=='tts_playback' and e['detail'].get('utterance_id')==uid and e['detail'].get('status')=='playing')
                                job=tts_server.get_job(body['tts_job_id'])
                                from datetime import datetime
                                claim=datetime.fromisoformat(command['started_at']).timestamp() if command['started_at'] else None
                                row={'scenario':scenario,'i':i,'utterance':uid,'orchestration_ms':round((orchestrated-started)*1000),
                                     'job':job,'playing':playing,'claim_epoch':claim,'text_epoch':started,
                                     'delivery_ms':round((claim-datetime.fromisoformat(command['created_at']).timestamp())*1000) if claim else None,
                                     'e2e_ms':round((claim-started)*1000+playing['playback_start_ms']) if claim else None}
                                measurements.append(row)
                                (out/'measurements.json').write_text(json.dumps(measurements,ensure_ascii=False,default=str),encoding='utf-8')
                                # Keep exact WAV bytes as a listening review set; no transformation.
                                with tts_server.open_audio(body['tts_job_id'],wait=0) as audio:
                                    audio.raise_for_status();(out/f'{uid}.wav').write_bytes(audio.read())
                                if scenario=='intro_body':
                                    with tts_server.open_audio(result['utterances'][1]['tts_job_id'],wait=0) as audio:
                                        audio.raise_for_status();(out/f'{uid}-intro.wav').write_bytes(audio.read())
                            print('PHASE6_SCENARIO',scenario,n,flush=True)
                        for name,volume in volumes.items():self.assertAlmostEqual(obs.send('GetInputVolume',{'inputName':name},raw=True)['inputVolumeDb'],volume,places=3)
                    finally:browser.close()
            finally:
                if process and process.poll() is None:process.terminate();process.wait(10);time.sleep(3)
                obs.send('TriggerMediaInputAction',{'inputName':'Sarae Voice','mediaAction':'OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP'},raw=True)
                obs.send('SetInputSettings',{'inputName':'Sarae Voice','inputSettings':original,'overlay':False},raw=True)
                obs.send('SetInputMute',{'inputName':'Sarae Voice','inputMuted':muted},raw=True)
                for name,volume in volumes.items():obs.send('SetInputVolume',{'inputName':name,'inputVolumeDb':volume},raw=True)
                obs.disconnect();server.should_exit=True;thread.join(5)
                if restarted_backend:
                    import psutil
                    parent=psutil.Process(restarted_backend.pid)
                    for child in parent.children(recursive=True):child.terminate()
                    restarted_backend.terminate();restarted_backend.wait(10)
                if profile_id:
                    try:tts_server.delete_voice(profile_id)
                    except (tts_server.TtsUnavailable,tts_server.TtsError):pass
                storage_providers.unregister('MEMORY')
