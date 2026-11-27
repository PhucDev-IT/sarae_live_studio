"""Product-specific extraction, multimodal provider payloads and draft-only results."""
import base64
import json
from types import SimpleNamespace

import pytest

from sarae_agent.studio.models import StudioError
from sarae_agent.studio.product_detail import extract_detail, read_detail_page
from sarae_agent.studio.tiktok import TikTokConnector
from sarae_agent.studio.transport import StudioTransport
from test_studio_ai import studio, isolated_environment, ai_for, error_code
from studio_helpers import create_product

PID = "1737599988303759314"
JPEG = b"\xff\xd8\xff\xe0" + b"image" * 5
UPLOAD = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode("ascii")
ANSWER = {"description": "Áo cotton form rộng", "facts": {"sizes": [{"size": "M", "weight_min": 45, "weight_max": 55}], "colors": ["Trắng"], "material": "Cotton"}, "warnings": ["Chưa có chiều cao trong bảng size."]}


def payload():
    return {"data": {"product": {"product_id": PID, "title": "Áo cotton", "desc_blocks": [{"text": "Cotton 100%"}, {"image": {"url_list": ["https://p16.ibyteimg.com/detail.jpg"]}}], "images": [{"url_list": ["https://p16.ibyteimg.com/cover.jpg"]}], "sale_props": [{"prop_name": "Màu sắc", "sale_prop_values": [{"prop_value": "Trắng"}]}, {"prop_name": "Size", "sale_prop_values": [{"prop_value": "Size M (45 - 55 Kg)"}]}], "specifications": [{"name": "Chất liệu", "value": "Cotton"}], "size_guide": {"image": {"url_list": ["https://p16.ibyteimg.com/size.jpg"]}}, "skus": [{"stock": 0, "sku_sale_props": [{"prop_name": "Màu", "prop_value": "Trắng"}, {"prop_name": "Size", "prop_value": "M"}]}]}}}


def test_extracts_own_description_options_and_guide():
    result = extract_detail(PID, {}, [payload()])
    assert result["description"] == "Cotton 100%"
    assert result["colors"] == ["Trắng"] and result["sizes"] == ["M"]
    assert result["variants"][0]["stock"] == 0 and result["in_stock_colors"] == []
    assert result["size_guide_image"].endswith("size.jpg")
    assert result["description_images"][0].endswith("detail.jpg")


def test_recommendations_and_unrelated_ids_do_not_supply_missing_facts():
    product = {"product_id": PID, "title": "Áo", "recommendations": [{"product_id": "999", "title": "Khác", "description": "Sai", "sale_props": [{"prop_name": "Size", "values": ["XL"]}]}]}
    result = extract_detail(PID, {}, [product, {"seller_id": PID, "description": "Seller không phải sản phẩm"}])
    assert result["description"] is None and result["sizes"] == []
    assert extract_detail(PID, {"og_description": "Buy now"}, [{"product_id": "999", "description": "Sai"}])["description"] is None


def test_description_images_in_json_blocks_and_large_snapshots_are_bounded():
    data = payload()
    product = data["data"]["product"]
    product["desc_blocks"] = json.dumps([{"text": "á" * 20000}, {"image": {"url_list": ["https://p16.ibyteimg.com/chart.jpg"]}}])
    product["images"] = [f"https://p16.ibyteimg.com/{i}.jpg" for i in range(100)]
    record = extract_detail(PID, {}, [data])
    assert len(record["description"]) == 10000 and len(record["images"]) == 20
    assert record["description_images"] == ["https://p16.ibyteimg.com/chart.jpg"]


class FakePage:
    def __init__(self, *, incomplete=False, blocked=False):
        self.url = "https://shop.tiktok.com/view/product/" + PID
        self.closed = False
        self.visits = 0
        self.incomplete = incomplete
        self.blocked = blocked
    def on(self, *args): pass
    def remove_listener(self, *args): pass
    def goto(self, *args, **kwargs): self.visits += 1
    def wait_for_timeout(self, *args): pass
    def evaluate(self, script, arg=None):
        if "document.images" not in script: return None
        return {"h1": "Áo", "text": "verify to continue" if self.blocked else "", "images": ["https://unrelated.invalid/cover.jpg"], "scripts": [] if self.incomplete else [{"text": json.dumps(payload())}]}
    def locator(self, *args): return SimpleNamespace(count=lambda: 0)
    def close(self): self.closed = True


def test_detail_tab_is_closed_and_partial_read_retries():
    page = FakePage(incomplete=True)
    record = read_detail_page(SimpleNamespace(new_page=lambda: page), PID)
    assert record["incomplete"] and page.visits == 2 and page.closed
    assert record["images"] == []
    assert "scripts" not in record and "responses" not in record
    page = FakePage()
    record = read_detail_page(SimpleNamespace(new_page=lambda: page), PID)
    assert not record["incomplete"] and page.closed and page.visits == 1


def test_verification_stops_read_and_closes_tab():
    page = FakePage(blocked=True)
    assert error_code(lambda: read_detail_page(SimpleNamespace(new_page=lambda: page), PID)) == "TIKTOK_VERIFICATION"
    assert page.closed


def test_connector_caches_details_without_changing_product(studio):
    product = create_product(studio, product_id=int(PID))
    browser = SimpleNamespace(status=lambda: {"browser_open": True}, read_product_detail=lambda _: {"product_id": PID, "description": "Đọc mới"})
    transport = StudioTransport(studio)
    transport.tiktok = TikTokConnector(studio, browser_factory=lambda _: browser)
    assert transport.dispatch("studio.products.detail", {"id": product["id"]}) == {"detail": None}
    result = transport.dispatch("studio.tiktok.detail", {"id": product["id"]})
    assert transport.dispatch("studio.products.detail", {"id": product["id"]}) == result
    assert studio.product_context(product["id"])["description"] == ""
    assert error_code(lambda: transport.dispatch("studio.tiktok.detail", {})) == "VALIDATION_ERROR"
    studio.dispatch("studio.products.delete", {"id": product["id"]})
    assert studio.get_setting("product_detail:" + product["id"], {}) == {}


@pytest.mark.parametrize("provider", ["gemini", "openai_compatible", "seller_brain"])
def test_multimodal_payload_and_draft_does_not_save(studio, provider):
    product = create_product(studio)
    config = {"model": "vision-test"} if provider == "openai_compatible" else {}
    ai, fake = ai_for(studio, ANSWER, provider=provider, **config)
    transport = StudioTransport(studio)
    transport.ai = ai
    result = transport.dispatch("studio.ai.product_analyze", {"id": product["id"], "text": "M 45-55kg", "images": [UPLOAD]})
    assert result["facts"]["sizes"][0]["weight_min"] == 45
    assert result["facts"]["sizes"][0]["height_min"] is None
    assert result["images_used"] == 1 and result["warnings"]
    body = fake.calls[0]["body"]
    if provider == "gemini":
        assert body["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "image/jpeg"
    else:
        assert body["messages"][1]["content"][1]["image_url"]["url"] == UPLOAD
    assert studio.product_context(product["id"])["description"] == ""
    assert studio.product_context(product["id"])["facts"]["sizes"] == []


def test_stored_guide_has_priority_and_failed_download_is_reported(studio, monkeypatch):
    product = create_product(studio)
    urls = ["https://p16.ibyteimg.com/size.jpg", "https://p16.ibyteimg.com/desc.jpg"]
    studio.set_setting("product_detail:" + product["id"], {"size_guide_image": urls[0], "description_images": [urls[1]], "description": "Cotton"})
    calls = []
    def fetch(url):
        calls.append(url)
        return (JPEG, ".jpg") if url == urls[0] else None
    monkeypatch.setattr("sarae_agent.studio.tiktok.fetch_image", fetch)
    ai, fake = ai_for(studio, ANSWER)
    result = ai.product_analyze({"id": product["id"]})
    assert calls == urls and result["images_used"] == 1 and len(result["warnings"]) == 2


@pytest.mark.parametrize("bad", [{"facts": {"sizes": [{"size": "M", "weight_min": 99, "weight_max": 40}]}}, {"warnings": "oops"}, {"description": 5}])
def test_invalid_ai_results_do_not_change_data(studio, bad):
    product = create_product(studio)
    ai, _ = ai_for(studio, {**ANSWER, **bad})
    assert error_code(lambda: ai.product_analyze({"id": product["id"], "text": "nguồn"})) == "AI_INVALID_RESULT"
    assert studio.product_context(product["id"])["description"] == ""


def test_upload_validation_and_missing_sources_precede_provider_call(studio):
    product = create_product(studio)
    ai, fake = ai_for(studio, ANSWER)
    for params in ({"images": ["https://private.invalid/image.jpg"]}, {"images": [UPLOAD] * 5}, {"images": [UPLOAD.replace("image/jpeg", "image/png")]}, {"text": "x" * 20001}, {}):
        assert error_code(lambda: ai.product_analyze({"id": product["id"], **params})) == "VALIDATION_ERROR"
    assert fake.calls == []
