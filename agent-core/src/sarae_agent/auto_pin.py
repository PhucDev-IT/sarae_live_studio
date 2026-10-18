"""Auto-pin TikTok LIVE Console in a dedicated Playwright-managed Chrome profile.

This module never handles CAPTCHA challenges or extracts credentials. The operator
signs into LIVE Console in the visible Chrome window. DOM selectors may require
updates when the console UI changes.
"""
from __future__ import annotations
import logging
import re
import threading
import queue
from concurrent.futures import Future
from pathlib import Path

log=logging.getLogger("sarae.autopin")
LIVE_ORIGINS={"shop.tiktok.com","livecenter.tiktok.com","seller-vn.tiktok.com","seller.tiktokglobalshop.com","seller-us.tiktok.com"}
class AutoPinService:
    def __init__(self, profile_dir:Path):
        self.profile_dir=profile_dir
        self._lock=threading.RLock()
        self._thread=None
        self._queue=queue.Queue()
        self._context=None
        self._playwright=None
        self._page=None
        self._items:list[str]=[]
        self._index=0
        self._seconds=30
        self._enabled=False
        self._last_error=""
        self._last_pin=""
        self._state="idle"

    def status(self):
        with self._lock:
            return {"state":self._state,"enabled":self._enabled,"interval_seconds":self._seconds,
                    "products":list(self._items),"current_index":self._index,
                    "last_pin":self._last_pin,"last_error":self._last_error,
                    "browser_open":self._context is not None}

    def _ensure_worker(self):
        if self._thread and self._thread.is_alive():return
        self._thread=threading.Thread(target=self._run,name="auto-pin-browser",daemon=True)
        self._thread.start()

    def _request(self,method:str,*args):
        with self._lock:
            self._ensure_worker()
        result=Future()
        self._queue.put((method,args,result))
        return result.result(timeout=45)

    def open_browser(self,url:str):
        from urllib.parse import urlsplit
        u=urlsplit(url)
        if u.scheme!="https" or u.hostname not in LIVE_ORIGINS:
            raise ValueError("Chỉ mở TikTok LIVE Console trên tên miền được hỗ trợ")
        return self._request("open",url)

    def _open_on_worker(self,url):
        if self._context is not None:
            try:
                if self._page is None or self._page.is_closed():
                    pages=[p for p in self._context.pages if not p.is_closed()]
                    self._page=pages[0] if pages else self._context.new_page()
                self._page.goto(url,wait_until="domcontentloaded",timeout=30000)
                return self.status()
            except Exception:
                # A closed Chrome context cannot be reused.
                try:self._context.close()
                except Exception:pass
                try:self._playwright.stop()
                except Exception:pass
                self._context=None;self._playwright=None;self._page=None
                raise RuntimeError("Chrome đã đóng hoặc trang bị ngắt khi điều hướng; hãy mở lại Chrome từ Sarae Agent")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Thiếu playwright trong Agent Core") from exc
        self.profile_dir.mkdir(parents=True,exist_ok=True)
        self._playwright=sync_playwright().start()
        try:
            self._context=self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),channel="chrome",headless=False,
                accept_downloads=False,viewport=None)
            self._page=self._context.pages[0] if self._context.pages else self._context.new_page()
            self._page.goto(url,wait_until="domcontentloaded",timeout=30000)
            self._state="ready"
        except Exception as exc:
            if self._context:
                try:self._context.close()
                except Exception:pass
            try:self._playwright.stop()
            except Exception:pass
            self._playwright=None;self._context=None;self._page=None
            self._last_error=str(exc)[:300]
            raise
        return self.status()

    def configure(self,products:list[str],interval_seconds:int):
        if not 5<=interval_seconds<=3600:raise ValueError("Chu kỳ phải từ 5 đến 3600 giây")
        cleaned=[v.strip() for v in products if v.strip()]
        if not cleaned or len(cleaned)>100:raise ValueError("Cần 1–100 mã hoặc tên sản phẩm")
        if len(set(cleaned))!=len(cleaned):raise ValueError("Danh sách sản phẩm trùng nhau")
        with self._lock:
            self._items=cleaned
            self._seconds=interval_seconds
            self._index=0
        return self.status()

    def _pin(self,term:str):
        page=self._page
        if page is None:raise RuntimeError("Chưa mở Chrome")
        if page.url.startswith("chrome://"):raise RuntimeError("Chưa mở LIVE Console")
        from urllib.parse import urlsplit
        if urlsplit(page.url).hostname not in LIVE_ORIGINS:
            raise RuntimeError("Trang hiện tại không phải TikTok LIVE Console")
        # Target a product row by its visible product name/ID and click its Pin button.
        # Avoid guessing coordinates and avoid interacting with challenge dialogs.
        row=page.get_by_text(term,exact=True).first
        if row.count()==0:raise RuntimeError("Không tìm thấy sản phẩm: "+term)
        candidate=row.locator("xpath=ancestor::*[.//button][1]")
        button=candidate.get_by_role("button",name=re.compile(r"^(pin|ghim)$",re.I))
        if button.count()!=1:
            raise RuntimeError("Không xác định được duy nhất nút Ghim cho "+term)
        button.click(timeout=5000)
        # Re-check that the button changed state, rather than reporting blind success.
        unpin=candidate.get_by_role("button",name=re.compile(r"^(unpin|bỏ ghim|bỏ ghim sản phẩm)$",re.I))
        unpin.wait_for(state="visible",timeout=5000)
        return term

    def _run(self):
        import time
        next_pin=None
        while True:
            timeout=max(0,next_pin-time.monotonic()) if next_pin is not None else None
            try:
                method,args,future=self._queue.get(timeout=timeout)
            except queue.Empty:
                method="tick";args=();future=None
            try:
                if method=="open":
                    result=self._open_on_worker(*args)
                elif method=="start":
                    if not self._items:raise RuntimeError("Chưa có danh sách sản phẩm")
                    if self._context is None:raise RuntimeError("Chưa mở Chrome LIVE Console")
                    self._enabled=True;self._state="running"
                    next_pin=time.monotonic()
                    result=self.status()
                elif method=="stop":
                    self._enabled=False;self._state="stopped"
                    next_pin=None
                    result=self.status()
                elif method=="tick":
                    if not self._enabled or not self._items:
                        next_pin=None
                        continue
                    term=self._items[self._index]
                    try:
                        self._last_pin=self._pin(term)
                        self._last_error=""
                        self._index=(self._index+1)%len(self._items)
                        self._state="running"
                        next_pin=time.monotonic()+self._seconds
                    except Exception as exc:
                        self._last_error=str(exc)[:300]
                        self._state="attention";self._enabled=False
                        next_pin=None
                        log.warning("Auto Pin dừng: %s",exc)
                    continue
                elif method=="close":
                    self._enabled=False
                    if self._context:self._context.close()
                    if self._playwright:self._playwright.stop()
                    self._context=None;self._page=None;self._playwright=None
                    result={"ok":True}
                else:raise ValueError("Unknown Auto Pin operation")
                if future:future.set_result(result)
                if method=="close":return
            except Exception as exc:
                if future:future.set_exception(exc)

    def start(self):
        return self._request("start")

    def stop(self):
        return self._request("stop")

    def close(self):
        if self._thread and self._thread.is_alive():
            return self._request("close")
        return {"ok":True}
