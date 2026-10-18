"""Identity/ownership tests. Run: python -m unittest discover -s tests -p test_*.py"""
import tempfile
import unittest
from pathlib import Path
from fastapi import HTTPException

from app import identity

class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.original_db=identity.DB
        identity.DB=Path(self.tmp.name)/"identity-test.sqlite3"
        identity.bootstrap_admin("admin@example.test","AdminSecretPassword123!")
        self.admin_token=identity.login(identity.Login(
            email="admin@example.test",password="AdminSecretPassword123!"))["access_token"]
        self.admin_header="Bearer "+self.admin_token

    def tearDown(self):
        identity.DB=self.original_db
        self.tmp.cleanup()

    def create_owner(self,email):
        return identity.create_owner(identity.CreateOwner(
            email=email,password="OwnerSecretPassword123!",max_agents=1),
            authorization=self.admin_header)

    def test_single_superadmin(self):
        with self.assertRaises(RuntimeError):
            identity.bootstrap_admin("another@example.test","AdminSecretPassword123!")

    def test_activation_one_time_and_owner_scope(self):
        a=self.create_owner("a@example.test")
        b=self.create_owner("b@example.test")
        key=identity.issue_key(a["id"],authorization=self.admin_header)["activation_key"]
        device=identity.activate(identity.Activate(
            activation_key=key,hostname="PC-A"))
        verified=identity.verify_agent(device["agent_id"],device["agent_credential"])
        self.assertEqual(verified["owner_id"],a["id"])
        with self.assertRaises(HTTPException):
            identity.activate(identity.Activate(activation_key=key,hostname="PC-ANOTHER"))
        owner_b=identity.login(identity.Login(
            email="b@example.test",password="OwnerSecretPassword123!"))["access_token"]
        with self.assertRaises(HTTPException):
            identity.allow_owner("Bearer "+owner_b,a["id"])
        with self.assertRaises(HTTPException):
            identity.verify_agent(device["agent_id"],"wrong-key")
        identity.revoke_agent(device["agent_id"],authorization=self.admin_header)
        with self.assertRaises(HTTPException):
            identity.verify_agent(device["agent_id"],device["agent_credential"])

    def test_logout_revokes_session(self):
        self.assertEqual(identity.me(authorization=self.admin_header)["role"], "SUPER_ADMIN")
        self.assertEqual(identity.logout(authorization=self.admin_header), {"ok": True})
        with self.assertRaises(HTTPException) as context:
            identity.me(authorization=self.admin_header)
        self.assertEqual(context.exception.status_code, 401)

    def test_platform_summary_admin_only(self):
        summary=identity.platform_summary(authorization=self.admin_header)
        self.assertEqual(summary["owners_total"],0)
        self.assertEqual(summary["agents_total"],0)
        owner=self.create_owner("owner@example.test")
        summary=identity.platform_summary(authorization=self.admin_header)
        self.assertEqual(summary["owners_total"],1)
        token=identity.login(identity.Login(email="owner@example.test",password="OwnerSecretPassword123!"))["access_token"]
        with self.assertRaises(HTTPException) as exc:
            identity.platform_summary(authorization="Bearer "+token)
        self.assertEqual(exc.exception.status_code,403)

    def test_license_agent_limit(self):
        owner=self.create_owner("a@example.test")
        first=identity.issue_key(owner["id"],authorization=self.admin_header)["activation_key"]
        second=identity.issue_key(owner["id"],authorization=self.admin_header)["activation_key"]
        identity.activate(identity.Activate(activation_key=first,hostname="PC-A"))
        with self.assertRaises(HTTPException) as context:
            identity.activate(identity.Activate(activation_key=second,hostname="PC-B"))
        self.assertEqual(context.exception.status_code,409)

if __name__=="__main__":
    unittest.main()
