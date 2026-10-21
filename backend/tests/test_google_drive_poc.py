"""Offline tests for the pure helpers of tools/google_drive_oauth_poc.py.

They do NOT prove the Google Drive PoC passed; that needs a real Google account.
"""
import argparse
import base64
import hashlib
import importlib.util
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

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

    def test_parse_drive_id_accepts_links_and_raw_ids(self):
        self.assertEqual(poc.parse_drive_id("https://drive.google.com/file/d/AbC_1-x/view?usp=sharing"), "AbC_1-x")
        self.assertEqual(poc.parse_drive_id("https://drive.google.com/open?id=XyZ9"), "XyZ9")
        self.assertEqual(poc.parse_drive_id("  rawId123 "), "rawId123")
        self.assertEqual(poc.parse_drive_id(""), "")

    def test_redact_hides_tokens(self):
        self.assertEqual(poc.redact('bad "ya29.secret" and 1//refresh'), "bad <redacted> and <redacted>")


class _FakeDrive:
    """files.list shows the new file only from the 3rd poll; files.get denies it (drive.file-like)."""

    def __init__(self, visible_from_poll=3):
        self.polls = 0
        self.visible_from_poll = visible_from_poll

    def list_children(self, folder_id):
        self.polls += 1
        items = [{"id": "old1"}, {"id": "old2"}]
        if self.polls >= self.visible_from_poll:
            items.append({"id": "new9"})
        return 200, items, ""

    def get_file(self, file_id):
        return poc.httpx.Response(404, text='{"error":"notFound"}')

    def list_changes(self, page_token):
        return []


class CheckNewFileFlowTest(unittest.TestCase):
    def _run(self, drive, poll_seconds):
        args = argparse.Namespace(poll_seconds=poll_seconds, poll_interval=0, scope="file", repick=False, port=0)
        report = poc.Report("file")
        before = [{"id": "old1"}, {"id": "old2"}]
        with mock.patch("builtins.input", side_effect=["", "https://drive.google.com/file/d/new9/view"]), \
                mock.patch.object(poc, "log"):
            poc.check_new_file(args, drive, report, poc._Inbox(), "folder", before, "tok")
        return report.checks

    def test_polling_tolerates_propagation_delay(self):
        checks = self._run(_FakeDrive(visible_from_poll=3), poll_seconds=5)
        self.assertEqual(checks["C4"].status, "PASS")
        self.assertEqual(checks["C4"].detail["attempts"], 3)
        self.assertTrue(checks["C4"].detail["operator_file_listed"])

    def test_never_listed_fails_and_direct_get_records_404(self):
        checks = self._run(_FakeDrive(visible_from_poll=10**6), poll_seconds=0)
        self.assertEqual(checks["C4"].status, "FAIL")
        self.assertEqual(checks["C4b"].status, "FAIL")
        self.assertEqual(checks["C4b"].detail["http"], 404)
        self.assertEqual(checks["C4c"].status, "FAIL")
        self.assertEqual(checks["C4d"].status, "MANUAL_SKIPPED")


if __name__ == "__main__":
    unittest.main()
