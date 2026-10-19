"""CAPTCHA templates: SUPER_ADMIN manages them, Agents download them, OWNERs cannot see them."""
import base64
import unittest

from tests.support import SaasTestCase

PNG = b"\x89PNG\r\n\x1a\n" + bytes(64)
JPEG = b"\xff\xd8\xff\xe0" + bytes(64)


def b64(raw):
    return base64.b64encode(raw).decode()


class CaptchaTemplateTests(SaasTestCase):
    def upload(self, raw=PNG, name="TikTok xác minh", headers=None):
        return self.client.post("/api/captcha-templates", headers=headers or self.ADMIN,
                                json={"name": name, "image_base64": b64(raw)})

    def test_super_admin_uploads_lists_previews_and_deletes(self):
        created = self.upload()
        self.assertEqual(created.status_code, 200, created.text)
        item = created.json()
        self.assertRegex(item["id"], r"^[0-9a-f]{32}$")
        self.assertEqual(self.upload(JPEG, "Mẫu 2").status_code, 200)
        listed = self.client.get("/api/captcha-templates", headers=self.ADMIN).json()["templates"]
        self.assertEqual([t["name"] for t in listed], ["TikTok xác minh", "Mẫu 2"])
        image = self.client.get(f"/api/captcha-templates/{item['id']}/image", headers=self.ADMIN)
        self.assertEqual((image.status_code, image.content), (200, PNG))
        self.assertEqual(self.client.delete(f"/api/captcha-templates/{item['id']}", headers=self.ADMIN).status_code, 200)
        self.assertEqual(len(self.client.get("/api/captcha-templates", headers=self.ADMIN).json()["templates"]), 1)
        self.assertEqual(self.client.delete(f"/api/captcha-templates/{item['id']}", headers=self.ADMIN).status_code, 404)

    def test_rejects_non_images_and_duplicates(self):
        self.assertEqual(self.upload(b"GIF89a" + bytes(64)).status_code, 422)
        self.assertEqual(self.client.post("/api/captcha-templates", headers=self.ADMIN,
                                          json={"name": "x", "image_base64": "%%%%%%%%%%%%%%%%%%%%%%"}).status_code, 422)
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(self.upload(name="same bytes").status_code, 409)

    def test_owner_and_anonymous_have_no_access(self):
        item = self.upload().json()
        for headers in (self.A, self.ANON):
            self.assertIn(self.client.get("/api/captcha-templates", headers=headers).status_code, (401, 403))
            self.assertIn(self.upload(JPEG, headers=headers).status_code, (401, 403))
            self.assertIn(self.client.delete(f"/api/captcha-templates/{item['id']}", headers=headers).status_code, (401, 403))
            self.assertIn(self.client.get(f"/api/captcha-templates/{item['id']}/image", headers=headers).status_code, (401, 403))

    def test_agent_downloads_full_set_with_its_credential(self):
        agent = self.activate_agent(self.owner_a)
        item = self.upload().json()
        key = {"X-Sarae-Agent-Key": agent["agent_credential"]}
        listed = self.client.get("/api/captcha-templates/agent", params={"agent_id": agent["agent_id"]}, headers=key)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["templates"], [{"id": item["id"], "sha256": item["sha256"], "bytes": len(PNG)}])
        image = self.client.get(f"/api/captcha-templates/agent/{item['id']}/image",
                                params={"agent_id": agent["agent_id"]}, headers=key)
        self.assertEqual((image.status_code, image.content), (200, PNG))
        bad = {"X-Sarae-Agent-Key": "wrong"}
        self.assertEqual(self.client.get("/api/captcha-templates/agent", params={"agent_id": agent["agent_id"]},
                                         headers=bad).status_code, 403)
        self.assertEqual(self.client.get(f"/api/captcha-templates/agent/{'0' * 32}/image",
                                         params={"agent_id": agent["agent_id"]}, headers=key).status_code, 404)


if __name__ == "__main__":
    unittest.main()
