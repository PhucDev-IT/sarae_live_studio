"""The Web may read public TTS error/revision headers across origins."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings


class VoiceCorsTests(unittest.TestCase):
    def test_only_public_tts_headers_are_exposed(self):
        with_headers={'Origin':settings.allowed_origins[0]}
        client=TestClient(app)
        try:
            response=client.get('/api/health',headers=with_headers)
            exposed=response.headers.get('access-control-expose-headers','').lower()
            self.assertIn('x-sarae-error',exposed)
            self.assertIn('x-voice-revision',exposed)
            self.assertNotIn('token',exposed)
            self.assertNotIn('authorization',exposed)
        finally:client.close()


if __name__=='__main__':unittest.main(verbosity=2)
