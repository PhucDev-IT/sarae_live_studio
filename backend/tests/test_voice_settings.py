"""Supported voice settings and compatibility with retired provider data."""
from fastapi import HTTPException
from app import credentials, identity
from app.cloud_agents import _tts_spec
from support import SaasTestCase


class VoiceSettingsTests(SaasTestCase):
    def test_edge_settings_are_owner_scoped(self):
        config = dict(provider='edge', model='edge-neural', voice='vi-VN-NamMinhNeural', rate=10, settings={})
        response = self.client.put('/api/ai/voice/config', json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get('/api/ai/voice/config').json()['voice'], config['voice'])
        self.assertEqual(self.client.get('/api/ai/voice/config', headers=self.B).json()['voice'], 'vi-VN-HoaiMyNeural')
        self.assertNotIn('has_api_key', response.json())

    def test_removed_provider_and_fallback_are_rejected(self):
        retired = dict(provider='elevenlabs', model='eleven_flash_v2_5', voice='voice123', rate=0, settings={})
        self.assertEqual(self.client.put('/api/ai/voice/config', json=retired).status_code, 422)
        response = self.client.put('/api/ai/voice/config', json=dict(fallback=dict(manual='provider', provider=retired)))
        self.assertEqual(response.status_code, 422)
        with self.assertRaises(HTTPException) as error:
            _tts_spec(retired)
        self.assertEqual(error.exception.status_code, 422)
        with self.assertRaises(HTTPException):
            credentials.save(self.owner_a['id'], 'elevenlabs', 'test-only-retired-key')

    def test_removed_endpoints_are_not_available(self):
        for method, path in [('PUT','/api/ai/voice/key'), ('DELETE','/api/ai/voice/key'),
                             ('GET','/api/ai/voice/catalog'), ('GET','/api/agents/voice-key/test-agent')]:
            response = self.client.request(method, path, json={'api_key':'test-only-retired-key'})
            self.assertIn(response.status_code, (404, 405), path)
        schema = self.client.get('/openapi.json').json()['paths']
        self.assertNotIn('/api/ai/voice/key', schema)
        self.assertNotIn('/api/agents/voice-key/{agent_id}', schema)

    def test_legacy_default_is_readable_without_writing_storage(self):
        retired = dict(provider='elevenlabs', model='old-model', voice='old-voice', rate=0, settings={})
        identity.update_owner_settings(self.owner_a['id'], voice=retired)
        response = self.client.get('/api/ai/voice/config')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['provider'], 'edge')
        self.assertTrue(response.json()['migration_notice'])
        self.assertEqual(identity.owner_settings(self.owner_a['id'])['voice'], retired)
        self.assertEqual(self.client.put('/api/ai/voice/config', json={'voice':'vi-VN-NamMinhNeural'}).status_code, 200)
        self.assertIsNone(self.client.get('/api/ai/voice/config').json()['migration_notice'])

    def test_legacy_fallback_disabled_without_changing_omnivoice(self):
        config = dict(provider='omnivoice', model='demo_default', voice='a'*32, rate=0, settings={},
                      fallback=dict(live_reply='comment_only', manual='provider', prepare='provider',
                                    provider=dict(provider='elevenlabs', model='old', voice='old')))
        identity.update_owner_settings(self.owner_a['id'], voice=config)
        response = self.client.get('/api/ai/voice/config')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual((data['provider'], data['voice']), ('omnivoice', 'a'*32))
        self.assertEqual(data['fallback'], dict(live_reply='comment_only', manual='none', prepare='none', provider=None))
        self.assertTrue(data['migration_notice'])

    def test_invalid_edge_settings_still_rejected(self):
        self.assertEqual(self.client.put('/api/ai/voice/config', json={'settings':{'speed':1.1}}).status_code, 422)
        with self.assertRaises(HTTPException):
            _tts_spec({'provider':'edge','settings':{'speed':1.1}})
