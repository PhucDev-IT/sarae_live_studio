"""Opt-in browser -> real Backend HTTP -> real GPU preview. No OBS or LIVE.

Run with backend/.venv/Scripts/python.exe and SARAE_WEB_VOICE_E2E=1,
SARAE_TTS_REAL_URL/TOKEN/REF. Vite harness must run on localhost:5176 with VITE_API_URL=http://127.0.0.1:8794.
Uses temporary owner/Agent/database/storage fixtures; never uses a production login.
"""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/tests')]
sys.path.append(str(ROOT/'agent-core/.venv/Lib/site-packages'))
from support import SaasTestCase


@unittest.skipUnless(os.environ.get('SARAE_WEB_VOICE_E2E')=='1','explicit Web real-preview opt-in required')
class RealWebPreviewTests(SaasTestCase):
    def test_create_asr_preview_reload_and_owner_isolation(self):
        import uvicorn
        from playwright.sync_api import sync_playwright,expect
        from app import tts_server,voice_profiles,storage_providers
        from app.main import app
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5176'],allow_methods=['*'],allow_headers=['*'],expose_headers=['X-Sarae-Error','X-Voice-Revision'])
        from app.config import settings
        from memory_storage import MemoryStorageProvider,MemoryStore
        MemoryStorageProvider.store=MemoryStore()
        storage_providers.register('MEMORY',MemoryStorageProvider)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8794,lifespan='off',log_level='warning'))
        thread=threading.Thread(target=server.run,daemon=True)
        agent=self.activate_agent(self.owner_a,'Phase5-preview')
        profile_id=None
        with patch.multiple(settings,sarae_tts_server_url=os.environ['SARAE_TTS_REAL_URL'],
                sarae_tts_server_token=os.environ['SARAE_TTS_REAL_TOKEN'],sarae_tts_timeout_seconds=30,sarae_storage_provider='MEMORY'):
            tts_server.set_transport(None);voice_profiles._verified.clear()
            try:
                thread.start()
                for _ in range(100):
                    if server.started:break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(channel='msedge',headless=True)
                    try:
                        context=browser.new_context(viewport={'width':1280,'height':1000})
                        page=context.new_page()
                        page.set_default_timeout(15000)
                        page.add_init_script("sessionStorage.setItem('sarae-auth-token',"+__import__('json').dumps(self.A['Authorization'].removeprefix('Bearer '))+")")
                        page.on('dialog',lambda d:d.accept())
                        requests=[]
                        page.on('request',lambda request: requests.append((request.method,request.url.split('/api',1)[1])) if '/api/' in request.url else None)
                        page.goto('http://127.0.0.1:5176/tests/voice-harness.html')
                        page.get_by_label('Provider',exact=True).select_option('omnivoice')
                        page.get_by_role('button',name='Tạo giọng',exact=True).click()
                        dialog=page.get_by_role('dialog')
                        dialog.get_by_label('Tên giọng').fill('Phase 5 model thật')
                        dialog.get_by_label('WAV giọng mẫu').set_input_files(os.environ['SARAE_TTS_REAL_REF'])
                        start=time.monotonic()
                        dialog.get_by_role('button',name='Tạo giọng',exact=True).click()
                        card=page.locator('article').filter(has_text='Phase 5 model thật')
                        expect(card).to_be_visible(timeout=90000)
                        expect(card.get_by_text('Đã đồng bộ',exact=True)).to_be_visible()
                        upload_ms=round((time.monotonic()-start)*1000)
                        profiles=self.client.get('/api/ai/voice/profiles').json()['profiles']
                        profile_id=profiles[0]['id']
                        self.assertTrue(profiles[0]['transcript']['asr'])
                        page.get_by_label('Giọng mặc định').select_option(profile_id)
                        start=time.monotonic()
                        page.get_by_role('button',name='Lưu & nghe thử',exact=True).click()
                        audio=page.get_by_label('Giọng nghe thử')
                        expect(audio).to_be_visible(timeout=60000)
                        page.wait_for_function("()=>{const a=document.querySelector('audio[aria-label=\"Giọng nghe thử\"]');return a&&a.readyState>=1&&a.duration>0}")
                        preview_ms=round((time.monotonic()-start)*1000)
                        audio.evaluate('(a)=>a.play()')
                        audio.evaluate('(a)=>a.pause()')
                        audio.evaluate('(a)=>{a.currentTime=0;return a.play()}')
                        audio.evaluate('(a)=>a.pause()')
                        page.reload()
                        expect(page.get_by_label('Provider',exact=True)).to_have_value('omnivoice')
                        expect(page.get_by_label('Giọng mặc định')).to_have_value(profile_id)
                        self.assertEqual(self.client.get('/api/ai/voice/profiles',headers=self.B).json()['profiles'],[])
                        self.assertEqual(self.client.get('/api/ai/voice/profiles/'+profile_id,headers=self.B).status_code,404)
                        self.assertEqual(self.client.get('/api/ai/voice/utterances',headers=self.B).json()['utterances'],[])
                        self.assertFalse(any('/tts/jobs' in path or '/token' in path or '/start' in path for _,path in requests))
                        print('WEB_REAL_PREVIEW',{'upload_sync_ms':upload_ms,'preview_ready_ms':preview_ms,'agent_fixture':bool(agent),'browser_audio':'play/pause/replay OK','owner_isolation':'OK'})
                    finally:browser.close()
            finally:
                if profile_id:tts_server.delete_voice(profile_id)
                server.should_exit=True;thread.join(5)
                storage_providers.unregister('MEMORY')


if __name__=='__main__':unittest.main(verbosity=2)
