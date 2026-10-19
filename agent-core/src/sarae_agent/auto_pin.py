"""Auto-pin TikTok LIVE Console products, in two steps that share one dedicated Chrome profile.

1. Login: the installed Chrome starts as an ordinary process (no Playwright, no automation flags) on
   ``profile_dir`` and the operator signs in by hand, exactly as in a normal browser.
2. Control: once that Chrome has closed (the operator clicks "Đã đăng nhập xong", or closes it), Playwright
   opens the same profile, so cookies and session are reused, and pins products on the LIVE Console.

Chrome cannot run twice on one profile, so step 2 refuses to start while the login Chrome still holds it.
The product list is read from the console itself (scan_products): every row with a Ghim/Pin button is a
product, named by its longest text line. The operator only removes the ones not to pin.

If the console redirects to a login / verification page, auto-pin stops and asks for step 1 again. This
module never types credentials, never touches CAPTCHA or verification dialogs, and adds no fingerprint or
stealth tricks. DOM selectors may need updates when the console UI changes.
"""
from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

log = logging.getLogger("sarae.autopin")
LIVE_ORIGINS = {"shop.tiktok.com", "livecenter.tiktok.com", "seller-vn.tiktok.com", "seller.tiktokglobalshop.com",
                "seller-us.tiktok.com"}
DEFAULT_URL = "https://shop.tiktok.com/streamer/live/product/dashboard"
LOGIN_HOSTS = {"www.tiktok.com", "tiktok.com", "m.tiktok.com"}
LOGIN_MARKERS = ("login", "signin", "sign_in", "sign-in", "verify", "verification", "passport", "captcha")
RELOGIN = "TikTok yêu cầu đăng nhập lại: dùng bước 1 (Đăng nhập TikTok bằng Chrome thường)"
LOGIN_CHROME_OPEN = "Chrome đăng nhập vẫn đang mở: bấm \"Đã đăng nhập xong\" hoặc đóng cửa sổ Chrome đó trước"
MAX_PRODUCTS = 100

# Runs in the LIVE Console page (read only). A product row is the largest ancestor of a Ghim/Pin button that
# contains no other such button; its name is the longest text line that is not a button label, price or count.
SCAN_PRODUCTS_JS = r"""
() => {
  // TikTok's pre-LIVE dashboard has sortable order inputs, NOT Pin buttons.
  const container = [...document.querySelectorAll('input.pc_order_input, input[role="spinbutton"][data-tid="m4b_input_number"]')];
  const result = [];
  for (const input of container) {
    let root = input;
    for (let i=0; i<9 && root.parentElement; i++) {
      root = root.parentElement;
      if (root.querySelector('img') && root.querySelector('.text-body-m-regular span')) break;
    }
    const title = root.querySelector('.text-body-m-regular span');
    const img = root.querySelector('img');
    const name = (title?.textContent || '').trim();
    if (!name || !img) continue;
    const position = parseInt(input.value, 10);
    const text = (root.innerText || '');
    const priceText = text.match(/[\d.,]+\s*₫/)?.[0] || null;
    const stockText = text.match(/Còn:\s*[^\n]+/)?.[0] || null;
    result.push({
      name, position: Number.isFinite(position) ? position : null,
      image_url: img.currentSrc || img.src || null,
      price_text: priceText, stock_text: stockText
    });
  }
  const scroller = container[0] ? (() => {
    let p = container[0].parentElement;
    while (p && p!==document.body) {
      if (p.scrollHeight > p.clientHeight+30 && getComputedStyle(p).overflowY !== 'visible') return p;
      p=p.parentElement;
    }
    return null;
  })() : null;
  const scroll = scroller ? {top:scroller.scrollTop, height:scroller.scrollHeight, client:scroller.clientHeight} : null;
  if (scroller) scroller.scrollTop = Math.min(scroller.scrollTop + Math.max(100,scroller.clientHeight*0.85),scroller.scrollHeight);
  return {rows:result,scroll};
}
"""


class LoginRequired(RuntimeError):
    """The console sent the controlled browser to a login or verification page."""


def needs_login(url: str) -> bool:
    """True for TikTok sign-in / verification pages (the operator must log in again in plain Chrome)."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    # On the LIVE Console only the path counts (its query strings may carry arbitrary words).
    where = (parts.path if host in LIVE_ORIGINS else parts.path + "?" + parts.query).lower()
    if host in LOGIN_HOSTS or host.startswith("accounts.") or host.startswith("passport."):
        return True
    return any(marker in where for marker in LOGIN_MARKERS)


def _registry_chrome() -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                value, _ = winreg.QueryValueEx(key, "")
                if value:
                    return str(value).strip('"')
        except OSError:
            continue
    return None


def find_chrome(env: dict | None = None, exists: Callable[[str], bool] = os.path.isfile,
                registry: Callable[[], str | None] = _registry_chrome) -> str:
    """Installed Google Chrome: App Paths registry entry first, then the standard install folders."""
    env = os.environ if env is None else env
    candidates = [registry()]
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = env.get(var)
        if base:
            candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    for path in candidates:
        if path and exists(path):
            return path
    raise RuntimeError("Không tìm thấy Google Chrome trên máy; hãy cài Chrome trước")


def _check_url(url: str) -> str:
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname not in LIVE_ORIGINS:
        raise ValueError("Chỉ mở TikTok LIVE Console trên tên miền được hỗ trợ")
    return url


class AutoPinService:
    LOGIN_CLOSE_SECONDS = 15.0

    def __init__(self, profile_dir: Path, *, chrome_finder: Callable[[], str] = find_chrome,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen):
        self.profile_dir = profile_dir
        self._find_chrome = chrome_finder
        self._popen = popen
        self._lock = threading.RLock()
        self._thread = None
        self._queue: queue.Queue = queue.Queue()
        self._context = None
        self._playwright = None
        self._page = None
        self._login_proc = None
        self._url = DEFAULT_URL
        self._items: list[str] = []
        self._found: list[dict] = []
        self._index = 0
        self._seconds = 30
        self._enabled = False
        self._last_error = ""
        self._last_pin = ""
        self._state = "idle"
        self._product_snapshot = None
        self._network_products = None
        self._observed_pages = set()

    # --- status --------------------------------------------------------------------------
    def login_open(self) -> bool:
        proc = self._login_proc
        return proc is not None and proc.poll() is None

    def status(self):
        with self._lock:
            login_open = self.login_open()
            if self._state == "login" and not login_open:
                self._state = "logged_in"  # the operator closed the login Chrome: step 2 can start
            return {"state": self._state, "enabled": self._enabled, "interval_seconds": self._seconds,
                    "products": list(self._items), "current_index": self._index,
                    "last_pin": self._last_pin, "last_error": self._last_error,
                    "browser_open": self._context is not None, "login_open": login_open,
                    "found": len(self._found)}

    # --- step 1: login in plain Chrome ---------------------------------------------------
    def _profile_locked(self) -> bool:
        """Chrome keeps <profile>/lockfile open without write sharing while it runs on that profile."""
        lock = self.profile_dir / "lockfile"
        if not lock.exists():
            return False
        try:
            os.close(os.open(lock, os.O_RDWR))
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def login_browser(self, url: str = DEFAULT_URL):
        """Start the installed Chrome on the Auto Pin profile for a manual sign-in (no automation at all)."""
        _check_url(url)
        if self.login_open():
            raise RuntimeError("Chrome đăng nhập đang mở; hãy đăng nhập trong cửa sổ đó")
        if self._context is not None:
            self._request("disconnect")  # the profile cannot be open in Playwright and plain Chrome at once
        chrome = self._find_chrome()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._url = url
            self._login_proc = self._popen([chrome, f"--user-data-dir={self.profile_dir}", "--no-first-run",
                                            "--no-default-browser-check", "--new-window", url])
            self._enabled = False
            self._state = "login"
            self._last_error = ""
        log.info("Đã mở Chrome thường để đăng nhập TikTok (pid %s)", getattr(self._login_proc, "pid", "?"))
        return self.status()

    def _close_login_chrome(self) -> None:
        """Close the login Chrome gracefully (taskkill without /F = a normal window close, cookies flushed)."""
        proc = self._login_proc
        if proc is not None and proc.poll() is None:
            try:
                subprocess.run(["taskkill", "/PID", str(proc.pid)], capture_output=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                proc.wait(timeout=self.LOGIN_CLOSE_SECONDS)
            except subprocess.TimeoutExpired:
                raise RuntimeError(LOGIN_CHROME_OPEN) from None
        deadline = time.monotonic() + self.LOGIN_CLOSE_SECONDS
        while self._profile_locked():  # helper processes may hold the profile a moment longer
            if time.monotonic() > deadline:
                raise RuntimeError(LOGIN_CHROME_OPEN)
            time.sleep(0.3)

    def finish_login(self):
        """Operator signed in: close the login Chrome, then let Playwright take the same profile."""
        self._close_login_chrome()
        return self._request("open", self._url)

    # --- step 2: Playwright control ------------------------------------------------------
    def _ensure_worker(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="auto-pin-browser", daemon=True)
        self._thread.start()

    def _request(self, method: str, *args):
        with self._lock:
            self._ensure_worker()
        result = Future()
        self._queue.put((method, args, result))
        return result.result(timeout=60)

    def open_browser(self, url: str = DEFAULT_URL):
        return self._request("open", _check_url(url))

    def _drop_context(self) -> None:
        for close in (getattr(self._context, "close", None), getattr(self._playwright, "stop", None)):
            if close is not None:
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
        self._context = None
        self._playwright = None
        self._page = None
        self._observed_pages.clear()

    def _landed(self) -> None:
        if self._page is not None and needs_login(self._page.url):
            self._state = "login_required"
            self._last_error = ""
            return
        self._state = "ready"
        self._last_error = ""

    def _capture_product_response(self,response):
        """Observe the page's own authenticated request; never copy cookies or signatures."""
        from urllib.parse import urlsplit
        if urlsplit(response.url).path!="/api/v1/streamer_desktop/live_product/list":
            return
        if response.status!=200:return
        try:
            body=response.json()
            if body.get("code")!=0:return
            raw=body.get("data",{}).get("products")
            if not isinstance(raw,list):return
            result=[]
            for x in raw:
                seller=str((x.get("seller_info") or {}).get("seller_id") or "")
                pid=str(x.get("product_id") or "")
                name=str(x.get("title") or "").strip()
                if not seller.isdigit() or not pid.isdigit() or not name:continue
                cover=(x.get("cover") or {}).get("url_list") or []
                category=x.get("category_info") or {}
                result.append({"seller_id":seller,"product_id":pid,"name":name,
                               "image_url":cover[0] if cover else None,
                               "price_text":x.get("format_available_price"),
                               "origin_price_text":x.get("format_origin_price"),
                               "stock_num":x.get("stock_num"),
                               "category_name":category.get("name")})
            if len(result)==len(raw):
                self._network_products=result
                log.info("TikTok Network captured %s products",len(result))
        except Exception as exc:
            log.warning("Không đọc được TikTok products response: %s",exc)

    def _attach_response_listener(self,page):
        if id(page) not in self._observed_pages:
            page.on("response",self._capture_product_response)
            self._observed_pages.add(id(page))

    def _open_on_worker(self, url):
        if self.login_open() or self._profile_locked():
            raise RuntimeError(LOGIN_CHROME_OPEN)
        self._url = url
        self._network_products = None
        if self._context is not None:
            try:
                if self._page is None or self._page.is_closed():
                    pages = [p for p in self._context.pages if not p.is_closed()]
                    self._page = pages[0] if pages else self._context.new_page()
                self._attach_response_listener(self._page)
                self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                # A closed Chrome context cannot be reused.
                self._drop_context()
                raise RuntimeError("Chrome đã đóng hoặc trang bị ngắt khi điều hướng; hãy bấm lại Bắt đầu")
            self._landed()
            return self.status()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Thiếu playwright trong Agent Core") from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir), channel="chrome", headless=False,
                accept_downloads=False, viewport=None)
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._attach_response_listener(self._page)
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            self._drop_context()
            self._last_error = str(exc)[:300]
            raise
        self._landed()
        return self.status()

    def scan_products(self):
        """Read the products of the open LIVE Console and make them the pin list (all of them)."""
        return self._request("scan")

    def _scan_on_worker(self) -> dict:
        """Read visible DOM rows while scrolling the TikTok product container (read-only)."""
        page=self._page
        if self._context is None or page is None:
            raise RuntimeError("Chưa mở Chrome điều khiển: đăng nhập rồi bấm đồng bộ")
        if needs_login(page.url):
            raise LoginRequired(RELOGIN)
        if urlsplit(page.url).hostname not in LIVE_ORIGINS:
            raise RuntimeError("Trang hiện tại không phải TikTok LIVE Console")
        if not page.url.startswith(DEFAULT_URL):
            raise RuntimeError("Hãy mở đúng trang TikTok LIVE Product Dashboard")
        # A fresh scan must not reuse a prior shop's response. Refresh once to trigger
        # the console's own authenticated XHR; never reconstruct its signed URL.
        self._network_products = None
        page.reload(wait_until="domcontentloaded", timeout=30000)
        for _ in range(25):
            if self._network_products is not None:
                break
            page.wait_for_timeout(200)
        if self._network_products is not None:
            rows=list(self._network_products)
            with self._lock:
                self._found=[{"name":x["name"],"pinned":False} for x in rows]
                self._items=[x["name"] for x in rows[:MAX_PRODUCTS]]
                self._product_snapshot={"complete":True,"products":rows}
            return {**self.status(),"products_found":self._found,
                    "product_snapshot":self._product_snapshot}
        # Old console revisions: retain read-only DOM diagnostics. The Cloud import
        # refuses DOM-only rows because they have no trustworthy TikTok IDs.
        found={}
        complete=False
        last=None
        stagnant=0
        for _ in range(150):
            part=page.evaluate(SCAN_PRODUCTS_JS)
            rows=part.get("rows",[])
            scroll=part.get("scroll")
            for row in rows:
                name=str(row.get("name") or "").strip()
                if not name:continue
                key=name.casefold()
                if key not in found:found[key]=row
            if not rows:break
            if not scroll:
                complete=True
                break
            signature=(round(scroll["top"]),len(found))
            stagnant=stagnant+1 if signature==last else 0
            last=signature
            if scroll["top"]+scroll["client"] >= scroll["height"]-5:
                complete=True
                break
            if stagnant>=4:break
            page.wait_for_timeout(120)
        rows=sorted(found.values(),key=lambda x:x.get("position") or 999999)
        if not rows:
            raise RuntimeError("Không tìm thấy ô thứ tự sản phẩm (.pc_order_input) trên trang TikTok")
        with self._lock:
            self._found=[{"name":x["name"],"pinned":False} for x in rows]
            self._items=[x["name"] for x in rows[:MAX_PRODUCTS]]
            self._index=0
            self._product_snapshot={"complete":complete and len(rows)<=1000,"products":rows}
        log.info("TikTok DOM scan: %s sản phẩm, complete=%s",len(rows),complete)
        return {**self.status(),"products_found":self._found,
                "product_snapshot":self._product_snapshot}

    def configure(self, products: list[str], interval_seconds: int):
        if not 5 <= interval_seconds <= 3600:
            raise ValueError("Chu kỳ phải từ 5 đến 3600 giây")
        cleaned = [v.strip() for v in products if v.strip()]
        if not cleaned or len(cleaned) > MAX_PRODUCTS:
            raise ValueError("Cần 1–100 mã hoặc tên sản phẩm")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Danh sách sản phẩm trùng nhau")
        with self._lock:
            self._items = cleaned
            self._seconds = interval_seconds
            self._index = 0
        return self.status()

    def _pin(self, term: str):
        page = self._page
        if page is None:
            raise RuntimeError("Chưa mở Chrome điều khiển")
        if needs_login(page.url):
            raise LoginRequired(RELOGIN)
        if page.url.startswith("chrome://"):
            raise RuntimeError("Chưa mở LIVE Console")
        if urlsplit(page.url).hostname not in LIVE_ORIGINS:
            raise RuntimeError("Trang hiện tại không phải TikTok LIVE Console")
        # Target a product row by its visible product name/ID and click its Pin button.
        # Avoid guessing coordinates and avoid interacting with challenge dialogs.
        row = page.get_by_text(term, exact=True).first
        if row.count() == 0:
            raise RuntimeError("Không tìm thấy sản phẩm: " + term)
        candidate = row.locator("xpath=ancestor::*[.//button][1]")
        button = candidate.get_by_role("button", name=re.compile(r"^(pin|ghim)$", re.I))
        if button.count() != 1:
            raise RuntimeError("Không xác định được duy nhất nút Ghim cho " + term)
        button.click(timeout=5000)
        # Re-check that the button changed state, rather than reporting blind success.
        unpin = candidate.get_by_role("button", name=re.compile(r"^(unpin|bỏ ghim|bỏ ghim sản phẩm)$", re.I))
        unpin.wait_for(state="visible", timeout=5000)
        return term

    def _run(self):
        next_pin = None
        while True:
            timeout = max(0, next_pin - time.monotonic()) if next_pin is not None else None
            try:
                method, args, future = self._queue.get(timeout=timeout)
            except queue.Empty:
                method, args, future = "tick", (), None
            try:
                if method == "open":
                    result = self._open_on_worker(*args)
                    if not self._items:
                        try:
                            self._scan_on_worker()  # first connection: take the products from the page
                            result = self.status()
                        except Exception as exc:  # noqa: BLE001 - the operator can scan again later
                            log.info("Chưa đọc được sản phẩm từ LIVE Console: %s", exc)
                elif method == "scan":
                    result = self._scan_on_worker()
                elif method == "start":
                    if self._context is None:
                        self._open_on_worker(self._url)  # step 2 after the operator closed the login Chrome
                    if not self._items:
                        self._scan_on_worker()  # no list yet: pin every product of the page
                    self._enabled = True
                    self._state = "running"
                    next_pin = time.monotonic()
                    result = self.status()
                elif method == "stop":
                    self._enabled = False
                    self._state = "stopped"
                    next_pin = None
                    result = self.status()
                elif method == "disconnect":
                    self._enabled = False
                    next_pin = None
                    self._drop_context()
                    result = {"ok": True}
                elif method == "tick":
                    if not self._enabled or not self._items:
                        next_pin = None
                        continue
                    term = self._items[self._index]
                    try:
                        self._last_pin = self._pin(term)
                        self._last_error = ""
                        self._index = (self._index + 1) % len(self._items)
                        self._state = "running"
                        next_pin = time.monotonic() + self._seconds
                    except LoginRequired as exc:
                        self._last_error = str(exc)
                        self._state = "login_required"
                        self._enabled = False
                        next_pin = None
                        log.warning("Auto Pin dừng: TikTok chuyển sang trang đăng nhập / xác minh")
                    except Exception as exc:  # noqa: BLE001
                        self._last_error = str(exc)[:300]
                        self._state = "attention"
                        self._enabled = False
                        next_pin = None
                        log.warning("Auto Pin dừng: %s", exc)
                    continue
                elif method == "close":
                    self._enabled = False
                    self._drop_context()
                    result = {"ok": True}
                else:
                    raise ValueError("Unknown Auto Pin operation")
                if future:
                    future.set_result(result)
                if method == "close":
                    return
            except Exception as exc:  # noqa: BLE001
                if future:
                    future.set_exception(exc)

    def start(self):
        return self._request("start")

    def stop(self):
        return self._request("stop")

    def close(self):
        if self._thread and self._thread.is_alive():
            return self._request("close")
        return {"ok": True}
