"""Opt-in real model + HTTP Backend + Agent VoiceEngine + OBS offline smoke test.

Requires SARAE_TTS_OBS_E2E=1, SARAE_TTS_REAL_URL/TOKEN/REF. Never starts streaming.
Uses isolated SaaS fixture databases and restores the existing Voice Media Source settings.
"""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'agent-core/src'))
sys.path.append(str(ROOT / 'agent-core/.venv/Lib/site-packages'))

from support import SaasTestCase


@unittest.skipUnless(os.environ.get('SARAE_TTS_OBS_E2E') == '1', 'explicit OBS offline opt-in required')
class AgentObsOfflineTests(SaasTestCase):
    def test_real_agent_playback(self):
        import httpx
        import obsws_python
        import uvicorn
        from app import storage_providers, tts_server, voice_profiles, tts_orchestrator
        from app.config import settings
        from app.main import app
        from memory_storage import MemoryStorageProvider, MemoryStore
        from sarae_agent.config import ConfigStore
        from sarae_agent.obs.controller import ObsController
        from sarae_agent.voice.tts import VoiceEngine
        from sarae_agent.voice.omnivoice import OmniVoiceProvider
        from sarae_agent.cloud.client import CloudClient
        from sarae_agent.cloud.commands import CommandRouter
        from sarae_agent.cloud.journal import EventOutbox

        obs_cfg = json.loads((Path(os.environ['APPDATA']) / 'obs-studio/plugin_config/obs-websocket/config.json').read_text())
        ws = obsws_python.ReqClient(host='127.0.0.1', port=obs_cfg['server_port'],
                                    password=obs_cfg.get('server_password', ''), timeout=4)
        self.assertFalse(ws.send('GetStreamStatus', raw=True)['outputActive'])
        self.assertFalse(ws.send('GetRecordStatus', raw=True)['outputActive'])
        original = ws.send('GetInputSettings', {'inputName': 'Sarae Voice'}, raw=True)['inputSettings']
        mute = ws.send('GetInputMute', {'inputName': 'Sarae Voice'}, raw=True)['inputMuted']
        volumes = {name: ws.send('GetInputVolume', {'inputName': name}, raw=True)['inputVolumeDb']
                   for name in ('Sarae Video A', 'Sarae Video B')}
        MemoryStorageProvider.store = MemoryStore()
        storage_providers.register('MEMORY', MemoryStorageProvider)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8794, lifespan='off', log_level='warning'))
        thread = threading.Thread(target=server.run, daemon=True)
        voice = None
        restarted_backend = None
        with patch.multiple(settings, sarae_tts_server_url=os.environ['SARAE_TTS_REAL_URL'],
                sarae_tts_server_token=os.environ['SARAE_TTS_REAL_TOKEN'], sarae_tts_timeout_seconds=30,
                sarae_tts_agent_url=os.environ['SARAE_TTS_REAL_URL'], sarae_storage_provider='MEMORY'), \
                patch.dict(os.environ, SARAE_AGENT_HOME=str(Path(self.tmp.name) / 'agent')):
            try:
                tts_server.set_transport(None)
                voice_profiles._verified.clear()
                thread.start()
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(.05)
                self.assertTrue(server.started)
                with httpx.Client(base_url='http://127.0.0.1:8794', headers=self.A, timeout=60) as web:
                    r = web.post('/api/ai/voice/profiles', files={'wav': ('ref.wav', Path(os.environ['SARAE_TTS_REAL_REF']).read_bytes(), 'audio/wav')}, data={'name': 'Phase4 OBS offline'})
                    self.assertEqual(r.status_code, 201, r.text)
                    profile = r.json()['id']
                    r = web.put('/api/ai/voice/config', json=dict(provider='omnivoice', model='demo_default', voice=profile, rate=0, settings={}))
                    self.assertEqual(r.status_code, 200, r.text)
                    agent = self.activate_agent(self.owner_a, 'Phase4-offline')
                    aid, key = agent['agent_id'], agent['agent_credential']
                    cfg = ConfigStore()
                    cfg.update({'obs': {'voice_input': 'Sarae Voice', 'media_input': 'Sarae Video A'}})
                    obs = ObsController(cfg, lambda *a: None, client_factory=lambda *a: ws)
                    obs.connect()
                    voice = VoiceEngine(cfg, obs)
                    voice.job_provider = OmniVoiceProvider(lambda: (SimpleNamespace(agent_id=aid, credential=key), 'http://127.0.0.1:8794/api'))
                    events = []
                    box = EventOutbox(path=Path(self.tmp.name) / 'events.json')
                    def emit(event):
                        events.append({**event, 'observed_at': time.monotonic()})
                        box.push(event)
                    voice.event_sink = emit
                    router = CommandRouter(playback=Mock(), monitor=Mock(), voice=voice)
                    results = []
                    with CloudClient('http://127.0.0.1:8794/api') as cloud:
                        cloud.heartbeat(key, {'agent_id': aid, 'hostname': 'offline', 'voice': voice.status()})
                        for path in ('direct', 'relay', 'token_lost'):
                            settings.sarae_tts_agent_url = os.environ['SARAE_TTS_REAL_URL'] if path != 'relay' else ''
                            t0 = time.monotonic()
                            r = web.post('/api/ai/voice/speak', json={'agent_id': aid, 'text': 'Dạ có size Mờ nha chị.'})
                            self.assertEqual(r.status_code, 200, r.text)
                            command = cloud.next_command(key, aid)
                            self.assertIsNotNone(command)
                            if path == 'token_lost':
                                # Stop the original HTTP server and restart the real Backend in a new
                                # process, preserving a snapshot of its databases but no transient token.
                                from app import identity, cloud_agents
                                server.should_exit = True
                                thread.join(5)
                                self.assertFalse(thread.is_alive())
                                data = Path(self.tmp.name) / 'restarted-backend'
                                data.mkdir()
                                raw = self.engine.raw_connection()
                                target = sqlite3.connect(data / 'sarae_live.db')
                                raw.driver_connection.backup(target)
                                target.close(); raw.close()
                                for source, name in ((identity.DB, 'identity.sqlite3'), (cloud_agents.DB, 'agents.sqlite3')):
                                    source_db = sqlite3.connect(source)
                                    target_db = sqlite3.connect(data / name)
                                    source_db.backup(target_db)
                                    target_db.close(); source_db.close()
                                env = {**os.environ, 'SARAE_DATA_DIR': str(data),
                                    'SARAE_TTS_SERVER_URL': os.environ['SARAE_TTS_REAL_URL'],
                                    'SARAE_TTS_SERVER_TOKEN': os.environ['SARAE_TTS_REAL_TOKEN'],
                                    'SARAE_TTS_AGENT_URL': os.environ['SARAE_TTS_REAL_URL']}
                                restarted_backend = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app',
                                    '--host', '127.0.0.1', '--port', '8794', '--lifespan', 'off', '--log-level', 'warning'],
                                    cwd=ROOT / 'backend', env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                for _ in range(200):
                                    try:
                                        if httpx.get('http://127.0.0.1:8794/openapi.json', timeout=1).status_code == 200:
                                            break
                                    except httpx.TransportError:
                                        time.sleep(.1)
                                else:
                                    self.fail('restarted Backend did not start')
                            result = router.execute(command['kind'], command['payload'], command_id=command['command_id'], session_id=command.get('session_id'))
                            cloud.ack(key, aid, command, True, json.dumps(result))
                            deadline = time.monotonic() + 60
                            uid = command['payload']['tts']['utterance_id']
                            while time.monotonic() < deadline:
                                if any(e['detail']['utterance_id'] == uid and e['detail']['status'] in ('played', 'failed', 'expired') for e in events):
                                    break
                                time.sleep(.05)
                            clips = [e for e in events if e['detail']['utterance_id'] == uid]
                            self.assertEqual(clips[-1]['detail']['status'], 'played', clips)
                            start = next(e for e in clips if e['detail']['status'] == 'playing')
                            self.assertEqual(start['detail']['audio_path'], 'relay' if path == 'token_lost' else path)
                            results.append({'path': path, 'backend_text_to_obs_playing_ms': round((start['observed_at'] - t0) * 1000), **start['detail']})
                            box.flush(lambda batch: cloud.post_events(key, aid, batch))
                            self.assertEqual(router.execute(command['kind'], command['payload'])['status'], 'duplicate')
                            for name, volume in volumes.items():
                                self.assertAlmostEqual(ws.send('GetInputVolume', {'inputName': name}, raw=True)['inputVolumeDb'], volume, places=3)
                        print('OBS_OFFLINE_RESULT', json.dumps(results))
                    tts_server.delete_voice(profile)
            finally:
                if voice:
                    voice.clear()
                    voice.close()
                ws.send('SetInputSettings', {'inputName': 'Sarae Voice', 'inputSettings': original, 'overlay': False}, raw=True)
                ws.send('SetInputMute', {'inputName': 'Sarae Voice', 'inputMuted': mute}, raw=True)
                ws.disconnect()
                server.should_exit = True
                thread.join(5)
                if restarted_backend:
                    restarted_backend.terminate()
                    restarted_backend.wait(10)
                storage_providers.unregister('MEMORY')
