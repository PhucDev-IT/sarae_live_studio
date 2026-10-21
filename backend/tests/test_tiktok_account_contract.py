"""TikTok broadcaster identity is independent from a product's source seller."""
import unittest

from fastapi import HTTPException
from pydantic import ValidationError

from app.cloud_agents import validate_payload
from app.tiktok_products import AccountProfile, Snapshot


class TikTokAccountContractTests(unittest.TestCase):
    def test_seller_and_creator_have_distinct_verified_broadcaster_ids(self):
        for uid, account_type in (
            ("7494647113328657914", 1),
            ("7495962653689350865", 3),
        ):
            with self.subTest(uid=uid):
                profile = AccountProfile(user_id=uid, user_type=account_type)
                self.assertEqual(profile.user_id, uid)
                self.assertEqual(profile.user_type, account_type)

    def test_products_cannot_replace_the_broadcaster_account(self):
        snapshot = Snapshot.model_validate({
            "agent_id": "agt_test",
            "account": {"user_id": "7495962653689350865", "user_type": 3},
            "complete": True,
            "products": [
                {"seller_id": "100", "product_id": "10", "name": "Áo"},
                {"seller_id": "200", "product_id": "20", "name": "Quần"},
            ],
        })
        self.assertEqual(snapshot.account.user_id, "7495962653689350865")
        self.assertNotEqual(snapshot.account.user_id, snapshot.products[0].seller_id)
        self.assertNotEqual(snapshot.account.user_id, snapshot.products[1].seller_id)

    def test_profile_must_be_supplied(self):
        with self.assertRaises(ValidationError):
            Snapshot.model_validate({"agent_id": "agt_test", "complete": True,
                                     "products": [{"seller_id": "1", "product_id": "2", "name": "Áo"}]})

    def test_remote_console_sync_requires_expected_account(self):
        self.assertEqual(
            validate_payload("tiktok_products_sync", {"expected_user_id": "7495962653689350865"}),
            {"expected_user_id": "7495962653689350865"},
        )
        for wrong in ({}, {"expected_user_id": "not-an-id"}, {"expected_user_id": 123}):
            with self.subTest(payload=wrong), self.assertRaises(HTTPException):
                validate_payload("tiktok_products_sync", wrong)


if __name__ == "__main__":
    unittest.main()
