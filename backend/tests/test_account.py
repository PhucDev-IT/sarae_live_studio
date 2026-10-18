"""An OWNER sees their own license and sign-ins, and can change their password; nothing about other accounts."""
import unittest

from app import identity
from support import PASSWORD, SaasTestCase


class AccountTests(SaasTestCase):
    def test_owner_sees_own_license_and_device_slots(self):
        self.activate_agent(self.owner_a, "PC-A")
        account = self.client.get("/api/identity/account").json()
        self.assertEqual((account["email"], account["agents_used"]), ("a@example.test", 1))
        self.assertEqual((account["license"]["max_agents"], account["license"]["valid"]), (3, True))
        self.assertGreaterEqual(account["sessions"], 1)
        self.assertNotIn("password_hash", str(account))
        self.assertEqual(self.client.get("/api/identity/account", headers=self.B).json()["agents_used"], 0)
        self.assertEqual(self.client.get("/api/identity/account", headers=self.ADMIN).status_code, 403)
        self.assertEqual(self.client.get("/api/identity/account", headers=self.ANON).status_code, 401)

    def test_password_change_signs_out_other_sessions_only(self):
        other = {"Authorization": "Bearer " + identity.login(identity.Login(email="a@example.test", password=PASSWORD))["access_token"]}
        wrong = self.client.post("/api/identity/password", json={"current_password": "nope", "new_password": "NewPassword123!"})
        self.assertEqual(wrong.status_code, 400)
        ok = self.client.post("/api/identity/password", json={"current_password": PASSWORD, "new_password": "NewPassword123!"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.client.get("/api/identity/me").status_code, 200, "this tab stays signed in")
        self.assertEqual(self.client.get("/api/identity/me", headers=other).status_code, 401, "other sign-ins are revoked")
        with self.assertRaises(Exception):
            identity.login(identity.Login(email="a@example.test", password=PASSWORD))
        self.assertTrue(identity.login(identity.Login(email="a@example.test", password="NewPassword123!"))["access_token"])

    def test_revoke_other_sessions(self):
        other = {"Authorization": "Bearer " + identity.login(identity.Login(email="a@example.test", password=PASSWORD))["access_token"]}
        self.assertGreaterEqual(self.client.post("/api/identity/sessions/revoke-others").json()["revoked"], 1)
        self.assertEqual(self.client.get("/api/identity/me", headers=other).status_code, 401)
        self.assertEqual(self.client.get("/api/identity/me", headers=self.B).status_code, 200, "B is untouched")


if __name__ == "__main__":
    unittest.main()
