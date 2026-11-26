"""Studio AI: facts, provider settings, protected keys, replies and scripts (fake providers, no network, no DPAPI)."""
from __future__ import annotations

import json

import pytest

from sarae_agent.studio.ai import StudioAI, redact
from sarae_agent.studio.service import StudioError, StudioService
from studio_helpers import create_product

KEY = "AIzaSyTESTKEY1234567890abcd"


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.delenv("SARAE_STUDIO_DATA_DIR", raising=False)


@pytest.fixture
def studio(tmp_path):
    instance = StudioService(data_dir=tmp_path / "studio")
    try:
        yield instance
    finally:
        instance.close()


class FakeProvider:
    def __init__(self, answer: dict | str | Exception):
        self.answer = answer
        self.calls = []

    def __call__(self, url, *, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        if isinstance(self.answer, Exception):
            raise self.answer
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer, ensure_ascii=False)
        if "generativelanguage" in url:
            return {"candidates": [{"content": {"parts": [{"text": text}]}}],
                    "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30}}
        return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


def ai_for(studio, answer, *, enabled=True, provider="gemini", key=True, **config):
    fake = FakeProvider(answer)
    ai = StudioAI(studio, post=fake, protect=lambda data: data[::-1], unprotect=lambda data: data[::-1])
    ai.configure({"provider": provider, "enabled": enabled, **config})
    if key and provider != "seller_brain":
        ai.set_key({"provider": provider, "api_key": KEY})
    return ai, fake


def error_code(operation) -> str:
    with pytest.raises(StudioError) as caught:
        operation()
    return caught.value.code


FACTS = {"sizes": [{"size": "m", "weight_min": 45, "weight_max": 55, "height_min": 150, "height_max": 165}],
         "colors": ["Trắng", "trắng", "Đen"], "material": " Linen   100% ", "shipping": "2-4 ngày",
         "notes": "", "extras": {"form": "rộng"}}


def test_product_facts_are_validated_normalized_and_kept_across_sync(studio):
    product = create_product(studio, "Áo linen", product_id=501)
    updated = studio.dispatch("studio.products.update", {"id": product["id"], "facts": FACTS})["product"]
    assert updated["facts"]["sizes"][0]["size"] == "M"
    assert updated["facts"]["colors"] == ["Trắng", "Đen"] and updated["facts"]["material"] == "Linen 100%"
    create_product(studio, "Áo linen mới", product_id=501)
    assert studio.dispatch("studio.products.list", {})["products"][0]["facts"] == updated["facts"]


@pytest.mark.parametrize("facts", [
    {"sizes": [{"size": "M", "weight_min": 60, "weight_max": 50}]}, {"sizes": [{"size": ""}]},
    {"sizes": [{"size": "M", "weight_min": 500}]}, {"colors": "đỏ"}, {"extras": {"Bad Key": "x"}},
    {"price": 1}, {"material": "x" * 201}, [],
])
def test_invalid_facts_are_rejected(studio, facts):
    product = create_product(studio)
    assert error_code(lambda: studio.dispatch("studio.products.update", {"id": product["id"], "facts": facts})) \
        == "VALIDATION_ERROR"
    assert studio.dispatch("studio.products.list", {})["products"][0]["facts"]["sizes"] == []


def test_config_defaults_switching_and_validation(studio):
    ai = StudioAI(studio, post=FakeProvider({}), protect=bytes, unprotect=bytes)
    assert ai.config()["config"]["provider"] == "gemini" and ai.config()["config"]["enabled"] is False
    config = ai.configure({"provider": "seller_brain"})["config"]
    assert config["model"] == "qwen3.5-9b-q4km" and config["base_url"] == "http://127.0.0.1:8830/v1"
    for bad in ({"base_url": "http://192.168.1.5:8830/v1"}, {"provider": "openai_compatible", "base_url": "http://api.x/v1"},
                {"provider": "gemini", "model": "../../files"}, {"provider": "nope"}, {"max_output_tokens": 10},
                {"temperature": 3}, {"enabled": "yes"}, {"api_key": KEY}):
        assert error_code(lambda: ai.configure(bad)) == "VALIDATION_ERROR"
    assert ai.config()["config"]["provider"] == "seller_brain"
    reopened = StudioAI(studio, post=FakeProvider({}), protect=bytes, unprotect=bytes)
    assert reopened.config()["config"] == config


def test_keys_are_protected_write_only_and_deletable(studio):
    ai, _ = ai_for(studio, {})
    path = studio.data_dir / "secrets" / "ai-gemini.dpapi.json"
    stored = path.read_text(encoding="utf-8")
    assert KEY not in stored and json.loads(stored)["last4"] == KEY[-4:]
    view = json.dumps(ai.config())
    assert KEY not in view and ai.config()["keys"]["gemini"] == {"saved": True, "last4": "abcd"}
    assert error_code(lambda: ai.set_key({"provider": "seller_brain", "api_key": KEY})) == "VALIDATION_ERROR"
    assert error_code(lambda: ai.set_key({"provider": "gemini", "api_key": "has space key"})) == "VALIDATION_ERROR"
    ai.delete_key({"provider": "gemini"})
    assert not path.exists()
    assert error_code(lambda: ai.reply({"comment": "giá sao shop"})) == "AI_KEY_MISSING"


def test_reply_uses_product_context_and_sanitizes_output(studio):
    product = create_product(studio, "Áo linen", product_id=601, price_text="389.000₫", origin_price_text="459.000₫",
                             stock_num=0)
    studio.dispatch("studio.products.update", {"id": product["id"], "description": "Áo linen form rộng",
                                               "facts": FACTS})
    ai, fake = ai_for(studio, {"intent": "ASK_SIZE", "reply": "  Bạn 50kg   mặc size M nha ", "needs_review": False,
                               "reason": "bảng size"})
    result = ai.reply({"comment": "50kg 1m6 mặc size gì", "product_id": product["id"], "username": "lan"})
    assert result["intent"] == "ASK_SIZE" and result["reply"] == "Bạn 50kg mặc size M nha"
    assert result["action_executed"] is False and result["usage"]["input_tokens"] == 120
    call = fake.calls[0]
    assert call["headers"] == {"x-goog-api-key": KEY} and "gemini-3.5-flash-lite" in call["url"]
    data = json.loads(call["body"]["contents"][0]["parts"][0]["text"].removeprefix("DATA:\n"))
    assert data["PRODUCT"]["gia_ban"] == "389.000đ" and data["PRODUCT"]["gia_goc"] == "459.000đ"
    assert data["PRODUCT"]["ton_kho"] == "hết hàng" and data["PRODUCT"]["thong_tin"]["sizes"][0]["size"] == "M"
    assert "image_url" not in json.dumps(data) and data["COMMENT"] == "50kg 1m6 mặc size gì"


def test_unknown_intent_and_empty_reply_need_review(studio):
    ai, _ = ai_for(studio, '```json\n{"intent": "HACK", "reply": "", "needs_review": false, "reason": "x"}\n```')
    result = ai.reply({"comment": "xin chào"})
    assert result["intent"] == "OTHER" and result["needs_review"] is True and result["product_id"] is None


def test_reply_requires_enabled_ai_and_valid_input(studio):
    ai, fake = ai_for(studio, {}, enabled=False)
    assert error_code(lambda: ai.reply({"comment": "giá"})) == "AI_DISABLED"
    ai.configure({"enabled": True})
    for bad in ({"comment": ""}, {"comment": "x" * 1001}, {"comment": "a", "extra": 1}):
        assert error_code(lambda: ai.reply(bad)) == "VALIDATION_ERROR"
    assert fake.calls == []


def test_seller_brain_uses_local_endpoint_schema_and_no_thinking(studio):
    ai, fake = ai_for(studio, {"intent": "GREETING", "reply": "Chào bạn", "needs_review": False, "reason": ""},
                      provider="seller_brain")
    ai.reply({"comment": "hello shop"})
    call = fake.calls[0]
    assert call["url"] == "http://127.0.0.1:8830/v1/chat/completions" and call["headers"] == {}
    assert call["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert call["body"]["response_format"]["type"] == "json_schema"
    assert call["body"]["model"] == "qwen3.5-9b-q4km"


def test_openai_compatible_sends_bearer_key_to_https_endpoint(studio):
    ai, fake = ai_for(studio, {"intent": "GENERAL", "reply": "Dạ", "needs_review": False, "reason": ""},
                      provider="openai_compatible", model="gpt-test-mini", base_url="https://openrouter.ai/api/v1/")
    ai.reply({"comment": "shop ơi"})
    call = fake.calls[0]
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert call["headers"] == {"Authorization": f"Bearer {KEY}"}
    assert call["body"]["response_format"] == {"type": "json_object"}


def test_provider_errors_are_redacted_and_counted(studio):
    ai, _ = ai_for(studio, RuntimeError(f"bad request for key {KEY} and sk-abcdef123456"))
    with pytest.raises(StudioError) as caught:
        ai.reply({"comment": "giá"})
    assert caught.value.code == "AI_PROVIDER_ERROR"
    assert KEY not in caught.value.message and "sk-abcdef123456" not in caught.value.message
    assert ai.config()["usage"]["errors"] == 1
    assert redact("AIzaSyAAAAAAAAAAAAAAAA", None) == "***"


def test_generated_script_is_saved_and_editable(studio):
    product = create_product(studio, "Váy hoa", product_id=701)
    ai, fake = ai_for(studio, {"title": "Kịch bản váy hoa", "body": "MỞ ĐẦU\nChào cả nhà!"}, max_output_tokens=300)
    result = ai.generate_script({"product_id": product["id"], "style": "energetic", "duration_minutes": 5,
                                 "notes": "nhấn mạnh chất voan"})
    script = result["script"]
    assert script["source"] == "ai" and script["product_id"] == product["id"] and script["title"] == "Kịch bản váy hoa"
    assert fake.calls[0]["body"]["generationConfig"]["maxOutputTokens"] == 2250
    listed = studio.dispatch("studio.scripts.list", {"product_id": product["id"]})["scripts"]
    assert [item["id"] for item in listed] == [script["id"]]
    edited = studio.dispatch("studio.scripts.update", {"id": script["id"], "body": "MỞ ĐẦU\nXin chào"})["script"]
    assert edited["body"] == "MỞ ĐẦU\nXin chào" and edited["title"] == script["title"]
    manual = studio.dispatch("studio.scripts.create", {"product_id": product["id"], "title": "Tự viết",
                                                       "body": "Nội dung"})["script"]
    assert manual["source"] == "manual"
    studio.dispatch("studio.products.delete", {"id": product["id"]})
    assert studio.dispatch("studio.scripts.list", {})["scripts"] == []


@pytest.mark.parametrize("params", [{"style": "rap"}, {"duration_minutes": 0}, {"duration_minutes": True},
                                    {"notes": 5}, {"title": "x"}])
def test_invalid_script_requests_are_rejected_before_calling_ai(studio, params):
    product = create_product(studio)
    ai, fake = ai_for(studio, {"title": "t", "body": "b"})
    assert error_code(lambda: ai.generate_script({"product_id": product["id"], **params})) == "VALIDATION_ERROR"
    assert fake.calls == []


def test_empty_script_from_ai_is_not_saved(studio):
    product = create_product(studio)
    ai, _ = ai_for(studio, {"title": "t", "body": "   "})
    assert error_code(lambda: ai.generate_script({"product_id": product["id"]})) == "AI_PROVIDER_ERROR"
    assert studio.dispatch("studio.scripts.list", {})["scripts"] == []
