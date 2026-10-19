"""CAPTCHA presence monitor for TikTok LIVE Studio.

Captures only the Studio window rectangle, runs scale-tolerant edge template matching and
raises an alert after two consecutive positive frames. It never clicks, solves or submits.
Screen capture handles (mss) live only inside the worker thread and are closed on stop.

Reference images come from Sarae (the platform's SUPER_ADMIN) through the Cloud: sync_templates() mirrors that
set exactly. Operators and OWNERs neither add nor see them.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable

from sarae_agent.config import ConfigStore, MonitorConfig, paths
from sarae_agent.errors import AgentError
from sarae_agent.monitoring.studio import find_studio_window, studio_process_running
from sarae_agent.windows import win32

log = logging.getLogger("sarae.monitor")

TEMPLATE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_TEMPLATE_BYTES = 3_000_000
CONFIRM_FRAMES = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_cv():
    if not win32.IS_WINDOWS:
        raise AgentError("MONITOR_UNSUPPORTED", "CAPTCHA Monitor cần Windows Desktop")
    try:
        import cv2
        import mss
        import numpy
    except ImportError as exc:
        raise AgentError("MONITOR_DEPENDENCY_MISSING", f"Thiếu thư viện giám sát: {exc.name}") from exc
    return cv2, numpy, mss


def match_reference(cv2, np, frame_gray, reference_gray) -> tuple[float, float]:
    """Best normalised edge-correlation score over a small scale range."""
    screen_edges = cv2.Canny(frame_gray, 70, 160)
    reference_edges = cv2.Canny(reference_gray, 70, 160)
    if cv2.countNonZero(reference_edges) < 25:
        raise ValueError("Ảnh mẫu thiếu chi tiết cạnh")
    best_score, best_scale = -1.0, 1.0
    for scale in (0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2):
        w = max(1, round(reference_gray.shape[1] * scale))
        h = max(1, round(reference_gray.shape[0] * scale))
        if w > frame_gray.shape[1] or h > frame_gray.shape[0]:
            continue
        template = cv2.resize(reference_edges, (w, h), interpolation=cv2.INTER_LINEAR)
        if cv2.countNonZero(template) < 20:
            continue
        score = float(cv2.matchTemplate(screen_edges, template, cv2.TM_CCOEFF_NORMED).max())
        if np.isfinite(score) and score > best_score:
            best_score, best_scale = score, scale
    if best_score < 0:
        raise ValueError("Không thể so khớp ảnh mẫu với màn hình")
    return best_score, best_scale


class CaptchaMonitor:
    def __init__(self, config: ConfigStore, emit: Callable[[str, dict], None]):
        self._config = config
        self._emit = emit
        self._guard = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.events: deque[dict] = deque(maxlen=100)
        self.state: dict = {"running": False, "status": "idle", "window_found": False, "window_visible": False,
                            "window_title": "", "window_info": None, "process_running": None,
                            "last_scan": None, "error": None, "events_count": 0, "match_score": None,
                            "matched_template": None, "monitors_detected": None, "capture_bounds": None}
        self._detected = False

    # --- templates (provided by Sarae through the Cloud) ---------------------------------
    def templates(self) -> list[dict]:
        return [{"name": p.name, "bytes": p.stat().st_size}
                for p in sorted(paths.monitor_templates.glob("*.png"))]

    @staticmethod
    def _normalize(raw: bytes) -> bytes:
        """Decode and check one reference image; returns it re-encoded as PNG."""
        if len(raw) > MAX_TEMPLATE_BYTES:
            raise AgentError("INVALID_TEMPLATE", "Ảnh mẫu tối đa 3 MB")
        cv2, np, _ = load_cv()
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or min(image.shape[:2]) < 20 or max(image.shape[:2]) > 1800:
            raise AgentError("INVALID_TEMPLATE", "Ảnh phải hợp lệ, cạnh từ 20 đến 1800 px")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if cv2.countNonZero(cv2.Canny(gray, 70, 160)) < 25:
            raise AgentError("INVALID_TEMPLATE", "Ảnh mẫu thiếu chi tiết")
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise AgentError("INVALID_TEMPLATE", "Không lưu được ảnh mẫu")
        return encoded.tobytes()

    def sync_templates(self, remote: list[dict], fetch: Callable[[str], bytes]) -> dict:
        """Mirror the Cloud set ``[{id, sha256}]``: download the new ones, delete every other image.

        A download whose sha256 differs from the listing, or that does not decode, is skipped and retried on the
        next sync. Returns {added, removed, failed, total}.
        """
        wanted = {f"sarae_{t['id']}.png": t for t in remote
                  if isinstance(t, dict) and TEMPLATE_ID_RE.fullmatch(str(t.get("id", "")))}
        folder = paths.monitor_templates
        existing = {p.name for p in folder.glob("*.png")}
        added = failed = 0
        for name, item in wanted.items():
            if name in existing:
                continue
            try:
                raw = fetch(item["id"])
                if hashlib.sha256(raw).hexdigest() != item.get("sha256"):
                    raise AgentError("INVALID_TEMPLATE", "Ảnh mẫu tải về không khớp mã kiểm tra")
                temp = folder / (name + ".tmp")
                temp.write_bytes(self._normalize(raw))
                temp.replace(folder / name)
                added += 1
            except Exception as exc:  # noqa: BLE001 - one bad image must not block the others
                failed += 1
                log.warning("Không tải được ảnh mẫu CAPTCHA %s: %s", item["id"][:8], exc,
                            extra={"code": getattr(exc, "code", "TEMPLATE_DOWNLOAD_FAILED")})
        removed = 0
        for name in existing - set(wanted):
            try:
                (folder / name).unlink()
                removed += 1
            except OSError:
                pass
        if added or removed:
            log.info("Đã cập nhật ảnh mẫu CAPTCHA từ Sarae: thêm %s, bỏ %s", added, removed)
            self._status_changed()
        return {"added": added, "removed": removed, "failed": failed, "total": len(self.templates())}

    # --- config --------------------------------------------------------------------------
    def configure(self, values: dict) -> dict:
        allowed = {k: v for k, v in values.items() if k in MonitorConfig.model_fields}
        updated = self._config.update({"monitor": allowed})
        self._status_changed()
        return updated.monitor.model_dump()

    # --- lifecycle -----------------------------------------------------------------------
    def start(self) -> dict:
        with self._guard:
            if self._thread and self._thread.is_alive():
                return self.status()
            load_cv()
            if not self.templates():
                raise AgentError("TEMPLATE_MISSING", "Giám sát CAPTCHA chưa sẵn sàng: máy chưa nhận dữ liệu nhận diện từ Sarae (cần Kết nối Cloud)")
            self._stop = threading.Event()
            self._detected = False
            self.state.update(running=True, status="starting", error=None)
            self._thread = threading.Thread(target=self._loop, args=(self._stop,), name="captcha-monitor",
                                            daemon=True)
            self._thread.start()
        self._event("monitor_started", "Đã bắt đầu giám sát")
        self._status_changed()
        return self.status()

    def stop(self, *, wait: float = 5.0) -> dict:
        with self._guard:
            thread = self._thread
            self._stop.set()
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=wait)
        with self._guard:
            was_running = self.state["running"]
            self.state.update(running=False, status="stopped" if was_running or thread else self.state["status"])
        if thread:
            self._event("monitor_stopped", "Đã dừng giám sát")
        self._status_changed()
        return self.status()

    def status(self) -> dict:
        with self._guard:
            return {"state": dict(self.state), "config": self._config.get().monitor.model_dump(),
                    "events": list(reversed(self.events))[:30], "templates": len(self.templates())}

    def heartbeat_view(self) -> dict:
        data = self.status()
        data["events"] = [{k: e[k] for k in ("at", "kind", "detail")} for e in data["events"][:10]]
        return data

    # --- internals -----------------------------------------------------------------------
    def _event(self, kind: str, detail: str, image: str | None = None) -> dict:
        event = {"id": int(time.time() * 1_000_000), "at": _now(), "kind": kind, "detail": detail, "image": image}
        with self._guard:
            self.events.append(event)
            self.state["events_count"] += 1
        return event

    def _status_changed(self) -> None:
        self._emit("monitor.status_changed", self.status())

    def _set(self, **changes) -> None:
        with self._guard:
            before = self.state.get("status")
            self.state.update(changes)
            changed = before != self.state.get("status")
        if changed:
            self._status_changed()

    def _loop(self, stop: threading.Event) -> None:
        positive = negative = 0
        capture = None
        try:
            cv2, np, mss = load_cv()
            capture = (getattr(mss, "MSS", None) or mss.mss)()
            while not stop.is_set():
                interval = self._config.get().monitor.interval_seconds
                threshold = self._config.get().monitor.threshold
                try:
                    target = find_studio_window()
                    self._set(window_found=target is not None, window_title=target["title"] if target else "",
                              window_visible=bool(target and not target["minimized"]), window_info=target,
                              last_scan=_now(), error=None)
                    if not target or target["minimized"]:
                        positive = negative = 0
                        self._set(status="window_unavailable", process_running=studio_process_running())
                        stop.wait(interval)
                        continue
                    rect = win32.window_rect(target["hwnd"])
                    if rect is None:
                        stop.wait(interval)
                        continue
                    left, top, right, bottom = rect
                    virtual = capture.monitors[0]
                    vx, vy = int(virtual["left"]), int(virtual["top"])
                    left, top = max(left, vx), max(top, vy)
                    right = min(right, vx + int(virtual["width"]))
                    bottom = min(bottom, vy + int(virtual["height"]))
                    if right - left < 120 or bottom - top < 100:
                        raise ValueError("Cửa sổ TikTok nằm ngoài vùng màn hình đang hiển thị")
                    bounds = {"left": left, "top": top, "width": right - left, "height": bottom - top}
                    shot = np.array(capture.grab(bounds))
                    self._set(capture_bounds=bounds, monitors_detected=len(capture.monitors) - 1,
                              process_running=True)
                    gray = cv2.cvtColor(shot, cv2.COLOR_BGRA2GRAY)
                    scores = []
                    for candidate in paths.monitor_templates.glob("*.png"):
                        reference = cv2.imread(str(candidate), cv2.IMREAD_GRAYSCALE)
                        if reference is None or reference.shape[0] > gray.shape[0] or reference.shape[1] > gray.shape[1]:
                            continue
                        try:
                            value, scale = match_reference(cv2, np, gray, reference)
                            scores.append((value, scale, candidate.name))
                        except ValueError:
                            continue
                    if not scores:
                        self._set(status="template_missing_or_invalid")
                        stop.wait(interval)
                        continue
                    score, scale, matched = max(scores, key=lambda item: item[0])
                    hit = score >= threshold
                    positive = positive + 1 if hit else 0
                    negative = negative + 1 if not hit else 0
                    self._set(match_score=round(score, 3), match_scale=scale, matched_template=matched,
                              status="captcha_detected" if self._detected else "watching")
                    if not self._detected and positive >= CONFIRM_FRAMES:
                        self._detected = True
                        image_name = "detected_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + ".png"
                        cv2.imwrite(str(paths.monitor_captures / image_name), shot)
                        self._prune_captures()
                        event = self._event("captcha_detected", f"Khớp mẫu CAPTCHA, điểm {round(score, 3)}",
                                            image_name)
                        self._set(status="captcha_detected")
                        log.warning("Phát hiện CAPTCHA trên TikTok LIVE Studio (điểm %.3f)", score,
                                    extra={"code": "CAPTCHA_DETECTED"})
                        self._emit("monitor.captcha_detected", {
                            "at": event["at"], "score": round(score, 3), "template": matched,
                            "image_path": str(paths.monitor_captures / image_name),
                            "window_title": target["title"]})
                    elif self._detected and negative >= CONFIRM_FRAMES:
                        self._detected = False
                        event = self._event("captcha_cleared",
                                            "Không còn thấy mẫu CAPTCHA; chưa xác minh đã giải thành công.")
                        self._set(status="watching")
                        self._emit("monitor.captcha_cleared", {"at": event["at"]})
                except AgentError:
                    raise
                except Exception as exc:  # noqa: BLE001 - keep watching after transient capture errors
                    self._set(status="error", error=str(exc)[:250])
                stop.wait(interval)
        except AgentError as exc:
            self._set(status="error", error=exc.message, running=False)
            self._event("monitor_error", exc.message)
        except Exception as exc:  # noqa: BLE001
            log.exception("CAPTCHA Monitor dừng do lỗi")
            self._set(status="error", error=str(exc)[:250], running=False)
            self._event("monitor_error", str(exc)[:250])
        finally:
            if capture is not None:
                try:
                    capture.close()
                except Exception:  # noqa: BLE001
                    pass
            with self._guard:
                self.state["running"] = False
            self._status_changed()

    @staticmethod
    def _prune_captures(keep: int = 50) -> None:
        files = sorted(paths.monitor_captures.glob("detected_*.png"))
        for old in files[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
