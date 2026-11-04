"""Auto solver for TikTok LIVE Studio CAPTCHA.

Uses:
  - CaptchaMonitor detection result
  - SadCaptchaAnalyzer (already in project)
  - win32 mouse control (no extra dependency)

Called automatically from CaptchaMonitor when a challenge is confirmed.
"""
from __future__ import annotations

import base64
import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable

from sarae_agent.errors import AgentError
from sarae_agent.monitoring.sadcaptcha import SadCaptchaAnalyzer
from sarae_agent.monitoring.studio import find_studio_window
from sarae_agent.windows import win32

log = logging.getLogger("sarae.solver")

MAX_ATTEMPTS_PER_CHALLENGE = 3


@dataclass
class SolveResult:
    success: bool
    challenge_type: str | None = None
    detail: str = ""
    error: str | None = None
    attempts: int = 0


def _b64_png(cv2, image) -> str:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise AgentError("SOLVER_ENCODE_FAILED", "Không encode được ảnh captcha")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _human_move(x: int, y: int, duration: float | None = None) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    current = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(current))

    duration = duration or random.uniform(0.35, 0.85)
    steps = max(10, int(duration * 55))
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3.0 - 2.0 * t)  # smoothstep
        nx = int(current.x + (x - current.x) * t)
        ny = int(current.y + (y - current.y) * t)
        user32.SetCursorPos(nx, ny)
        time.sleep(duration / steps)


def _click(x: int, y: int) -> None:
    import ctypes
    user32 = ctypes.windll.user32
    _human_move(x, y)
    time.sleep(random.uniform(0.06, 0.16))
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(random.uniform(0.04, 0.11))
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP


def _drag(start_x: int, start_y: int, end_x: int, end_y: int) -> None:
    import ctypes
    user32 = ctypes.windll.user32
    _human_move(start_x, start_y, duration=random.uniform(0.28, 0.55))
    time.sleep(0.12)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(random.uniform(0.22, 0.4))

    overshoot = random.randint(10, 20)
    _human_move(end_x + overshoot, end_y + random.randint(-5, 7),
                duration=random.uniform(0.75, 1.25))
    time.sleep(random.uniform(0.07, 0.18))
    _human_move(end_x, end_y, duration=random.uniform(0.22, 0.45))
    time.sleep(random.uniform(0.07, 0.15))
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP


class CaptchaSolver:
    """One-shot auto solver. Safe to call from the monitor thread."""

    def __init__(
        self,
        analyzer: SadCaptchaAnalyzer,
        emit: Callable[[str, dict], None],
        notify_owner: Callable[[str], None] | None = None,
    ):
        self._analyzer = analyzer
        self._emit = emit
        self._notify_owner = notify_owner
        self._lock = threading.Lock()
        self._solving = False
        self._fail_streak = 0  # consecutive failed challenges

    def try_solve(self, challenge_type: str | None, image_path: str | None = None) -> SolveResult:
        """Called by CaptchaMonitor after a confirmed detection."""
        with self._lock:
            if self._solving:
                return SolveResult(False, error="Đang giải captcha khác")
            self._solving = True

        try:
            result = self._solve_once(challenge_type)
            if result.success:
                self._fail_streak = 0
                self._emit("monitor.captcha_solved", {
                    "challenge_type": result.challenge_type,
                    "detail": result.detail,
                    "attempts": result.attempts,
                })
            else:
                self._fail_streak += 1
                self._emit("monitor.captcha_solve_failed", {
                    "error": result.error,
                    "attempts": result.attempts,
                    "fail_streak": self._fail_streak,
                })
                if self._fail_streak >= MAX_ATTEMPTS_PER_CHALLENGE:
                    msg = (f"⚠️ Không giải được CAPTCHA sau {self._fail_streak} lần.\n"
                           f"Lỗi cuối: {result.error or 'unknown'}")
                    self._notify(msg)
            return result
        finally:
            with self._lock:
                self._solving = False

    def _notify(self, text: str) -> None:
        log.warning("Owner notify: %s", text)
        if self._notify_owner:
            try:
                self._notify_owner(text)
            except Exception:
                log.exception("Gửi thông báo owner thất bại")

    def _solve_once(self, challenge_type: str | None) -> SolveResult:
        try:
            from sarae_agent.monitoring.captcha import load_cv
            cv2, np, mss = load_cv()

            target = find_studio_window()
            if not target or target.get("minimized"):
                return SolveResult(False, error="Không tìm thấy cửa sổ LIVE Studio")

            rect = win32.window_rect(target["hwnd"])
            if rect is None:
                return SolveResult(False, error="Không lấy được tọa độ cửa sổ")

            left, top, right, bottom = rect
            with (getattr(mss, "MSS", None) or mss.mss)() as sct:
                bounds = {"left": left, "top": top, "width": right - left, "height": bottom - top}
                shot = np.array(sct.grab(bounds))
                bgr = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)

            h, w = bgr.shape[:2]
            # Captcha dialog thường nằm giữa cửa sổ Studio
            cx1, cy1 = int(w * 0.25), int(h * 0.22)
            cx2, cy2 = int(w * 0.75), int(h * 0.82)
            roi = bgr[cy1:cy2, cx1:cx2]
            offset_x, offset_y = left + cx1, top + cy1

            # Ưu tiên loại monitor báo, nếu không có thì thử puzzle trước
            kinds = []
            if challenge_type in ("PUZZLE", "puzzle"):
                kinds = ["puzzle"]
            elif challenge_type in ("SHAPES", "shapes"):
                kinds = ["shapes"]
            else:
                kinds = ["puzzle", "shapes"]

            last_error = None
            for kind in kinds:
                try:
                    if kind == "puzzle":
                        return self._do_puzzle(cv2, roi, offset_x, offset_y)
                    else:
                        return self._do_shapes(cv2, roi, offset_x, offset_y)
                except AgentError as exc:
                    last_error = exc.message
                    log.warning("Thử %s thất bại: %s", kind, exc.message)
                    continue

            return SolveResult(False, error=last_error or "Không giải được")

        except AgentError as exc:
            return SolveResult(False, error=exc.message)
        except Exception as exc:
            log.exception("Solver lỗi không mong muốn")
            return SolveResult(False, error=str(exc)[:200])

    def _do_puzzle(self, cv2, roi, offset_x: int, offset_y: int) -> SolveResult:
        h, w = roi.shape[:2]
        # Ước lượng vùng canvas + piece (cần tinh chỉnh sau khi có ảnh thật)
        canvas = roi[int(h * 0.12):int(h * 0.68), int(w * 0.06):int(w * 0.70)]
        piece  = roi[int(h * 0.12):int(h * 0.68), int(w * 0.70):int(w * 0.96)]

        if min(canvas.shape[:2]) < 40 or min(piece.shape[:2]) < 18:
            raise AgentError("SOLVER_CROP_FAILED", "Crop puzzle không đủ lớn")

        puzzle_b64 = _b64_png(cv2, canvas)
        piece_b64  = _b64_png(cv2, piece)

        analysis = self._analyzer.analyze("puzzle", puzzle_b64, piece_b64)
        proportion = analysis["result"]["slideXProportion"]
        distance = int(proportion * canvas.shape[1])

        # Thanh trượt thường nằm dưới canvas
        slider_y = offset_y + int(h * 0.76)
        start_x  = offset_x + int(w * 0.16)
        end_x    = start_x + distance

        log.info("Puzzle: kéo %d px (%.3f)", distance, proportion)
        _drag(start_x, slider_y, end_x, slider_y)
        time.sleep(1.4)
        return SolveResult(True, challenge_type="puzzle",
                           detail=f"kéo {distance}px", attempts=1)

    def _do_shapes(self, cv2, roi, offset_x: int, offset_y: int) -> SolveResult:
        img_b64 = _b64_png(cv2, roi)
        analysis = self._analyzer.analyze("shapes", img_b64)
        r = analysis["result"]

        h, w = roi.shape[:2]
        p1x = offset_x + int(w * r["pointOneProportionX"])
        p1y = offset_y + int(h * r["pointOneProportionY"])
        p2x = offset_x + int(w * r["pointTwoProportionX"])
        p2y = offset_y + int(h * r["pointTwoProportionY"])

        log.info("Shapes: click (%.2f,%.2f) → (%.2f,%.2f)",
                 r["pointOneProportionX"], r["pointOneProportionY"],
                 r["pointTwoProportionX"], r["pointTwoProportionY"])

        _click(p1x, p1y)
        time.sleep(random.uniform(0.28, 0.55))
        _click(p2x, p2y)
        time.sleep(random.uniform(0.3, 0.55))

        # Nút Confirm gần đáy dialog
        confirm_x = offset_x + w // 2
        confirm_y = offset_y + int(h * 0.90)
        _click(confirm_x, confirm_y)

        return SolveResult(True, challenge_type="shapes",
                           detail="đã click 2 điểm + Confirm", attempts=1)