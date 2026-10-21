"""Private images (product / presenter photos, Agent thumbnails) open only through URLs signed for their real owner."""
import base64
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from app import catalog, media_access
from support import SaasTestCase

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg==")
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


class PrivateImageTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        self.images = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        for target in ("PRODUCT_IMAGE_DIR", "PRESENTER_IMAGE_DIR"):
            patcher = patch.object(catalog, target, Path(self.images.name))
            patcher.start()
            self.addCleanup(patcher.stop)
        thumbs = patch("app.agent_library.THUMB_DIR", Path(self.images.name) / "thumbs")
        thumbs.start()
        self.addCleanup(thumbs.stop)

    def tearDown(self):
        super().tearDown()
        self.images.cleanup()

    def upload(self, headers=None, folder="products"):
        response = self.client.post(f"/api/{folder}/images", files={"file": ("a.png", PNG, "image/png")}, headers=headers or self.A)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_signed_url_opens_only_unaltered_and_unexpired(self):
        uploaded = self.upload()
        self.assertEqual(self.client.get(uploaded["url"], headers=self.ANON).status_code, 200, "no token needed: <img> works")
        path = urlsplit(uploaded["url"]).path
        self.assertEqual(self.client.get(path).status_code, 403, "unsigned")
        query = parse_qs(urlsplit(uploaded["url"]).query)
        tampered = f"{path}?exp={int(query['exp'][0]) + 3600}&sig={query['sig'][0]}"
        self.assertEqual(self.client.get(tampered).status_code, 403, "extended expiry breaks the signature")
        with patch.object(media_access.time, "time", return_value=time.time() + media_access.URL_TTL_SECONDS + 5):
            self.assertEqual(self.client.get(uploaded["url"]).status_code, 403, "expired")

    def test_another_owner_cannot_read_or_reuse_a_photo(self):
        uploaded = self.upload()
        name = uploaded["ref"].rsplit("/", 1)[1]
        forged = media_access.signed(uploaded["ref"], "product", name, self.owner_b["id"])
        self.assertEqual(self.client.get(forged).status_code, 403, "a URL signed for B does not open A's photo")
        attach = self.client.post("/api/presenters", json={"name": "Của B", "avatar_url": uploaded["url"]}, headers=self.B)
        self.assertEqual(attach.status_code, 422)

    def test_api_returns_signed_urls_and_stores_the_stable_reference(self):
        uploaded = self.upload(folder="presenters")
        created = self.client.post("/api/presenters", json={"name": "Lan", "avatar_url": uploaded["url"]}).json()
        self.assertIn("sig=", created["avatar_url"])
        with self.Session() as db:
            self.assertEqual(db.get(catalog.Presenter, created["id"]).avatar_url, uploaded["ref"])
        listed = self.client.get("/api/presenters").json()[0]["avatar_url"]
        self.assertEqual(self.client.get(listed).status_code, 200)
        external = self.client.post("/api/presenters", json={"name": "Ngoài", "avatar_url": "https://cdn.example/a.png"}).json()
        self.assertEqual(external["avatar_url"], "https://cdn.example/a.png", "external URLs are left as they are")
        # Product photos come from TikTok (CDN URLs synced by the Agent) and are returned as they are.
        (pid,) = self.tiktok_products(self.owner_a, "Áo")
        with self.Session() as db:
            db.get(catalog.Product, pid).image_url = "https://p16-oec.tiktokcdn.com/a.jpg"
            db.commit()
        self.assertEqual(self.client.get(f"/api/products/{pid}").json()["image_url"], "https://p16-oec.tiktokcdn.com/a.jpg")

    def test_replaced_or_deleted_photo_is_removed(self):
        first, second = self.upload(folder="presenters"), self.upload(folder="presenters")
        person = self.client.post("/api/presenters", json={"name": "Lan", "avatar_url": first["url"]}).json()
        self.client.put(f"/api/presenters/{person['id']}", json={"name": "Lan", "avatar_url": second["url"]})
        self.assertFalse((Path(self.images.name) / first["ref"].rsplit("/", 1)[1]).exists())
        self.assertEqual(self.client.get(first["url"]).status_code, 404)
        self.client.delete(f"/api/presenters/{person['id']}")
        self.assertEqual(self.client.get(second["url"]).status_code, 404)

    def test_presenter_photos_follow_the_same_rules(self):
        uploaded = self.upload(folder="presenters")
        person = self.client.post("/api/presenters", json={"name": "Lan", "avatar_url": uploaded["url"]}).json()
        self.assertEqual(self.client.get(person["avatar_url"]).status_code, 200)
        self.assertEqual(self.client.post("/api/presenters", json={"name": "B", "avatar_url": uploaded["url"]}, headers=self.B).status_code, 422)

    def test_agent_thumbnails_are_signed_per_owner_and_close_on_revoke(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        video = {"media_id": "b" * 64, "filename": "a.mp4", "size_bytes": 1, "modified_ns": 1,
                 "thumb_jpeg_base64": base64.b64encode(JPEG).decode()}
        self.client.post("/api/agent-library/sync", json={"agent_id": agent["agent_id"], "videos": [video]},
                         headers={"X-Sarae-Agent-Key": agent["agent_credential"]})
        url = self.client.get("/api/media", params={"agent_id": agent["agent_id"]}).json()[0]["thumb_url"]
        self.assertEqual(self.client.get(url, headers=self.ANON).status_code, 200)
        name = urlsplit(url).path.rsplit("/", 1)[1]
        forged = media_access.signed(f"/api/agent-library/thumbs/{name}", "thumb", name, self.owner_b["id"])
        self.assertEqual(self.client.get(forged).status_code, 403)
        self.client.delete(f"/api/identity/agents/{agent['agent_id']}")
        self.assertEqual(self.client.get(url).status_code, 404, "a revoked Agent's thumbnails are gone")

    def test_heartbeat_keeps_no_absolute_path_of_the_pc(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        self.client.post("/api/agents/heartbeat", headers={"X-Sarae-Agent-Key": agent["agent_credential"]}, json={
            "agent_id": agent["agent_id"], "hostname": "PC-A",
            "verification": {"state": {"window_info": {"exe_name": "TikTok LIVE Studio.exe",
                                                       "exe_path": "C:\\Users\\shop\\AppData\\TikTok LIVE Studio.exe"}}}})
        status = self.client.get("/api/verification-monitor/status", params={"agent_id": agent["agent_id"]}).json()
        self.assertEqual(status["state"]["window_info"]["exe_path"], "TikTok LIVE Studio.exe")


if __name__ == "__main__":
    unittest.main()
