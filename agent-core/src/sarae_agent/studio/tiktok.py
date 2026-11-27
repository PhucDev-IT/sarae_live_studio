"""TikTok LIVE Console connection for the local studio.

The operator signs in once in a plain Chrome window on a Studio-owned profile;
Playwright then reopens that profile read-only to observe the console's own
account_info and live_product/list responses. Cookies never leave the profile,
and Studio never replays TikTok's signed URLs.
"""
from __future__ import annotations

import logging
import threading
import urllib.error
import urllib.request
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .models import StudioError
from .service import StudioService

log = logging.getLogger(__name__)
IMAGE_HOST_SUFFIXES = (".ibyteimg.com", ".tiktokcdn.com", ".tiktokcdn-us.com", ".byteimg.com", ".tiktokcdn-eu.com")
MAX_IMAGE_BYTES = 3 * 1024 * 1024


def _image_url_allowed(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return parts.scheme == "https" and parts.port in (None, 443) and not parts.username and not parts.password and host.endswith(IMAGE_HOST_SUFFIXES)
    except (ValueError, TypeError):
        return False


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _image_url_allowed(newurl):
            raise urllib.error.HTTPError(newurl, code, "Redirect outside TikTok image hosts", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def image_suffix(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def fetch_image(url: str) -> tuple[bytes, str] | None:
    """Download one cover image; None when it is not a small JPEG/PNG/WebP from TikTok's CDN."""
    if not _image_url_allowed(url):
        return None
    opener = urllib.request.build_opener(_CheckedRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": "SaraeLiveStudio", "Accept": "image/*"})
    try:
        with opener.open(request, timeout=10) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if len(data) > MAX_IMAGE_BYTES:
        return None
    suffix = image_suffix(data)
    return (data, suffix) if suffix else None


class TikTokConnector:
    def __init__(self, service: StudioService, *, emit: Callable[[str, dict], None] = lambda *_: None,
                 browser_factory: Callable[[Path], object] | None = None,
                 image_fetcher: Callable[[str], tuple[bytes, str] | None] = fetch_image):
        self.service = service
        self.emit = emit
        self._factory = browser_factory
        self._fetch_image = image_fetcher
        self._browser = None
        self._lock = threading.Lock()
        self._syncing = threading.Lock()
        self._thumbnails: threading.Thread | None = None

    @property
    def profile_dir(self) -> Path:
        return self.service.data_dir / "tiktok-chrome-profile"

    def _get_browser(self):
        with self._lock:
            if self._browser is None:
                if self._factory is not None:
                    self._browser = self._factory(self.profile_dir)
                else:
                    from sarae_agent.auto_pin import AutoPinService
                    self._browser = AutoPinService(self.profile_dir)
            return self._browser

    def _call(self, operation):
        try:
            return operation()
        except StudioError:
            raise
        except FutureTimeout as exc:
            raise StudioError("TIKTOK_TIMEOUT", "TikTok LIVE Console phản hồi quá lâu. Hãy thử lại.") from exc
        except Exception as exc:  # noqa: BLE001 - browser errors carry an operator-facing message
            from sarae_agent.auto_pin import LoginRequired
            if isinstance(exc, LoginRequired):
                raise StudioError("TIKTOK_LOGIN_REQUIRED", "Phiên TikTok đã hết hạn. Hãy đăng nhập lại.") from exc
            log.warning("TikTok browser operation failed: %s", exc)
            raise StudioError("TIKTOK_BROWSER", str(exc)[:300] or "Không điều khiển được Chrome TikTok.") from exc

    def status(self, _params: dict | None = None) -> dict:
        browser = self._browser
        state = browser.status() if browser is not None else {"state": "idle", "browser_open": False,
                                                                 "login_open": False, "last_error": ""}
        return {"state": state.get("state", "idle"), "browser_open": bool(state.get("browser_open")),
                "login_open": bool(state.get("login_open")), "last_error": state.get("last_error") or "",
                "syncing": self._syncing.locked(), "account": self.service.tiktok_account()}

    def login(self, _params: dict | None = None) -> dict:
        self._call(lambda: self._get_browser().login_browser())
        return self.status()

    def connect(self, _params: dict | None = None) -> dict:
        browser = self._get_browser()
        self._call(lambda: browser.finish_login() if browser.login_open() else browser.open_browser())
        return self.status()

    def sync(self, _params: dict | None = None) -> dict:
        if not self._syncing.acquire(blocking=False):
            raise StudioError("TIKTOK_BUSY", "Đang đồng bộ sản phẩm TikTok, hãy đợi lượt hiện tại hoàn tất.")
        try:
            browser = self._get_browser()
            if not browser.status().get("browser_open"):
                raise StudioError("TIKTOK_NOT_CONNECTED", "Hãy kết nối TikTok LIVE Console trước khi đồng bộ.")
            scan = self._call(browser.scan_products)
            snapshot = scan.get("product_snapshot") or {}
            if not snapshot.get("complete"):
                raise StudioError("TIKTOK_SCAN_PARTIAL", "TikTok chưa trả danh sách sản phẩm kèm mã. "
                                  "Đợi LIVE Console tải xong rồi đồng bộ lại; chưa có gì thay đổi.")
            result = self.service.import_tiktok_snapshot(scan.get("account_profile"), snapshot.get("products"))
        finally:
            self._syncing.release()
        self._start_thumbnails()
        return result

    def chat_send(self, payload: dict) -> dict:
        """Post one reply in the open LIVE Console chat (never opens or navigates a browser)."""
        browser = self._browser
        if browser is None or not browser.status().get("browser_open"):
            return {"status": "FAILED", "mode": None,
                    "error": "Chưa kết nối TikTok LIVE Console trong Studio; bình luận chưa được gửi."}
        return self._call(lambda: browser.chat_send(payload))

    def detail(self, params: dict) -> dict:
        if set(params) != {"id"}:
            raise StudioError("VALIDATION_ERROR", "Cần mã sản phẩm trong Studio.")
        product = next((p for p in self.service.dispatch("studio.products.list", {})["products"]
                        if p["id"] == params["id"]), None)
        if product is None:
            raise StudioError("NOT_FOUND", "Không tìm thấy sản phẩm.")
        if not product.get("tiktok_product_id"):
            raise StudioError("VALIDATION_ERROR", "Sản phẩm này chưa có mã TikTok.")
        if not self._syncing.acquire(blocking=False):
            raise StudioError("TIKTOK_BUSY", "Đang đọc TikTok. Hãy đợi lượt hiện tại hoàn tất.")
        try:
            browser = self._get_browser()
            if not browser.status().get("browser_open"):
                raise StudioError("TIKTOK_NOT_CONNECTED", "Hãy kết nối TikTok LIVE Console trước.")
            detail = self._call(lambda: browser.read_product_detail(product["tiktok_product_id"]))
            self.service.set_setting("product_detail:" + product["id"], detail)
            return {"detail": detail}
        finally:
            self._syncing.release()

    def _start_thumbnails(self) -> None:
        if self._thumbnails is not None and self._thumbnails.is_alive():
            return
        self._thumbnails = threading.Thread(target=self._download_thumbnails, name="studio-thumbnails", daemon=True)
        self._thumbnails.start()

    def _download_thumbnails(self) -> None:
        stored = 0
        try:
            for job in self.service.thumbnail_jobs()[:1000]:
                image = self._fetch_image(job["image_url"])
                if image and self.service.store_thumbnail(job["id"], job["image_url"], *image):
                    stored += 1
        except StudioError as exc:
            log.warning("Stopped caching product images: %s", exc.code)
        if stored:
            self.emit("studio.changed", {"method": "studio.tiktok.thumbnails"})

    def close(self) -> None:
        browser = self._browser
        if browser is not None:
            try:
                browser.close()
            except Exception:  # noqa: BLE001 - shutdown must continue
                log.warning("Could not close the TikTok browser cleanly")
