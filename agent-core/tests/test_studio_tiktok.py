"""TikTok-only product catalog: snapshot import, connector flow and cached covers (no network, no Chrome)."""
from __future__ import annotations

import sqlite3

import pytest

from sarae_agent.studio.service import StudioError, StudioService
from sarae_agent.studio.tiktok import TikTokConnector, _image_url_allowed, image_suffix
from studio_helpers import ACCOUNT, SELLER, tiktok_row

JPEG = b"\xff\xd8\xff\xe0" + b"cover" * 10


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


def products(studio):
    return studio.dispatch("studio.products.list", {})["products"]


def error_code(operation) -> str:
    with pytest.raises(StudioError) as caught:
        operation()
    return caught.value.code


def test_snapshot_creates_products_with_parsed_tiktok_fields(studio):
    result = studio.import_tiktok_snapshot(ACCOUNT, [
        tiktok_row("Áo linen", product_id=11, price_text="389.000₫", origin_price_text="₫459.000",
                   stock_num=7, category_name="Áo sơ mi", image_url="https://p16-oec-sg.ibyteimg.com/a.jpg"),
        tiktok_row("Váy hoa", product_id=12, price_text=None, origin_price_text="", stock_num=None),
    ])
    assert result | {"account": None} == {"created": 2, "updated": 0, "missing": 0, "total": 2, "account": None}
    assert result["account"]["user_id"] == ACCOUNT["user_id"] and result["account"]["product_count"] == 2
    by_id = {item["tiktok_product_id"]: item for item in products(studio)}
    linen, dress = by_id["11"], by_id["12"]
    assert (linen["name"], linen["price"], linen["origin_price"], linen["stock_num"], linen["category"]) == \
        ("Áo linen", 389000, 459000, 7, "Áo sơ mi")
    assert linen["source"] == "tiktok" and linen["tiktok_seller_id"] == SELLER and linen["tiktok_product_id"] == "11"
    assert linen["description"] == "" and linen["has_thumbnail"] is False
    assert (dress["price"], dress["origin_price"], dress["stock_num"]) == (None, None, None)
    assert studio.tiktok_account()["nick_name"] == "Shop Việt"


def test_resync_refreshes_tiktok_fields_and_keeps_studio_data(studio, tmp_path):
    studio.import_tiktok_snapshot(ACCOUNT, [tiktok_row("Áo cũ", product_id=21), tiktok_row("Quần", product_id=22)])
    first = next(item for item in products(studio) if item["tiktok_product_id"] == "21")
    studio.dispatch("studio.products.update", {"id": first["id"], "description": "Linen mát, form rộng"})
    presenter = studio.dispatch("studio.presenters.create", {"name": "Chị Lan"})["presenter"]
    connection = sqlite3.connect(studio.database_path)
    try:  # a video link must survive a re-sync; insert it without FFprobe
        with connection:
            connection.execute("""INSERT INTO media(id, product_id, original_name, relative_path, size_bytes, sha256,
                mime_type, created_at) VALUES ('00000000-0000-4000-8000-000000000001', ?, 'a.mp4',
                '00000000-0000-4000-8000-000000000001.mp4', 1, ?, 'video/mp4', 'now')""", (first["id"], "0" * 64))
    finally:
        connection.close()
    result = studio.import_tiktok_snapshot(ACCOUNT, [tiktok_row("Áo mới tên", product_id=21, price_text="99.000₫")])
    assert (result["created"], result["updated"], result["missing"]) == (0, 1, 1)
    refreshed = next(item for item in products(studio) if item["tiktok_product_id"] == "21")
    assert refreshed["id"] == first["id"] and refreshed["name"] == "Áo mới tên" and refreshed["price"] == 99000
    assert refreshed["description"] == "Linen mát, form rộng"
    assert studio.dispatch("studio.media.list", {"product_id": first["id"]})["media"][0]["product_id"] == first["id"]
    # A product TikTok no longer lists is kept, never deleted.
    assert {item["tiktok_product_id"] for item in products(studio)} == {"21", "22"}
    assert presenter["id"]


def test_same_product_id_in_two_shops_stays_separate(studio):
    studio.import_tiktok_snapshot(ACCOUNT, [tiktok_row("Áo", product_id=31, seller_id="9001"),
                                            tiktok_row("Áo", product_id=31, seller_id="9002")])
    assert len(products(studio)) == 2


@pytest.mark.parametrize("bad", [
    {"product_id": "abc"}, {"product_id": ""}, {"seller_id": None}, {"seller_id": "0"}, {"name": "  "},
    {"product_id": "1" * 41},
])
def test_invalid_row_rejects_the_whole_snapshot(studio, bad):
    good, broken = tiktok_row("Tốt", product_id=41), tiktok_row("Xấu")
    broken.update(bad)
    assert error_code(lambda: studio.import_tiktok_snapshot(ACCOUNT, [good, broken])) \
        == "TIKTOK_SNAPSHOT_INVALID"
    assert products(studio) == []
    assert studio.tiktok_account() is None


def test_duplicate_rows_and_unverified_account_are_rejected(studio):
    row = tiktok_row("Áo", product_id=51)
    assert error_code(lambda: studio.import_tiktok_snapshot(ACCOUNT, [row, dict(row)])) == "TIKTOK_SNAPSHOT_INVALID"
    assert error_code(lambda: studio.import_tiktok_snapshot(None, [row])) == "TIKTOK_ACCOUNT_UNVERIFIED"
    assert error_code(lambda: studio.import_tiktok_snapshot({"user_id": "x"}, [row])) == "TIKTOK_SNAPSHOT_INVALID"
    assert products(studio) == []


def test_unsafe_stock_and_image_values_are_dropped(studio):
    studio.import_tiktok_snapshot(ACCOUNT, [tiktok_row("Áo", product_id=61, stock_num=True,
                                                       image_url="javascript:alert(1)")])
    product = products(studio)[0]
    assert product["stock_num"] is None and product["image_url"] == ""


def test_products_cannot_be_created_or_renamed_by_hand(studio):
    studio.import_tiktok_snapshot(ACCOUNT, [tiktok_row("Áo", product_id=71)])
    product = products(studio)[0]
    assert error_code(lambda: studio.dispatch("studio.products.create", {"name": "Tạo tay"})) == "METHOD_NOT_FOUND"
    for field, value in (("name", "Đổi"), ("price", 1)):
        assert error_code(lambda: studio.dispatch("studio.products.update",
                                                  {"id": product["id"], field: value})) == "VALIDATION_ERROR"
    assert error_code(lambda: studio.dispatch("studio.products.update", {"id": product["id"]})) == "VALIDATION_ERROR"
    assert error_code(lambda: studio.dispatch("studio.products.update",
                                              {"id": product["id"], "description": 5})) == "VALIDATION_ERROR"
    assert products(studio)[0]["name"] == "Áo"


def test_thumbnail_is_stored_resolved_and_replaced_when_cover_changes(studio):
    url = "https://p16-oec-sg.ibyteimg.com/a.jpg"
    studio.import_tiktok_snapshot(ACCOUNT, [tiktok_row("Áo", product_id=81, image_url=url)])
    product = products(studio)[0]
    assert studio.thumbnail_jobs() == [{"id": product["id"], "image_url": url}]
    assert not studio.store_thumbnail(product["id"], "https://p16-oec-sg.ibyteimg.com/other.jpg", JPEG, ".jpg")
    assert studio.store_thumbnail(product["id"], url, JPEG, ".jpg")
    assert studio.resolve_thumbnail(product["id"]).read_bytes() == JPEG
    assert products(studio)[0]["has_thumbnail"] is True and studio.thumbnail_jobs() == []
    studio.import_tiktok_snapshot(ACCOUNT, [tiktok_row("Áo", product_id=81, image_url=url.replace("a.jpg", "b.jpg"))])
    assert products(studio)[0]["has_thumbnail"] is False
    assert error_code(lambda: studio.resolve_thumbnail(product["id"])) == "NOT_FOUND"
    assert list((studio.data_dir / "thumbnails").iterdir()) == []


def test_deleting_a_product_removes_its_cached_cover(studio):
    url = "https://p16-oec-sg.ibyteimg.com/a.jpg"
    studio.import_tiktok_snapshot(ACCOUNT, [tiktok_row("Áo", product_id=91, image_url=url)])
    product = products(studio)[0]
    studio.store_thumbnail(product["id"], url, JPEG, ".jpg")
    studio.dispatch("studio.products.delete", {"id": product["id"]})
    assert list((studio.data_dir / "thumbnails").iterdir()) == []


def test_image_rules_accept_only_tiktok_cdn_images():
    assert _image_url_allowed("https://p16-oec-sg.ibyteimg.com/x.jpeg")
    assert not _image_url_allowed("http://p16-oec-sg.ibyteimg.com/x.jpeg")
    assert not _image_url_allowed("https://evil.example/ibyteimg.com.jpg")
    assert not _image_url_allowed("https://ibyteimg.com.evil.example/x.jpg")
    assert not _image_url_allowed("https://p16.ibyteimg.com:8443/x.jpg")
    assert image_suffix(JPEG) == ".jpg"
    assert image_suffix(b"\x89PNG\r\n\x1a\nrest") == ".png"
    assert image_suffix(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ".webp"
    assert image_suffix(b"<svg onload=alert(1)>") is None


class FakeBrowser:
    def __init__(self, scan=None, *, open_=True):
        self.scan = scan
        self.open = open_
        self.calls = []

    def status(self):
        return {"state": "ready" if self.open else "idle", "browser_open": self.open, "login_open": False}

    def login_open(self):
        return False

    def login_browser(self):
        self.calls.append("login")

    def open_browser(self):
        self.calls.append("open")
        self.open = True

    def scan_products(self):
        self.calls.append("scan")
        if isinstance(self.scan, Exception):
            raise self.scan
        return self.scan

    def close(self):
        self.calls.append("close")


def connector(studio, browser, events=None, fetch=lambda url: (JPEG, ".jpg")):
    return TikTokConnector(studio, emit=lambda name, data: (events if events is not None else []).append(name),
                           browser_factory=lambda _: browser, image_fetcher=fetch)


def test_connector_syncs_complete_snapshot_and_caches_covers(studio):
    url = "https://p16-oec-sg.ibyteimg.com/a.jpg"
    browser = FakeBrowser({"product_snapshot": {"complete": True, "products": [tiktok_row("Áo", image_url=url)]},
                           "account_profile": ACCOUNT})
    events = []
    tiktok = connector(studio, browser, events)
    assert tiktok.sync()["created"] == 1
    tiktok._thumbnails.join(timeout=5)
    assert products(studio)[0]["has_thumbnail"] is True
    assert events == ["studio.changed"]
    status = tiktok.status()
    assert status["account"]["user_id"] == ACCOUNT["user_id"] and status["syncing"] is False
    tiktok.close()
    assert browser.calls == ["scan", "close"]


def test_connector_requires_connection_and_complete_scan(studio):
    assert error_code(connector(studio, FakeBrowser(open_=False)).sync) == "TIKTOK_NOT_CONNECTED"
    partial = FakeBrowser({"product_snapshot": {"complete": False, "products": [{"name": "Chỉ tên"}]}})
    assert error_code(connector(studio, partial).sync) == "TIKTOK_SCAN_PARTIAL"
    assert error_code(connector(studio, FakeBrowser(RuntimeError("Chrome đã đóng"))).sync) == "TIKTOK_BROWSER"
    assert products(studio) == []


def test_connector_login_and_connect_use_the_studio_profile(studio):
    seen = []
    browser = FakeBrowser(open_=False)
    tiktok = TikTokConnector(studio, browser_factory=lambda path: seen.append(path) or browser)
    tiktok.login()
    tiktok.connect()
    assert browser.calls == ["login", "open"]
    assert seen == [studio.data_dir / "tiktok-chrome-profile"]
    assert tiktok.status()["browser_open"] is True
