"""Mock S3 API tests, with real Sarae routes/authorization/database and no network."""
import hashlib
import io
import json
from unittest.mock import patch

from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.response import StreamingBody
from boto3 import client as sdk_client

from app import storage_service
from app.config import settings
from app.r2_storage import CloudflareR2StorageProvider, PART_SIZE
from app.storage_errors import StorageError
from app.storage_models import MediaItem, MediaUpload, StorageCredential
from test_media_library import MediaLibraryTestCase, video_bytes
import test_media_library as library_tests

CONFIG = dict(account_id="a" * 32, bucket="sarae-private", access_key_id="test-access",
              secret_access_key="test-secret", endpoint="", region="auto")


class FakeS3:
    def __init__(self):
        self.uploads, self.objects = {}, {}

    def head_bucket(self, **kw):
        return {}

    def create_multipart_upload(self, Key, **kw):
        uid = str(len(self.uploads) + 1)
        self.uploads[uid] = {"key": Key, "parts": {}}
        return {"UploadId": uid}

    def list_parts(self, UploadId, **kw):
        if UploadId not in self.uploads:
            raise ClientError({"Error": {"Code": "NoSuchUpload"}}, "ListParts")
        return {"Parts": [{"PartNumber": n, "Size": len(b), "ETag": f'"part-{n}"'}
                          for n, b in sorted(self.uploads[UploadId]["parts"].items())]}

    def upload_part(self, UploadId, PartNumber, Body, **kw):
        self.uploads[UploadId]["parts"][PartNumber] = Body
        return {"ETag": f'"part-{PartNumber}"'}

    def complete_multipart_upload(self, UploadId, Key, **kw):
        self.objects[Key] = b"".join(b for n, b in sorted(self.uploads.pop(UploadId)["parts"].items()))
        return {"ETag": '"multipart-etag-2"'}

    def abort_multipart_upload(self, UploadId, **kw):
        self.uploads.pop(UploadId, None)
        return {}

    def head_object(self, Key, **kw):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key]), "ETag": '"multipart-etag-2"'}

    def get_object(self, Key, Range=None, **kw):
        data = self.objects[Key]
        extra = {}
        if Range:
            start, end = Range[6:].split("-")
            if start:
                start, end = int(start), min(int(end) if end else len(data)-1, len(data)-1)
            else:
                start, end = max(0, len(data)-int(end)), len(data)-1
            if start >= len(data):
                raise ClientError({"Error": {"Code": "InvalidRange"}}, "GetObject")
            extra["ContentRange"] = f"bytes {start}-{end}/{len(data)}"
            data = data[start:end+1]
        return dict(Body=StreamingBody(io.BytesIO(data), len(data)), ContentLength=len(data), **extra)

    def delete_object(self, Key, **kw):
        self.objects.pop(Key, None)
        return {}


class R2Test(MediaLibraryTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeS3()
        self.s3_patch = patch("app.r2_storage.boto3.client", return_value=self.fake)
        self.s3_patch.start()
        self.addCleanup(self.s3_patch.stop)
        self.provider_patch = patch.object(settings, "sarae_storage_provider", "R2")
        self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)
        self.assertEqual(self.save().status_code, 200)
        self.assertEqual(self.client.post("/api/storage-admin/r2/check", headers=self.ADMIN).status_code, 200)

    def save(self, config=None, headers=None):
        return self.client.put("/api/storage-admin/r2/config", json=config or CONFIG, headers=headers or self.ADMIN)

    def upload(self, data=None, headers=None, filename="clip.mp4"):
        data = data or video_bytes()
        created = self.start_upload(data, headers, filename)
        self.assertEqual(created.status_code, 201, created.text)
        for start in range(0, len(data), PART_SIZE):
            response = self.send(created.json()["id"], data, start, min(start+PART_SIZE, len(data))-1, headers)
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "COMPLETED")
        return response.json()["media_id"]

    def test_admin_encryption_and_no_secret_response(self):
        self.assertEqual(self.save(headers=self.A).status_code, 403)
        for path, method in (("config", "put"), ("check", "post"), ("connection", "delete")):
            kwargs = {"json": CONFIG} if method == "put" else {}
            self.assertEqual(getattr(self.client, method)(f"/api/storage-admin/r2/{path}", headers=self.B, **kwargs).status_code, 403)
        status = self.client.get("/api/storage-admin/status", headers=self.ADMIN)
        self.assertNotIn("test-secret", status.text)
        self.assertNotIn("test-access", status.text)
        with self.Session() as db:
            self.assertNotIn("test-secret", db.get(StorageCredential, "R2").refresh_token_enc)

    def test_upload_resume_after_restart_and_lost_completion(self):
        data = video_bytes(PART_SIZE+250)
        uid = self.start_upload(data).json()["id"]
        self.assertEqual(self.send(uid, data, 0, PART_SIZE-1).status_code, 200)
        storage_service._hashers.clear()
        self.assertEqual(self.client.get(f"/api/media-library/uploads/{uid}").json()["received_bytes"], PART_SIZE)
        response = self.send(uid, data, PART_SIZE, len(data)-1)
        self.assertEqual(response.status_code, 200, response.text)
        mid = response.json()["media_id"]
        with self.Session() as db:
            media = db.get(MediaItem, mid)
            self.assertEqual(media.sha256, hashlib.sha256(data).hexdigest())
            self.assertIsNone(media.md5)
            self.assertIn(f"/media/{mid}/", media.provider_file_id)
        self.assertEqual(len(self.fake.objects), 1)

    def test_recover_lost_final_response(self):
        data = video_bytes()
        uid = self.start_upload(data).json()["id"]
        with self.Session() as db:
            from app import credentials
            session = credentials.decrypt(db.get(MediaUpload, uid).session_enc)
            store = CloudflareR2StorageProvider(db)
            store.upload_chunk(session, 0, data, len(data), len(data))
        response = self.client.get(f"/api/media-library/uploads/{uid}")
        self.assertEqual(response.json()["status"], "COMPLETED", response.text)

    def test_range_owner_product_thumbnail_and_delete(self):
        self.data = video_bytes()
        self.media_id = self.upload(self.data)
        library_tests.LibraryTest.test_stream_supports_range_and_signed_urls_only(self)
        self.media_id = self.upload()
        self.assertEqual(self.media(self.media_id, self.B).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/media-library/{self.media_id}", headers=self.B).status_code, 404)
        library_tests.LibraryTest.test_product_video_many_to_many_with_order(self)
        self.assertEqual(self.client.delete(f"/api/media-library/{self.media_id}").status_code, 200)

    def test_cancel_and_reconfiguration_guard(self):
        uid = self.start_upload(video_bytes()).json()["id"]
        self.assertEqual(self.save().status_code, 409)
        self.assertEqual(self.client.delete(f"/api/media-library/uploads/{uid}").status_code, 200)
        self.assertFalse(self.fake.uploads)
        self.assertEqual(self.save().status_code, 200)

    def test_small_nonfinal_part_rejected(self):
        data = video_bytes(PART_SIZE+1)
        uid = self.start_upload(data).json()["id"]
        self.assertEqual(self.send(uid, data, 0, 262143).status_code, 400)

    def test_errors_never_sign_out_sarae(self):
        for code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "NoSuchBucket", "AccessDenied"):
            with patch.object(self.fake, "head_bucket", side_effect=ClientError({"Error": {"Code": code}}, "HeadBucket")):
                response = self.client.post("/api/storage-admin/r2/check", headers=self.ADMIN)
                self.assertEqual(response.status_code, 502)
                self.assertNotIn("test-secret", response.text)
        with patch.object(self.fake, "head_bucket", side_effect=ReadTimeoutError(endpoint_url="private")):
            self.assertEqual(self.client.post("/api/storage-admin/r2/check", headers=self.ADMIN).status_code, 502)

    def test_credential_change_detected(self):
        with self.Session() as db:
            store = CloudflareR2StorageProvider(db)
            session = store.begin_upload("owner", "media", "a.mp4", "video/mp4", 3)
            state = json.loads(session)
            state["revision"] = "changed"
            with self.assertRaises(StorageError) as caught:
                store.upload_status(json.dumps(state), 3)
            self.assertEqual(caught.exception.code, "STORAGE_CONFIG_CHANGED")

    def test_disconnect_reconnect_and_endpoint_validation(self):
        self.assertEqual(self.client.delete("/api/storage-admin/r2/connection", headers=self.ADMIN).status_code, 200)
        self.assertEqual(self.start_upload(video_bytes()).status_code, 503)
        self.assertEqual(self.client.post("/api/storage-admin/r2/check", headers=self.ADMIN).status_code, 200)
        self.assertEqual(self.save({**CONFIG, "endpoint": "http://localhost:8080"}).status_code, 503)

    def test_env_priority_and_disconnect(self):
        with self.Session() as db:
            db.delete(db.get(StorageCredential, "R2"))
            db.commit()
        values = {"sarae_r2_"+key: value for key, value in CONFIG.items()}
        with patch.multiple(settings, **values):
            self.assertEqual(self.client.post("/api/storage-admin/r2/check", headers=self.ADMIN).status_code, 200)
            view = self.client.get("/api/storage-admin/status", headers=self.ADMIN).json()["r2"]
            self.assertEqual((view["source"], view["status"]), ("ENV", "CONNECTED"))
            self.client.delete("/api/storage-admin/r2/connection", headers=self.ADMIN)
            self.assertEqual(self.start_upload(video_bytes()).status_code, 503)
            self.save({**CONFIG, "bucket": "db-bucket"})
            view = self.client.get("/api/storage-admin/status", headers=self.ADMIN).json()["r2"]
            self.assertEqual((view["source"], view["bucket"]), ("DATABASE", "db-bucket"))

    def test_agent_download_and_owner_isolation(self):
        self.data = video_bytes()
        self.media_id = self.upload(self.data)
        self.product = self.tiktok_products(self.owner_a, "Video product")[0]
        self.client.put(f"/api/products/{self.product}/videos", json={"media_ids": [self.media_id]})
        library_tests.AgentMediaTest.test_agents_of_the_owner_share_the_library(self)

    def test_agent_cannot_download_foreign_owner(self):
        self.data = video_bytes()
        self.media_id = self.upload(self.data)
        self.product = self.tiktok_products(self.owner_a, "Video product")[0]
        self.client.put(f"/api/products/{self.product}/videos", json={"media_ids": [self.media_id]})
        library_tests.AgentMediaTest.test_agent_of_another_owner_or_session_is_refused(self)

    session = library_tests.AgentMediaTest.session
    as_agent = library_tests.AgentMediaTest.as_agent

    def test_missing_object_range_and_checksum_failure(self):
        mid = self.upload()
        url = self.media(mid).json()["stream_url"]
        self.assertEqual(self.client.get(url, headers={"Range": "bytes=999999999-"}).status_code, 416)
        with self.Session() as db:
            key = db.get(MediaItem, mid).provider_file_id
            self.assertIsNone(CloudflareR2StorageProvider(db).describe("missing"))
        data = video_bytes()
        uid = self.start_upload(data).json()["id"]
        with patch.object(self.fake, "upload_part", side_effect=ClientError({"Error": {"Code": "BadDigest"}}, "UploadPart")):
            response = self.send(uid, data, 0, len(data)-1)
            self.assertEqual(response.headers["X-Sarae-Error"], "STORAGE_CHECKSUM_MISMATCH")
        self.assertIn(key, self.fake.objects)

    def test_corrupted_completed_object_is_deleted(self):
        original = self.fake.complete_multipart_upload
        def corrupt(**kwargs):
            result = original(**kwargs)
            key = kwargs["Key"]
            self.fake.objects[key] = b"X" + self.fake.objects[key][1:]
            return result
        data = video_bytes()
        uid = self.start_upload(data).json()["id"]
        with patch.object(self.fake, "complete_multipart_upload", side_effect=corrupt):
            response = self.send(uid, data, 0, len(data)-1)
        self.assertEqual(response.status_code, 422)
        self.assertFalse(self.fake.objects)
        self.assertEqual(self.client.get(f"/api/media-library/uploads/{uid}").json()["status"], "FAILED")

    def test_parts_pagination_and_download_interruption(self):
        with self.Session() as db:
            store = CloudflareR2StorageProvider(db)
            state = {"key": "key", "upload_id": "uid"}
            with patch.object(self.fake, "list_parts", side_effect=[
                {"Parts": [{"PartNumber": 1, "Size": PART_SIZE}], "IsTruncated": True, "NextPartNumberMarker": 1},
                {"Parts": [{"PartNumber": 2, "Size": 10}]}]):
                self.assertEqual(len(store._parts(state)), 2)
            body = StreamingBody(io.BytesIO(b"short"), 10)
            with patch.object(self.fake, "get_object", return_value={"Body": body, "ContentLength": 10}):
                with self.assertRaises(StorageError) as caught:
                    list(store.open_read("key").iter())
                self.assertEqual(caught.exception.code, "STORAGE_UNREACHABLE")

    def test_botocore_validates_request_contract(self):
        # Use a real botocore client + Stubber so the fake cannot hide invalid SDK parameter shapes.
        from botocore.stub import Stubber
        client = sdk_client("s3", endpoint_url="https://"+"a"*32+".r2.cloudflarestorage.com",
                              region_name="auto", aws_access_key_id="test", aws_secret_access_key="test")
        with Stubber(client) as stub, self.Session() as db:
            store = CloudflareR2StorageProvider(db)
            store.client = client
            stub.add_response("head_bucket", {}, {"Bucket": CONFIG["bucket"]})
            store.health()
            stub.add_response("create_multipart_upload", {"UploadId": "test-upload"},
                              {"Bucket": CONFIG["bucket"], "Key": "owners/owner/media/media/video", "ContentType": "video/mp4",
                               "Metadata": {"sarae-media-id": "media"}})
            store.begin_upload("owner", "media", "x.mp4", "video/mp4", 100)
            stub.assert_no_pending_responses()
