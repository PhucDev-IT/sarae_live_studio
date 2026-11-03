"""Browser/component integration tests. Run Vite on localhost:5175 first.

Run with agent-core/.venv/Scripts/python.exe web/tests/voice_ui.py.
Uses installed Playwright/Edge; all Backend requests are intercepted and owner authenticated.
"""
import copy
import io
import json
import re
import unittest
import wave
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect


PROFILE = dict(id='a'*32,name='Giọng shop',revision=1,updated_at='2026-11-03T04:00:00Z',
    wav=dict(duration_s=7.54,sample_rate=24000,channels=1,bytes=362000),
    transcript=dict(source='asr',manual=None,asr='Xin chào cửa hàng.',effective='Xin chào cửa hàng.'),
    sync=dict(state='SYNCED',up_to_date=True,error=None),qc=dict(local=dict(warnings=[]),server=dict(warnings=['end_may_be_cut'])))
CONFIG = dict(provider='omnivoice',model='demo_default',voice='a'*32,rate=0,settings={},has_api_key=True,
    omnivoice_configured=True,fallback=dict(live_reply='none',prepare='none',manual='none',provider=None))
READY = dict(provider='omnivoice',applicable=True,ready=False,warnings=['AGENT_NO_OMNIVOICE','LONG_JOB_RUNNING'],
    service=dict(configured=True,online=True,state='loading'),voice=dict(name='Giọng shop',revision=1,state='SYNCED',up_to_date=True),
    agent=dict(agent_id='agent',supports_omnivoice=False),gpu=dict(busy=True,running={'class':'long'}))


def wav():
    b=io.BytesIO()
    with wave.open(b,'wb') as w:
        w.setparams((1,2,24000,0,'NONE',''));w.writeframes(b'\0\0'*2400)
    return b.getvalue()


class VoiceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(channel='msedge',headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':1280,'height':1000})
        self.page=self.context.new_page()
        self.page.add_init_script("sessionStorage.setItem('sarae-auth-token','owner-test')")
        self.config=copy.deepcopy(CONFIG);self.profiles=[copy.deepcopy(PROFILE)]
        self.calls=[];self.running=False;self.preview_error=None
        self.readiness=copy.deepcopy(READY)
        self.errors=[];self.page.on('pageerror',lambda e:self.errors.append(str(e)))
        self.page.on('dialog',lambda d:d.accept())
        self.context.route('**/api/**',self.route)
        self.page.goto('http://127.0.0.1:5175/tests/voice-harness.html')
        expect(self.page.get_by_role('heading',name='Voice Settings')).to_be_visible()

    def tearDown(self):
        self.assertFalse(self.errors,self.errors)
        self.context.close()

    def route(self,route):
        r=route.request;path=urlsplit(r.url).path.removeprefix('/api');method=r.method
        self.calls.append((method,path,(r.post_data_buffer or b'').decode('utf-8',errors='replace')))
        if method=='OPTIONS':
            route.fulfill(status=204,headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'*','Access-Control-Allow-Methods':'*'});return
        if path!='/health':self.assertEqual(r.headers.get('authorization'),'Bearer owner-test')
        status=200;data={}
        if path=='/health':data={'status':'UP'}
        elif path=='/identity/me':data={'id':'owner-test','email':'owner@example.test','role':'OWNER'}
        elif path=='/ai/config':data=dict(provider='gemini',model='test',enabled=False,max_output_tokens=220,max_requests_per_session=100,has_api_key=False)
        elif path=='/ai/usage':data=dict(requests=0,input_tokens=0,output_tokens=0)
        elif path=='/ai/credentials':data={'credentials':[]}
        elif path=='/owner/agents':data={'agents':[dict(id='agent',hostname='Windows PC',online=True,platform='WINDOWS',last_seen=None)]}
        elif path=='/ai/voice/config':
            if method=='PUT':self.config.update(r.post_data_json)
            data=self.config
        elif path=='/ai/voice/catalog':data=dict(models=[dict(id='eleven_flash_v2_5',name='Flash')],voices=[dict(id='voice11',name='Voice Eleven')])
        elif path=='/live/sessions':data=[dict(status='RUNNING')] if self.running else []
        elif path.endswith('/readiness'):data={'tts':self.readiness} if method=='GET' else {**self.readiness,'warmup':dict(cache='ram',ms=2)}
        elif path=='/ai/voice/utterances':data={'utterances':[dict(id=str(i),state=s,tts_job_state='running',part='body',voice_rev=1,created_at='2026-11-03T04:00:00Z',error_code=None) for i,s in enumerate(['NEW','SENT','PLAYING','PLAYED','CANCELLED','EXPIRED','FAILED'])]}
        elif path=='/ai/voice/preview' or path.endswith('/wav') and method=='GET':
            if self.preview_error and path.endswith('preview'):
                code={429:'TTS_BUSY',503:'TTS_NOT_READY'}.get(self.preview_error,'')
                detail={409:'Giọng đang được sử dụng.',422:'Nội dung chưa hợp lệ.'}.get(self.preview_error,'busy')
                route.fulfill(status=self.preview_error,headers={'Access-Control-Allow-Origin':'*','Access-Control-Expose-Headers':'X-Sarae-Error','X-Sarae-Error':code},json={'detail':detail});return
            route.fulfill(status=200,headers={'Access-Control-Allow-Origin':'*'},content_type='audio/wav',body=wav());return
        elif path=='/ai/voice/profiles':
            if method=='POST':self.profiles.append({**copy.deepcopy(PROFILE),'id':'b'*32,'name':'Giọng mới','sync':dict(state='PENDING',up_to_date=False,error=None)});data=self.profiles[-1];status=201
            else:data={'profiles':self.profiles,'tts_configured':True}
        elif path.startswith('/ai/voice/profiles/'):
            pid=path.split('/')[4];p=next((p for p in self.profiles if p['id']==pid),None)
            if method=='DELETE':self.profiles=[p for p in self.profiles if p['id']!=pid];status=204
            elif method=='PATCH':
                body=r.post_data_json;p['name']=body.get('name',p['name'])
                if 'transcript' in body:p['transcript'].update(source='manual' if body['transcript'] else 'asr',manual=body['transcript']);p['revision']+=1
                data=p
            elif path.endswith('/sync'):
                p['sync'].update(state='SYNCED',up_to_date=True);data=p
            elif path.endswith('/wav'):
                p['revision']+=1;p['sync'].update(state='PENDING',up_to_date=False);p['transcript'].update(source='asr',manual=None);data=p
            else:data=p
        else:raise AssertionError(f'Unexpected API {method} {path}')
        route.fulfill(status=status,headers={'Access-Control-Allow-Origin':'*'},content_type='application/json',body='' if status==204 else json.dumps(data))

    def test_passive_open_no_mutations_and_statuses(self):
        expect(self.page.get_by_text('Đuôi WAV có thể bị cắt khi còn tiếng nói.')).to_be_visible()
        expect(self.page.get_by_text('Đang tải model',exact=True)).to_be_visible()
        self.assertFalse([c for c in self.calls if c[0] not in ('GET','OPTIONS')])
        self.page.get_by_text('Trạng thái phát G-OmniVoice',exact=True).click()
        for label in ['Đã gửi · đang chờ âm thanh','Đang phát','Đã phát xong','Bị hủy','Hết hạn','Thất bại']:
            expect(self.page.get_by_text(label,exact=True)).to_be_visible()

    def test_preview_wav_replay_and_error_no_fallback(self):
        self.page.get_by_role('button',name='Lưu & nghe thử',exact=True).click()
        expect(self.page.get_by_label('Giọng nghe thử')).to_be_visible()
        self.assertEqual(len([c for c in self.calls if c[1]=='/ai/voice/preview']),1)
        self.preview_error=429
        self.page.get_by_role('button',name='Lưu & nghe thử',exact=True).click()
        expect(self.page.get_by_text(re.compile('GPU đang bận hoặc hàng đợi'))).to_be_visible()
        self.assertEqual(self.config['provider'],'omnivoice')

    def test_create_auto_manual_and_delete(self):
        self.page.get_by_role('button',name='Tạo giọng',exact=True).click()
        dialog=self.page.get_by_role('dialog')
        dialog.get_by_label('Tên giọng').fill('Giọng mới')
        dialog.get_by_label('WAV giọng mẫu').set_input_files({'name':'ref.wav','mimeType':'audio/wav','buffer':wav()})
        dialog.get_by_role('button',name='Tạo giọng',exact=True).click()
        expect(self.page.locator('article').filter(has_text='Giọng mới')).to_be_visible()
        post=next(c for c in self.calls if c[0]=='POST' and c[1]=='/ai/voice/profiles')
        self.assertNotIn('name="transcript"',post[2] or '')
        card=self.page.locator('article').filter(has_text='Giọng mới')
        card.get_by_role('button',name='Chỉnh sửa').click()
        dialog=self.page.get_by_role('dialog')
        dialog.get_by_label('Transcript',exact=True).select_option('manual')
        dialog.get_by_label('Nội dung WAV').fill('Transcript đã kiểm tra.')
        dialog.get_by_role('button',name='Lưu giọng mẫu').click()
        expect(card.get_by_text('Manual Transcript',exact=False)).to_be_visible()
        card.get_by_role('button',name='Xóa',exact=True).click()
        self.page.get_by_role('dialog').get_by_role('button',name=re.compile('Xác nhận|Xóa')).click()
        expect(self.page.locator('article').filter(has_text='Giọng mới')).to_have_count(0)

    def test_fallback_reload_and_live_guard(self):
        self.page.get_by_text('Fallback · provider dự phòng',exact=True).click()
        self.page.get_by_label('Fallback live_reply').select_option('provider')
        self.page.get_by_role('button',name='Lưu giọng nói',exact=True).click()
        expect(self.page.get_by_text('Đã lưu cấu hình giọng nói cho Agent')).to_be_visible()
        self.assertEqual(self.config['fallback']['provider']['provider'],'edge')
        self.page.reload()
        self.page.get_by_text('Fallback · provider dự phòng',exact=True).click()
        expect(self.page.get_by_label('Fallback live_reply')).to_have_value('provider')
        self.running=True
        self.page.get_by_label('Provider',exact=True).select_option('edge')
        self.page.get_by_role('button',name='Lưu giọng nói',exact=True).click()
        expect(self.page.get_by_text(re.compile('Có phiên Sarae đang RUNNING'))).to_be_visible()
        self.assertEqual(self.config['provider'],'omnivoice')

    def test_edge_elevenlabs_and_mobile(self):
        self.page.get_by_label('Provider',exact=True).select_option('edge')
        expect(self.page.get_by_label('Tốc độ (%)')).to_be_visible()
        self.page.get_by_role('button',name='Lưu & nghe thử',exact=True).click()
        expect(self.page.get_by_label('Giọng nghe thử')).to_be_visible()
        self.page.get_by_label('Provider',exact=True).select_option('elevenlabs')
        expect(self.page.get_by_role('option',name='Voice Eleven')).to_have_count(1)
        self.page.get_by_role('button',name='Lưu & nghe thử',exact=True).click()
        expect(self.page.get_by_text('Giọng thử đã sẵn sàng.',exact=False)).to_be_visible()
        self.page.set_viewport_size({'width':390,'height':844})
        self.page.get_by_label('Provider',exact=True).select_option('omnivoice')
        self.page.screenshot(path='web/tests/voice-mobile.png',full_page=True)
        self.assertLessEqual(self.page.locator('body').evaluate('(e)=>e.scrollWidth'),390)

    def test_profile_rename_replace_and_sync(self):
        card=self.page.locator('article').first
        card.get_by_role('button',name='Chỉnh sửa').click()
        self.page.get_by_role('dialog').get_by_label('Tên giọng').fill('Giọng đã đổi tên')
        self.page.get_by_role('dialog').get_by_role('button',name='Lưu giọng mẫu').click()
        expect(card.get_by_text('Giọng đã đổi tên',exact=False)).to_be_visible()
        card.get_by_role('button',name='Chỉnh sửa').click()
        self.page.get_by_role('dialog').get_by_label('WAV giọng mẫu').set_input_files({'name':'new.wav','mimeType':'audio/wav','buffer':wav()})
        self.page.get_by_role('dialog').get_by_role('button',name='Lưu giọng mẫu').click()
        expect(card.get_by_text('Chờ đồng bộ',exact=True)).to_be_visible()
        expect(self.page.get_by_role('button',name='Lưu & nghe thử',exact=True)).to_be_disabled()
        card.get_by_role('button',name='Đồng bộ lại').click()
        expect(card.get_by_text('Đã đồng bộ',exact=True)).to_be_visible()
        self.assertEqual(len([c for c in self.calls if c[0]=='PUT' and c[1].endswith('/wav')]),1)

    def test_active_readiness_and_duplicate_clicks(self):
        self.page.get_by_role('button',name='Đồng bộ TTS của phiên').evaluate('(b)=>{b.click();b.click()}')
        expect(self.page.get_by_text('Đã warmup (2 ms)',exact=True)).to_be_visible()
        self.assertEqual(len([c for c in self.calls if c[0]=='POST' and c[1]=='/ai/voice/readiness']),1)
        self.page.get_by_role('button',name='Lưu & nghe thử',exact=True).evaluate('(b)=>{b.click();b.click()}')
        expect(self.page.get_by_label('Giọng nghe thử')).to_be_visible()
        self.assertEqual(len([c for c in self.calls if c[1]=='/ai/voice/preview']),1)

    def test_offline_profiles_are_visible_and_not_ready(self):
        self.profiles[0]['sync'].update(state='PENDING',up_to_date=False)
        self.readiness.update(ready=False,warnings=['TTS_BACKEND_UNREACHABLE'],service=dict(configured=True,online=False,state=None))
        self.page.reload()
        expect(self.page.locator('article').get_by_text('Giọng shop',exact=False)).to_be_visible()
        expect(self.page.get_by_role('button',name='Lưu & nghe thử',exact=True)).to_be_disabled()
        expect(self.page.get_by_text('Offline',exact=True)).to_be_visible()
        expect(self.page.get_by_text('TTS sẵn sàng tại thời điểm kiểm tra',exact=True)).to_have_count(0)

    def test_http_409_422_429_503_show_errors(self):
        for status in (409,422,429,503):
            with self.subTest(status=status):
                self.preview_error=status
                self.page.get_by_role('button',name='Lưu & nghe thử',exact=True).click()
                text={409:'Giọng đang được sử dụng.',422:'Nội dung chưa hợp lệ.',429:'GPU đang bận hoặc hàng đợi',503:'Model TTS chưa sẵn sàng'}[status]
                expect(self.page.get_by_text(re.compile(re.escape(text)))).to_be_visible()
        self.assertEqual(self.config['provider'],'omnivoice')

    def test_full_ai_settings_screen(self):
        self.page.goto('http://127.0.0.1:5175/#/ai')
        expect(self.page.get_by_role('heading',name='Voice Settings')).to_be_visible()
        expect(self.page.get_by_role('heading',name='AI Provider Configuration')).to_be_visible()
        self.page.screenshot(path='web/tests/voice-desktop.png',full_page=True)


if __name__=='__main__':unittest.main(verbosity=2)
