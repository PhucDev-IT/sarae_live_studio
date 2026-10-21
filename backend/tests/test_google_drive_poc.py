"""Offline tests for the pure helpers of tools/google_drive_oauth_poc.py.

They do NOT prove the Google Drive PoC passed; that needs a real Google account.
"""
import base64
import hashlib
import importlib.util
import sys
import unittest
import urllib.parse
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "tools" / "google_drive_oauth_poc.py"
_spec = importlib.util.spec_from_file_location("google_drive_oauth_poc", _PATH)
poc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = poc  # dataclasses resolve annotations via sys.modules
_spec.loader.exec_module(poc)


class GoogleDrivePocHelpersTest(unittest.TestCase):
    def test_pkce_challenge_is_s256_of_verifier(self):
        verifier, challenge = poc.pkce_pair()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(challenge, expected)
        self.assertGreaterEqual(len(verifier), 43)

    def test_state_must_match_exactly(self):
        self.assertTrue(poc.state_matches("abc", "abc"))
        self.assertFalse(poc.state_matches("abc", "abd"))
        self.assertFalse(poc.state_matches("abc", None))
        self.assertFalse(poc.state_matches("abc", ""))

    def test_authorize_url_requests_offline_pkce(self):
        url = poc.build_authorize_url("cid", "http://127.0.0.1:8765/callback", poc.SCOPES["file"], "st", "ch")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(q["access_type"], ["offline"])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertEqual(q["state"], ["st"])
        self.assertEqual(q["scope"], ["https://www.googleapis.com/auth/drive.file"])

    def test_folder_query_escapes_quotes(self):
        self.assertEqual(poc.folder_children_query("a'b"), "'a\\'b' in parents and trashed = false")

    def test_diff_ids(self):
        before = [{"id": "1"}, {"id": "2"}]
        after = [{"id": "2"}, {"id": "3"}]
        self.assertEqual(poc.diff_ids(before, after), {"added": ["3"], "removed": ["1"]})

    def test_redact_hides_tokens(self):
        self.assertEqual(poc.redact('bad "ya29.secret" and 1//refresh'), "bad <redacted> and <redacted>")


if __name__ == "__main__":
    unittest.main()
