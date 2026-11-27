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


@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-5-mini", "o3", "o4-mini"])
def test_official_openai_reasoning_models_use_supported_parameters(studio, model):
    ai, fake = ai_for(studio, {"intent": "GREETING", "reply": "Chào bạn", "needs_review": False, "reason": ""},
                      provider="openai_compatible", model=model)
    ai.reply({"comment": "hello"})
    body = fake.calls[0]["body"]
    assert body["max_completion_tokens"] == 300
    assert "max_tokens" not in body and "temperature" not in body
    assert body["response_format"] == {"type": "json_object"}


def test_official_openai_standard_model_keeps_temperature(studio):
    ai, fake = ai_for(studio, {"intent": "GREETING", "reply": "Chào bạn", "needs_review": False, "reason": ""},
                      provider="openai_compatible", model="gpt-4.1-mini", temperature=0.7)
    ai.reply({"comment": "hello"})
    assert fake.calls[0]["body"]["max_completion_tokens"] == 300
    assert fake.calls[0]["body"]["temperature"] == 0.7


def test_compatible_endpoint_keeps_its_token_and_temperature_contract(studio):
    ai, fake = ai_for(studio, {"intent": "GREETING", "reply": "Chào bạn", "needs_review": False, "reason": ""},
                      provider="openai_compatible", model="gpt-6-luna", base_url="https://gateway.example/v1")
    ai.reply({"comment": "hello"})
    body = fake.calls[0]["body"]
    assert body["max_tokens"] == 300 and body["temperature"] == 0.4
    assert "max_completion_tokens" not in body


@pytest.mark.parametrize("status,body", [
    (400, {"error": {"message": "Unsupported parameter for " + KEY, "param": "max_tokens", "code": "unsupported_parameter"}}),
    (422, {"error": "Invalid image"}), (400, []),
])
def test_http_bad_request_exposes_sanitized_provider_reason(monkeypatch, status, body):
    import httpx
    response = httpx.Response(status, json=body, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs): return response
    monkeypatch.setattr(httpx, "Client", Client)
    with pytest.raises(StudioError) as caught:
        StudioAI._httpx_post("https://api.openai.com/v1/chat/completions", headers={"Authorization": "Bearer " + KEY}, body={}, timeout=1)
    assert caught.value.code == "AI_BAD_REQUEST"
    assert caught.value.details["status"] == status
    assert KEY not in caught.value.message and KEY not in json.dumps(caught.value.details)
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        assert caught.value.details["param"] == "max_tokens"
        assert "Unsupported parameter" in caught.value.message


def test_reply_prompt_sizes_by_weight_when_height_is_missing():
    from sarae_agent.studio.ai import reply_prompt
    system, _ = reply_prompt("50kg mặc size gì", "Lan", None)
    rule = system.split("5.")[1].split("6.")[0]
    # Weight alone is enough: no "need your height" and no operator review for it.
    assert "chỉ cho cân nặng" in rule and "không bắt hỏi chiều cao" in rule and "needs_review = false" in rule
    assert "ưu tiên cân nặng" in rule and "ngoài bảng size" in rule


def test_a_weight_only_chart_hides_height_from_the_ai():
    from sarae_agent.studio.ai import product_prompt_context, reply_prompt
    product = {"name": "Set HELO", "price": 109000, "facts": {"colors": ["Xanh than"], "sizes": [
        {"size": "M", "weight_min": 40, "weight_max": 50, "height_min": None, "height_max": None, "note": ""},
        {"size": "L", "weight_min": 50, "weight_max": 58, "height_min": None, "height_max": None, "note": "form rộng"}]}}
    facts = product_prompt_context(product)["thong_tin"]
    assert facts["sizes"] == [{"size": "M", "weight_min": 40, "weight_max": 50},
                              {"size": "L", "weight_min": 50, "weight_max": 58, "note": "form rộng"}]
    assert facts["bang_size_chi_theo"] == ["cân nặng"] and facts["colors"] == ["Xanh than"]
    assert product["facts"]["sizes"][0]["height_min"] is None  # the stored product is untouched
    system, data = reply_prompt("1m60 48kg mặc size gì", "Lan", product)
    assert "bỏ qua" in system and "KHÔNG nhắc tới" in system and "chưa xác định" in system
    assert "height" not in data and "bang_size_chi_theo" in data
    # A chart with both measures keeps both; no chart leaves facts alone.
    both = {"sizes": [{"size": "M", "weight_min": 40, "weight_max": 50, "height_min": 150, "height_max": 160}]}
    assert product_prompt_context({"name": "x", "facts": both})["thong_tin"]["bang_size_chi_theo"] == ["cân nặng", "chiều cao"]
    assert product_prompt_context({"name": "x", "facts": {"colors": ["Đen"]}})["thong_tin"] == {"colors": ["Đen"]}


def test_reply_prompt_asks_back_on_ambiguous_questions_instead_of_guessing():
    from sarae_agent.studio.ai import reply_prompt
    system, data = reply_prompt("quần dài bn ạ", "Mẹ Cải Bắp", None)
    # "bn" can be the price of the trousers alone or their length: ask back and wait for the operator.
    assert "'quần dài bn'" in system and "hỏi lại" in system and "giá cả set" in system
    assert "needs_review = true" in system.split("7.")[1]
    assert json.loads(data.removeprefix("DATA:\n"))["COMMENT"] == "quần dài bn ạ"
