"""Centralized Media Library: admin-only system storage, uploads, owner isolation, streaming, Product–Video N:N,
Agent access, provider migration and LOCAL_LEGACY safety. Google is the in-process fake (tests/fake_google_drive.py).
"""
import hashlib
import os
import re
import urllib.parse
from datetime import timedelta
from unittest.mock import patch

import httpx
from cryptography.fernet import Fernet

from app import credentials, google_api, storage_providers, storage_service
from app.config import settings
from app.storage_models import MediaItem, MediaStorageObject, MediaUpload, StorageCredential, now
from fake_google_drive import FakeGoogle
from memory_storage import MemoryStorageProvider, MemoryStore
from support import SaasTestCase

ALIGN = storage_service.CHUNK_ALIGN
JPEG = b"\xff\xd8\xff\xe0" + b"cover" * 50


def video_bytes(size=600_000, seed=b"a"):
    head = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    body = hashlib.sha256(seed).digest() * (size // 32 + 1)
    return (head + body)[:size]


class MediaLibraryTestCase(SaasTestCase):
    def setUp(self):
        super().setUp()
        self.google = FakeGoogle()
        google_api.set_transport(httpx.MockTransport(self.google.handler))
        self.settings_patch = patch.multiple(settings, sarae_google_client_id="client-id.apps.googleusercontent.com",
                                             sarae_google_client_secret="client-secret",
                                             sarae_google_redirect_uri="http://api.test/api/storage-admin/google/callback",
                                             sarae_web_app_url="http://web.test", sarae_storage_provider="GOOGLE_DRIVE",
                                             sarae_credential_key=Fernet.generate_key().decode(),
                                             sarae_media_owner_quota_bytes=0)
        self.settings_patch.start()
        credentials._fernet = None
        storage_service._hashers.clear()

    def tearDown(self):
        google_api.set_transport(None)
        self.settings_patch.stop()
        credentials._fernet = None
        storage_providers.unregister("MEMORY")
        super().tearDown()

    # ----- helpers -----
    def connect_storage(self):
        url = self.client.post("/api/storage-admin/google/authorize", headers=self.ADMIN).json()["url"]
        code, state = self.google.approve(url)
        response = self.client.get("/api/storage-admin/google/callback", params={"code": code, "state": state},
                                   headers=self.ANON, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        return response.headers["location"]

    def start_upload(self, data, headers=None, filename="clip.mp4", client_id=None):
        body = {"client_upload_id": client_id or os.urandom(8).hex(), "filename": filename, "size_bytes": len(data)}
        return self.client.post("/api/media-library/uploads", json=body, headers=headers or self.A)

    def send(self, upload_id, data, start, end, headers=None):
        return self.client.put(f"/api/media-library/uploads/{upload_id}/chunk", content=data[start:end + 1],
                               headers={**(headers or self.A), "Content-Range": f"bytes {start}-{end}/{len(data)}"})

    def upload(self, data=None, headers=None, filename="clip.mp4"):
        data = data or video_bytes()
        created = self.start_upload(data, headers, filename)
        self.assertEqual(created.status_code, 201, created.text)
        upload = created.json()
        for start in range(0, len(data), ALIGN):
            response = self.send(upload["id"], data, start, min(start + ALIGN, len(data)) - 1, headers)
            self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "COMPLETED", result)
        return result["media_id"]

    def media(self, media_id, headers=None):
        return self.client.get(f"/api/media-library/{media_id}", headers=headers or self.A)

    def relative(self, url):
        return url  # signed URLs are server-relative


class AdminStorageConnectionTest(MediaLibraryTestCase):
    def test_only_super_admin_manages_system_storage(self):
        self.assertEqual(self.client.get("/api/storage-admin/status").status_code, 403)
        self.assertEqual(self.client.post("/api/storage-admin/google/authorize").status_code, 403)
        self.assertEqual(self.client.get("/api/storage-admin/status", headers=self.ANON).status_code, 401)
        self.assertEqual(self.client.delete("/api/storage-admin/google/connection", headers=self.B).status_code, 403)

    def test_connect_encrypts_tokens_and_creates_root_folder(self):
        location = self.connect_storage()
        self.assertEqual(location, "http://web.test/#/admin/storage?storage=connected")
        status = self.client.get("/api/storage-admin/status", headers=self.ADMIN)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["google"]["status"], "CONNECTED")
        self.assertEqual(status.json()["google"]["account_email"], "storage@sarae.test")
        self.assertNotIn("ya29.", status.text)
        self.assertNotIn("1//", status.text)
        with self.Session() as db:
            row = db.get(StorageCredential, "GOOGLE_DRIVE")
            self.assertNotIn("1//", row.refresh_token_enc)
            self.assertNotIn("ya29.", row.access_token_enc)
            self.assertTrue(credentials.decrypt(row.refresh_token_enc).startswith("1//fake-refresh-"))
            root = self.google.folder_by_prop("sarae_role", "root")
            self.assertEqual(row.root_container_id, root["id"])
            self.assertEqual(root["name"], "Sarae Media Storage")

    def test_callback_rejects_unknown_replayed_and_expired_state(self):
        url = self.client.post("/api/storage-admin/google/authorize", headers=self.ADMIN).json()["url"]
        code, state = self.google.approve(url)
        bad = self.client.get("/api/storage-admin/google/callback", params={"code": code, "state": "forged"}, follow_redirects=False)
        self.assertIn("storage_error=STORAGE_STATE_INVALID", bad.headers["location"])
        ok = self.client.get("/api/storage-admin/google/callback", params={"code": code, "state": state}, follow_redirects=False)
        self.assertIn("storage=connected", ok.headers["location"])
        replay = self.client.get("/api/storage-admin/google/callback", params={"code": code, "state": state}, follow_redirects=False)
        self.assertIn("storage_error=STORAGE_STATE_INVALID", replay.headers["location"])

        url = self.client.post("/api/storage-admin/google/authorize", headers=self.ADMIN).json()["url"]
        code, state = self.google.approve(url)
        with patch("app.storage_admin.now", return_value=now() + timedelta(minutes=11)):
            expired = self.client.get("/api/storage-admin/google/callback", params={"code": code, "state": state}, follow_redirects=False)
        self.assertIn("storage_error=STORAGE_STATE_EXPIRED", expired.headers["location"])

    def test_consent_denied_and_missing_scope_are_rejected(self):
        url = self.client.post("/api/storage-admin/google/authorize", headers=self.ADMIN).json()["url"]
        _, state = self.google.approve(url)
        denied = self.client.get("/api/storage-admin/google/callback", params={"error": "access_denied", "state": state}, follow_redirects=False)
        self.assertIn("storage_error=STORAGE_CONSENT_DENIED", denied.headers["location"])

        self.google.granted_scopes = ""  # the admin unticked the Drive permission on the consent screen
        url = self.client.post("/api/storage-admin/google/authorize", headers=self.ADMIN).json()["url"]
        code, state = self.google.approve(url)
        missing = self.client.get("/api/storage-admin/google/callback", params={"code": code, "state": state}, follow_redirects=False)
        self.assertIn("storage_error=STORAGE_SCOPE_MISSING", missing.headers["location"])
        self.assertEqual(self.client.get("/api/storage-admin/status", headers=self.ADMIN).json()["google"]["status"], "NOT_CONNECTED")

    def test_authorize_requires_server_configuration(self):
        with patch.object(settings, "sarae_google_client_id", ""):
            response = self.client.post("/api/storage-admin/google/authorize", headers=self.ADMIN)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["X-Sarae-Error"], "STORAGE_NOT_CONFIGURED")

    def test_access_token_is_refreshed_and_revocation_is_detected(self):
        self.connect_storage()
        self.google.expire_access_tokens()
        self.upload()
        self.assertGreaterEqual(self.google.refresh_calls, 1)

        self.google.revoke_everything()
        with self.Session() as db:
            db.get(StorageCredential, "GOOGLE_DRIVE").access_expires_at = now() - timedelta(minutes=1)
            db.commit()
        response = self.start_upload(video_bytes())
        self.assertEqual(response.status_code, 503)  # never 401: that would sign the customer out
        self.assertEqual(response.headers["X-Sarae-Error"], "STORAGE_TOKEN_REVOKED")
        self.assertEqual(self.client.get("/api/storage-admin/status", headers=self.ADMIN).json()["google"]["status"], "REVOKED")

    def test_disconnect_revokes_at_google_and_keeps_media(self):
        self.connect_storage()
        media_id = self.upload()
        response = self.client.delete("/api/storage-admin/google/connection", headers=self.ADMIN)
        self.assertEqual(response.json(), {"status": "DISCONNECTED", "revoked_at_google": True})
        self.assertFalse(any(self.google.refresh_tokens.values()))
        with self.Session() as db:
            self.assertIsNone(db.get(StorageCredential, "GOOGLE_DRIVE").refresh_token_enc)
        self.assertEqual(self.media(media_id).status_code, 200)
        blocked = self.start_upload(video_bytes())
        self.assertEqual(blocked.headers["X-Sarae-Error"], "STORAGE_NOT_CONNECTED")
        # Reconnecting the same account restores access to the existing files.
        self.connect_storage()
        self.assertEqual(self.client.get(self.media(media_id).json()["stream_url"], headers=self.ANON).status_code, 200)

    def test_switching_google_account_is_blocked_while_media_exist(self):
        self.connect_storage()
        self.upload()
        self.google.account = {**self.google.account, "permissionId": "perm-2", "emailAddress": "other@gmail.test"}
        self.assertIn("storage_error=STORAGE_ACCOUNT_MISMATCH", self.connect_storage())
        status = self.client.get("/api/storage-admin/status", headers=self.ADMIN).json()["google"]
        self.assertEqual(status["account_email"], "storage@sarae.test")


class UploadTest(MediaLibraryTestCase):
    def setUp(self):
        super().setUp()
        self.connect_storage()

    def test_chunked_upload_lands_in_owner_folder_with_checksums(self):
        data = video_bytes()
        media_id = self.upload(data, filename="Áo thun đỏ.MP4")
        media = self.media(media_id).json()
        self.assertEqual(media["checksum_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(media["size_bytes"], len(data))
        self.assertEqual(media["display_name"], "Áo thun đỏ")
        self.assertEqual(media["duration_seconds"], 12.5)
        for secret in ("file-", "sess", "googleapis", "provider"):
            self.assertNotIn(secret, str({k: v for k, v in media.items() if k not in ("stream_url", "thumb_url")}))
        with self.Session() as db:
            item = db.get(MediaItem, media_id)
            stored = self.google.files[item.provider_file_id]
        owner_folder = self.google.folder_by_prop("sarae_owner", self.owner_a["id"])
        self.assertEqual(stored["parents"], [owner_folder["id"]])
        self.assertEqual(owner_folder["parents"], [self.google.folder_by_prop("sarae_role", "root")["id"]])
        self.assertEqual(stored["name"], f"{media_id}.mp4")  # the customer's filename is not used in storage
        self.assertEqual(self.google.content[item.provider_file_id], data)
        listed = self.client.get("/api/media-library").json()
        self.assertEqual([m["id"] for m in listed["items"]], [media_id])

    def test_retried_create_and_chunks_never_duplicate_the_file(self):
        data = video_bytes()
        first = self.start_upload(data, client_id="client-upload-1").json()
        again = self.start_upload(data, client_id="client-upload-1").json()
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(len(self.google.sessions), 1)
        self.assertEqual(self.send(first["id"], data, 0, ALIGN - 1).json()["received_bytes"], ALIGN)
        retried = self.send(first["id"], data, 0, ALIGN - 1)  # the reply was lost; the browser sends it again
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json()["received_bytes"], ALIGN)
        self.send(first["id"], data, ALIGN, 2 * ALIGN - 1)
        done = self.send(first["id"], data, 2 * ALIGN, len(data) - 1).json()
        self.assertEqual(done["status"], "COMPLETED")
        self.assertEqual(len(self.google.content), 1)
        conflict = self.start_upload(video_bytes(1_000_000), client_id="client-upload-1")
        self.assertEqual(conflict.headers["X-Sarae-Error"], "UPLOAD_ID_REUSED")

    def test_resume_uses_the_offset_the_provider_really_has(self):
        data = video_bytes()
        upload = self.start_upload(data).json()
        self.send(upload["id"], data, 0, ALIGN - 1)
        with self.Session() as db:
            session_uri = credentials.decrypt(db.get(MediaUpload, upload["id"]).session_enc)
        self.google.append_directly(session_uri, data[ALIGN:2 * ALIGN])  # stored by Google, reply never arrived
        skipped = self.send(upload["id"], data, 2 * ALIGN, len(data) - 1)
        self.assertEqual(skipped.status_code, 409)
        self.assertEqual(skipped.headers["X-Sarae-Error"], "UPLOAD_OFFSET_MISMATCH")
        status = self.client.get(f"/api/media-library/uploads/{upload['id']}").json()
        self.assertEqual(status["received_bytes"], 2 * ALIGN)
        done = self.send(upload["id"], data, 2 * ALIGN, len(data) - 1).json()
        self.assertEqual(done["status"], "COMPLETED")
        media = self.media(done["media_id"]).json()
        self.assertIsNone(media["checksum_sha256"])  # Backend did not see every byte; provider md5 is kept
        with self.Session() as db:
            self.assertEqual(db.get(MediaItem, done["media_id"]).md5, hashlib.md5(data).hexdigest())

    def test_upload_validation(self):
        self.assertEqual(self.start_upload(b"x" * 10, filename="a.exe").headers["X-Sarae-Error"], "MEDIA_TYPE_NOT_ALLOWED")
        with patch.object(settings, "sarae_media_max_upload_bytes", 1000):
            self.assertEqual(self.start_upload(video_bytes()).status_code, 413)
        with patch.object(settings, "sarae_media_owner_quota_bytes", 700_000):
            self.upload()
            self.assertEqual(self.start_upload(video_bytes()).headers["X-Sarae-Error"], "MEDIA_QUOTA_EXCEEDED")
        data = video_bytes()
        upload = self.start_upload(data).json()
        self.assertEqual(self.send(upload["id"], data, 0, 999).headers["X-Sarae-Error"], "UPLOAD_CHUNK_ALIGN")
        missing = self.client.put(f"/api/media-library/uploads/{upload['id']}/chunk", content=b"x")
        self.assertEqual(missing.headers["X-Sarae-Error"], "UPLOAD_RANGE_INVALID")

    def test_non_video_content_is_rejected_and_session_cancelled(self):
        data = b"MZ" + os.urandom(300_000)
        upload = self.start_upload(data, filename="fake.mp4").json()
        response = self.send(upload["id"], data, 0, ALIGN - 1)
        self.assertEqual(response.headers["X-Sarae-Error"], "MEDIA_CONTENT_INVALID")
        self.assertTrue(next(iter(self.google.sessions.values()))["cancelled"])
        self.assertEqual(self.client.get(f"/api/media-library/uploads/{upload['id']}").json()["status"], "FAILED")

    def test_cancel_upload(self):
        data = video_bytes()
        upload = self.start_upload(data).json()
        self.send(upload["id"], data, 0, ALIGN - 1)
        cancelled = self.client.delete(f"/api/media-library/uploads/{upload['id']}")
        self.assertEqual(cancelled.json()["status"], "CANCELLED")
        self.assertTrue(next(iter(self.google.sessions.values()))["cancelled"])
        self.assertEqual(self.send(upload["id"], data, ALIGN, 2 * ALIGN - 1).headers["X-Sarae-Error"], "UPLOAD_NOT_ACTIVE")
        self.assertEqual(self.client.get("/api/media-library").json()["total"], 0)

    def test_corrupted_upload_is_detected_by_md5(self):
        self.google.corrupt_uploads = True
        data = video_bytes()
        upload = self.start_upload(data).json()
        self.send(upload["id"], data, 0, ALIGN - 1)
        self.send(upload["id"], data, ALIGN, 2 * ALIGN - 1)
        response = self.send(upload["id"], data, 2 * ALIGN, len(data) - 1)
        self.assertEqual(response.headers["X-Sarae-Error"], "STORAGE_CHECKSUM_MISMATCH")
        self.assertEqual(self.google.content, {})  # the bad copy was deleted
        self.assertEqual(self.client.get("/api/media-library").json()["total"], 0)


class LibraryTest(MediaLibraryTestCase):
    def setUp(self):
        super().setUp()
        self.connect_storage()
        self.data = video_bytes()
        self.media_id = self.upload(self.data)

    def test_other_owner_can_never_reach_the_media(self):
        self.assertEqual(self.client.get("/api/media-library", headers=self.B).json()["total"], 0)
        self.assertEqual(self.media(self.media_id, self.B).status_code, 404)
        self.assertEqual(self.client.patch(f"/api/media-library/{self.media_id}", json={"display_name": "x"}, headers=self.B).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/media-library/{self.media_id}", headers=self.B).status_code, 404)
        self.assertEqual(self.client.put(f"/api/media-library/{self.media_id}/products", json={"product_ids": []}, headers=self.B).status_code, 404)
        upload = self.start_upload(self.data).json()
        self.assertEqual(self.client.get(f"/api/media-library/uploads/{upload['id']}", headers=self.B).status_code, 404)
        self.assertEqual(self.send(upload["id"], self.data, 0, ALIGN - 1, headers=self.B).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/media-library/uploads/{upload['id']}", headers=self.B).status_code, 404)
        b_product = self.tiktok_products(self.owner_b, "B shirt")[0]
        a_product = self.tiktok_products(self.owner_a, "A shirt", seller="6001")[0]
        linked = self.client.put(f"/api/products/{b_product}/videos", json={"media_ids": [self.media_id]}, headers=self.B)
        self.assertEqual(linked.headers["X-Sarae-Error"], "MEDIA_NOT_FOUND")
        self.assertEqual(self.client.get(f"/api/products/{a_product}/videos", headers=self.B).status_code, 404)
        # B's own uploads live in B's own folder.
        b_media = self.upload(video_bytes(seed=b"b"), headers=self.B)
        with self.Session() as db:
            b_file = self.google.files[db.get(MediaItem, b_media).provider_file_id]
        self.assertEqual(b_file["parents"], [self.google.folder_by_prop("sarae_owner", self.owner_b["id"])["id"]])
        self.assertEqual([m["id"] for m in self.client.get("/api/media-library").json()["items"]], [self.media_id])

    def test_stream_supports_range_and_signed_urls_only(self):
        url = self.media(self.media_id).json()["stream_url"]
        partial = self.client.get(url, headers={**self.ANON, "Range": "bytes=100-199"})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, self.data[100:200])
        self.assertEqual(partial.headers["content-range"], f"bytes 100-199/{len(self.data)}")
        self.assertEqual(partial.headers["accept-ranges"], "bytes")
        full = self.client.get(url, headers=self.ANON)
        self.assertEqual((full.status_code, full.content), (200, self.data))
        tampered = re.sub(r"sig=[^&]+", "sig=AAAA", url)
        self.assertEqual(self.client.get(tampered, headers=self.ANON).status_code, 403)
        unsigned = url.split("?")[0]
        self.assertEqual(self.client.get(unsigned, headers=self.ANON).status_code, 403)
        query = dict(urllib.parse.parse_qsl(url.split("?")[1]))
        with patch("app.media_access.time.time", return_value=int(query["exp"]) + 1):
            self.assertEqual(self.client.get(url, headers=self.ANON).status_code, 403)
        self.assertIsNone(self.media(self.media_id).json()["thumb_url"])  # no cover yet
        self.client.put(f"/api/media-library/{self.media_id}/thumbnail", content=JPEG, headers={"Content-Type": "image/jpeg"})
        thumb = self.client.get(self.media(self.media_id).json()["thumb_url"], headers=self.ANON)
        self.assertEqual((thumb.status_code, thumb.headers["content-type"], thumb.content), (200, "image/jpeg", JPEG))
        self.client.delete(f"/api/media-library/{self.media_id}")
        self.assertEqual(self.client.get(url, headers=self.ANON).status_code, 410)

    def test_metadata_edit_does_not_touch_the_stored_file(self):
        with self.Session() as db:
            file_id = db.get(MediaItem, self.media_id).provider_file_id
        before = dict(self.google.files[file_id])
        patched = self.client.patch(f"/api/media-library/{self.media_id}", json={"display_name": "Áo khoác", "notes": "quay ngày 21", "tags": "áo,mới"})
        self.assertEqual(patched.json()["display_name"], "Áo khoác")
        self.assertEqual(self.google.files[file_id], before)
        self.assertEqual(self.client.get("/api/media-library", params={"q": "khoác"}).json()["total"], 1)

    def test_product_video_many_to_many_with_order(self):
        p1, p2 = self.tiktok_products(self.owner_a, "Áo", "Quần")
        second = self.upload(video_bytes(seed=b"2"))
        third = self.upload(video_bytes(seed=b"3"))
        put = self.client.put(f"/api/products/{p1}/videos", json={"media_ids": [third, self.media_id, second]})
        self.assertEqual(put.json()["media_ids"], [third, self.media_id, second])
        self.client.put(f"/api/media-library/{self.media_id}/products", json={"product_ids": [p1, p2]})
        self.assertEqual(self.client.get(f"/api/products/{p1}/videos").json()["media_ids"], [third, self.media_id, second])
        self.assertEqual(self.client.get(f"/api/products/{p2}/videos").json()["media_ids"], [self.media_id])
        self.assertEqual(self.media(self.media_id).json()["product_ids"], [p1, p2])
        self.assertEqual(self.client.get("/api/media-library", params={"product_id": p2}).json()["total"], 1)
        dup = self.client.put(f"/api/products/{p1}/videos", json={"media_ids": [second, second]})
        self.assertEqual(dup.headers["X-Sarae-Error"], "MEDIA_DUPLICATE")
        self.client.delete(f"/api/media-library/{second}")
        kept = self.client.get(f"/api/products/{p1}/videos").json()
        self.assertEqual((kept["media_ids"], kept["available_count"]), ([third, self.media_id, second], 2))
        new_link = self.client.put(f"/api/products/{p2}/videos", json={"media_ids": [self.media_id, second]})
        self.assertEqual(new_link.headers["X-Sarae-Error"], "MEDIA_NOT_AVAILABLE")


class CoverAndPresenterTest(MediaLibraryTestCase):
    def setUp(self):
        super().setUp()
        self.connect_storage()
        self.media_id = self.upload()

    def put_cover(self, body=JPEG, content_type="image/jpeg", headers=None, **params):
        return self.client.put(f"/api/media-library/{self.media_id}/thumbnail", content=body, params=params,
                               headers={**(headers or self.A), "Content-Type": content_type})

    def test_cover_is_stored_versioned_and_fills_missing_metadata(self):
        with self.Session() as db:
            item = db.get(MediaItem, self.media_id)
            item.duration_seconds = item.width = item.height = None  # provider has not processed the video yet
            db.commit()
        first = self.put_cover(duration_seconds=33.25, width=720, height=1280).json()
        self.assertTrue(first["has_thumbnail"])
        self.assertEqual((first["duration_seconds"], first["width"], first["height"]), (33.25, 720, 1280))
        with patch("app.media_library.now", return_value=now() + timedelta(seconds=5)):
            second = self.put_cover(body=b"\x89PNG" + b"x" * 40, content_type="image/png", duration_seconds=99).json()
        self.assertNotEqual(first["thumb_url"], second["thumb_url"])  # cache busting
        self.assertEqual(second["duration_seconds"], 33.25)  # client hints never overwrite known metadata
        image = self.client.get(second["thumb_url"], headers=self.ANON)
        self.assertEqual(image.headers["content-type"], "image/png")
        listed = self.client.get("/api/media-library").json()["items"][0]
        self.assertTrue(listed["has_thumbnail"])

    def test_cover_validation_and_isolation(self):
        self.assertEqual(self.put_cover(content_type="image/gif").headers["X-Sarae-Error"], "THUMBNAIL_TYPE_NOT_ALLOWED")
        self.assertEqual(self.put_cover(body=b"not-a-jpeg").headers["X-Sarae-Error"], "THUMBNAIL_CONTENT_INVALID")
        self.assertEqual(self.put_cover(body=b"\xff\xd8\xff" + b"0" * 600_000).status_code, 413)
        self.assertEqual(self.put_cover(headers=self.B).status_code, 404)

    def test_presenters_are_linked_filtered_and_owner_scoped(self):
        from app.catalog import Presenter
        with self.Session() as db:
            rows = [Presenter(owner_id=self.owner_a["id"], name="Lan"), Presenter(owner_id=self.owner_a["id"], name="Mai"),
                    Presenter(owner_id=self.owner_b["id"], name="B-person")]
            db.add_all(rows)
            db.commit()
            lan, mai, other = (r.id for r in rows)
        linked = self.client.put(f"/api/media-library/{self.media_id}/presenters", json={"presenter_ids": [mai, lan, mai]})
        self.assertEqual(linked.json()["presenter_ids"], [lan, mai])
        self.assertEqual(self.client.get("/api/media-library", params={"presenter_id": lan}).json()["total"], 1)
        self.assertEqual(self.client.get("/api/media-library", params={"presenter_id": 999}).json()["total"], 0)
        foreign = self.client.put(f"/api/media-library/{self.media_id}/presenters", json={"presenter_ids": [other]})
        self.assertEqual(foreign.headers["X-Sarae-Error"], "PRESENTER_NOT_FOUND")
        self.assertEqual(self.client.put(f"/api/media-library/{self.media_id}/presenters", json={"presenter_ids": []}, headers=self.B).status_code, 404)
        cleared = self.client.put(f"/api/media-library/{self.media_id}/presenters", json={"presenter_ids": []})
        self.assertEqual(cleared.json()["presenter_ids"], [])


class AgentMediaTest(MediaLibraryTestCase):
    def setUp(self):
        super().setUp()
        self.connect_storage()
        self.data = video_bytes()
        self.media_id = self.upload(self.data)
        self.product = self.tiktok_products(self.owner_a, "Áo")[0]
        self.client.put(f"/api/products/{self.product}/videos", json={"media_ids": [self.media_id]})

    def session(self, owner, product_ids, agent_id=None):
        from app.live_sessions import LiveSession, SessionProduct
        with self.Session() as db:
            row = LiveSession(owner_id=owner["id"], name="LIVE", status="DRAFT", agent_id=agent_id)
            db.add(row)
            db.flush()
            db.add_all(SessionProduct(session_id=row.id, product_id=pid, sort_order=i) for i, pid in enumerate(product_ids))
            db.commit()
            return row.id

    def as_agent(self, agent):
        return {"X-Sarae-Agent-Key": agent["agent_credential"]}

    def test_agents_of_the_owner_share_the_library(self):
        pc1, pc2 = self.activate_agent(self.owner_a, "PC1"), self.activate_agent(self.owner_a, "PC2")
        empty = self.tiktok_products(self.owner_a, "Không video", seller="7001")[0]
        session_id = self.session(self.owner_a, [self.product, empty])
        for agent in (pc1, pc2):
            manifest = self.client.get("/api/agent-media/manifest", params={"agent_id": agent["agent_id"], "session_id": session_id},
                                       headers=self.as_agent(agent))
            self.assertEqual(manifest.status_code, 200, manifest.text)
            body = manifest.json()
            self.assertEqual([i["media_id"] for i in body["items"]], [self.media_id])
            self.assertEqual(body["items"][0]["sha256"], hashlib.sha256(self.data).hexdigest())
            self.assertEqual(body["products_without_video"], [empty])
            self.assertNotIn("googleapis", manifest.text)
            part = self.client.get(f"/api/agent-media/{self.media_id}/content", params={"agent_id": agent["agent_id"]},
                                   headers={**self.as_agent(agent), "Range": "bytes=0-1023"})
            self.assertEqual((part.status_code, part.content), (206, self.data[:1024]))

    def test_agent_of_another_owner_or_session_is_refused(self):
        a_agent, b_agent = self.activate_agent(self.owner_a, "A-PC"), self.activate_agent(self.owner_b, "B-PC")
        session_id = self.session(self.owner_a, [self.product])
        steal = self.client.get(f"/api/agent-media/{self.media_id}/content", params={"agent_id": b_agent["agent_id"]},
                                headers=self.as_agent(b_agent))
        self.assertEqual(steal.status_code, 404)
        foreign = self.client.get("/api/agent-media/manifest", params={"agent_id": b_agent["agent_id"], "session_id": session_id},
                                  headers=self.as_agent(b_agent))
        self.assertEqual(foreign.status_code, 404)
        spoof = self.client.get(f"/api/agent-media/{self.media_id}/content", params={"agent_id": a_agent["agent_id"]},
                                headers=self.as_agent(b_agent))  # A's agent id with B's key
        self.assertEqual(spoof.status_code, 403)
        other_pc = self.activate_agent(self.owner_a, "A-PC2")
        assigned = self.session(self.owner_a, [self.product], agent_id=other_pc["agent_id"])
        wrong_pc = self.client.get("/api/agent-media/manifest", params={"agent_id": a_agent["agent_id"], "session_id": assigned},
                                   headers=self.as_agent(a_agent))
        self.assertEqual(wrong_pc.status_code, 404)


class ProviderMigrationTest(MediaLibraryTestCase):
    def setUp(self):
        super().setUp()
        MemoryStorageProvider.store = MemoryStore()
        storage_providers.register("MEMORY", MemoryStorageProvider)
        self.connect_storage()
        self.data = video_bytes()
        self.media_id = self.upload(self.data)

    def test_migration_copies_verifies_and_switches_mapping(self):
        with self.Session() as db:
            drive_file = db.get(MediaItem, self.media_id).provider_file_id
        response = self.client.post("/api/storage-admin/migrations", json={"target_provider": "MEMORY"}, headers=self.ADMIN)
        self.assertEqual(response.json()["migrated"], 1, response.text)
        with self.Session() as db:
            media = db.get(MediaItem, self.media_id)
            self.assertEqual(media.provider, "MEMORY")
            objects = {o.provider: o.status for o in db.query(MediaStorageObject).filter_by(media_id=self.media_id)}
        self.assertEqual(objects, {"GOOGLE_DRIVE": "RETIRED", "MEMORY": "ACTIVE"})
        self.assertIn(drive_file, self.google.content)  # the source copy is kept for rollback
        stream = self.client.get(self.media(self.media_id).json()["stream_url"], headers={**self.ANON, "Range": "bytes=0-9"})
        self.assertEqual((stream.status_code, stream.content), (206, self.data[:10]))
        self.assertEqual(self.media(self.media_id).json()["id"], self.media_id)  # Sarae id never changes
        self.assertEqual(self.client.post("/api/storage-admin/migrations", json={"target_provider": "MEMORY"}, headers=self.ADMIN).json()["results"], [])

    def test_failed_checksum_keeps_the_old_mapping(self):
        MemoryStorageProvider.store.corrupt_writes = True
        response = self.client.post("/api/storage-admin/migrations", json={"target_provider": "MEMORY"}, headers=self.ADMIN)
        self.assertEqual(response.json()["results"][0]["error_code"], "STORAGE_CHECKSUM_MISMATCH")
        with self.Session() as db:
            self.assertEqual(db.get(MediaItem, self.media_id).provider, "GOOGLE_DRIVE")
        self.assertEqual(MemoryStorageProvider.store.objects, {})
        self.assertEqual(self.client.get(self.media(self.media_id).json()["stream_url"], headers=self.ANON).content, self.data)

    def test_customers_cannot_migrate(self):
        self.assertEqual(self.client.post("/api/storage-admin/migrations", json={"target_provider": "MEMORY"}).status_code, 403)

    def test_new_uploads_can_target_another_provider(self):
        with patch.object(settings, "sarae_storage_provider", "MEMORY"):
            media_id = self.upload(video_bytes(seed=b"m"))
        with self.Session() as db:
            self.assertEqual(db.get(MediaItem, media_id).provider, "MEMORY")
