"""TikTok pin command contract without a running Cloud or LIVE Console."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.cloud_agents import validate_payload
from app.live import _pin_account, _pin_mapping


class PinCommandContractTests(unittest.TestCase):
    def test_pin_mapping_keeps_sarae_and_tiktok_ids_distinct(self):
        product = SimpleNamespace(id=7, tiktok_product_id="1732299744789890081", name="Son đỏ",
                                  image_url="https://img.example/son.jpg", price=199000.0)
        mapping = _pin_mapping(product, "7494647113328657914")
        self.assertEqual(mapping["tiktok_product_id"], "1732299744789890081")
        self.assertEqual(mapping["price_text"], "199000")
        self.assertEqual(mapping["expected_user_id"], "7494647113328657914")

    def test_pin_without_tiktok_id_uses_verified_descriptor(self):
        body = validate_payload("PIN_PRODUCT", {"session_id": 42, "target": {"name": "Son đỏ", "image_url": "https://img.example/son.jpg",
                                                              "price_text": "199000", "expected_user_id": "7494647113328657914"}})
        self.assertIsNone(body["target"]["tiktok_product_id"])
        self.assertEqual(body["target"]["name"], "Son đỏ")
        self.assertEqual(body["session_id"], 42)

    def test_confirmation_token_and_auto_toggle_are_validated(self):
        with self.assertRaises(HTTPException):
            validate_payload("PIN_PRODUCT", {"session_id": 42, "target": {"name": "Son",
                "expected_user_id": "7494647113328657914"}, "confirmed_token": "$(bad)"})
        with self.assertRaises(HTTPException):
            validate_payload("UNPIN_PRODUCT", {})
        with self.assertRaises(HTTPException):
            validate_payload("SET_AUTO_PIN", {"enabled": True, "products": []})
        self.assertEqual(validate_payload("SET_AUTO_PIN", {"enabled": False, "products": []})["enabled"], False)

    def test_pin_target_rejects_missing_account_and_bad_existing_id(self):
        target = {"name": "Son đỏ", "image_url": "", "price_text": "199000"}
        with self.assertRaises(HTTPException) as missing_account:
            validate_payload("PIN_PRODUCT", {"session_id": 42, "target": target})
        self.assertEqual(missing_account.exception.status_code, 422)
        with self.assertRaises(HTTPException):
            validate_payload("PIN_PRODUCT", {"session_id": 42, "target": {**target, "expected_user_id": "123",
                                                        "tiktok_product_id": "Sarae-7"}})
        with self.assertRaises(HTTPException):
            validate_payload("PIN_PRODUCT", {"target": {**target, "expected_user_id": "123"}})
        with self.assertRaises(HTTPException):
            validate_payload("UNPIN_PRODUCT", {"expected_user_id": "123"})
        with self.assertRaises(HTTPException):
            validate_payload("SET_AUTO_PIN", {"enabled": True, "session_id": 5,
                "products": [{"sarae_product_id": 7, **target, "expected_user_id": ""}]})

    def test_auto_pin_accepts_mapping_without_tiktok_id(self):
        body = validate_payload("SET_AUTO_PIN", {"enabled": True, "session_id": 5,
            "products": [{"sarae_product_id": 7, "name": "Son đỏ", "image_url": "",
                          "price_text": "199000", "expected_user_id": "7494647113328657914"}]})
        self.assertIsNone(body["products"][0]["tiktok_product_id"])

    @patch("app.live_sessions.playing_session", return_value=42)
    def test_pin_account_uses_playing_session_and_owned_account(self, playing):
        db = Mock()
        db.get.return_value = SimpleNamespace(id=42, owner_id="owner-a", agent_id="agent-a",
                                              status="RUNNING", tiktok_account_id=9)
        db.execute.return_value.first.return_value = ("7494647113328657914",)
        self.assertEqual(_pin_account(db, {"id": "owner-a"}, "agent-a"), ("7494647113328657914", 42))
        playing.assert_called_once_with("owner-a", "agent-a")
        self.assertEqual(db.execute.call_args.args[1], {"id": 9, "owner": "owner-a"})


if __name__ == "__main__":
    unittest.main()
