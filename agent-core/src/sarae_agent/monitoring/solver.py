"""Auto solver for TikTok LIVE Studio CAPTCHA (SadCaptcha).

Cải tiến so với bản trước:
- Extract puzzle canvas + piece đúng chuẩn SadCaptcha (che piece trước khi gửi).
- Ưu tiên dùng ảnh monitor đã capture, fallback chụp cửa sổ.
- ROI + tỷ lệ crop ổn định hơn với layout Live Studio.
- Human-like mouse (win32), retry trong 1 challenge, notify khi fail ≥ 3 lần.
"""
from __future__ import annotations

import base64
import logging
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sarae_agent.errors import AgentError
from sarae_agent.monitoring.sadcaptcha import SadCaptchaAnalyzer
from sarae_agent.monitoring.studio import find_studio_window
from sarae_agent.windows import win32

log = logging.getLogger("sarae.solver")

MAX_ATTEMPTS_PER_CHALLENGE = 3
MAX_RETRIES_ONCE = 2          # retry trong 1 lần gọi try_solve
DEBUG_SAVE_CROPS = False      # bật True khi cần lưu ảnh crop để debug


@dataclass
class SolveResult:
    success: bool
    challenge_type: str | None = None
    detail: str = ""
    error: str | None = None
    attempts: int = 0


# ---------------------------------------------------------------------------
# Mouse helpers (win32, no pyautogui)
# ---------------------------------------------------------------------------

def _human_move(x: int, y: int, duration: float | None = None) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    current = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(current))

    duration = duration or random.uniform(0.32, 0.78)
    steps = max(12, int(duration * 60))
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
    # jitter nhỏ cho tự nhiên
    x += random.randint(-2, 2)
    y += random.randint(-2, 2)
    _human_move(x, y)
    time.sleep(random.uniform(0.05, 0.14))
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(random.uniform(0.04, 0.10))
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP


def _drag(start_x: int, start_y: int, end_x: int, end_y: int) -> None:
    import ctypes
    user32 = ctypes.windll.user32
    _human_move(start_x, start_y, duration=random.uniform(0.25, 0.50))
    time.sleep(0.10)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(random.uniform(0.18, 0.35))

    overshoot = random.randint(8, 18)
    _human_move(
        end_x + overshoot,
        end_y + random.randint(-4, 6),
        duration=random.uniform(0.70, 1.15),
    )
    time.sleep(random.uniform(0.06, 0.15))
    _human_move(end_x, end_y, duration=random.uniform(0.18, 0.38))
    time.sleep(random.uniform(0.06, 0.12))
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _b64_png(cv2, image) -> str:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise AgentError("SOLVER_ENCODE_FAILED", "Không encode được ảnh captcha")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _save_debug(cv2, image, name: str) -> None:
    if not DEBUG_SAVE_CROPS:
        return
    try:
        from sarae_agent.config import paths
        out = paths.monitor_captures / f"solver_debug_{name}.png"
        cv2.imwrite(str(out), image)
        log.debug("Debug crop saved: %s", out)
    except Exception:
        pass


def _load_image_from_path(cv2, np, path: str | Path):
    """Load BGR image from file path (monitor capture)."""
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise AgentError("SOLVER_IMAGE_LOAD", f"Không đọc được ảnh: {path}")
    return img


# ---------------------------------------------------------------------------
# CaptchaSolver
# ---------------------------------------------------------------------------

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
        self._fail_streak = 0

    def try_solve(
        self,
        challenge_type: str | None,
        image_path: str | None = None,
    ) -> SolveResult:
        """Called by CaptchaMonitor after a confirmed detection."""
        with self._lock:
            if self._solving:
                return SolveResult(False, error="Đang giải captcha khác")
            self._solving = True

        try:
            result = self._solve_with_retries(challenge_type, image_path)
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
                    msg = (
                        f"⚠️ Không giải được CAPTCHA sau {self._fail_streak} lần.\n"
                        f"Lỗi cuối: {result.error or 'unknown'}"
                    )
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

    def _solve_with_retries(
        self,
        challenge_type: str | None,
        image_path: str | None,
    ) -> SolveResult:
        last: SolveResult | None = None
        for attempt in range(1, MAX_RETRIES_ONCE + 1):
            last = self._solve_once(challenge_type, image_path, attempt)
            if last.success:
                last.attempts = attempt
                return last
            log.warning("Solve attempt %d failed: %s", attempt, last.error)
            time.sleep(0.6 + random.random() * 0.5)
        assert last is not None
        last.attempts = MAX_RETRIES_ONCE
        return last

    def _solve_once(
        self,
        challenge_type: str | None,
        image_path: str | None,
        attempt: int,
    ) -> SolveResult:
        try:
            from sarae_agent.monitoring.captcha import load_cv
            cv2, np, mss = load_cv()

            # 1. Lấy ảnh nguồn
            bgr, offset_x, offset_y = self._acquire_frame(cv2, np, mss, image_path)
            h, w = bgr.shape[:2]

            # Nếu ảnh đến từ monitor capture của challenge template
            # → bản thân nó đã là dialog → dùng nguyên ảnh
            # Nếu ảnh là full cửa sổ Studio → cắt ROI giữa
            if image_path and Path(image_path).is_file():
                # Ảnh đã crop sẵn (template-sized) → dùng nguyên
                roi = bgr
                abs_offset_x, abs_offset_y = offset_x, offset_y
            else:
                # Full window screenshot → cắt vùng giữa
                cx1, cy1 = int(w * 0.22), int(h * 0.18)
                cx2, cy2 = int(w * 0.78), int(h * 0.85)
                roi = bgr[cy1:cy2, cx1:cx2]
                abs_offset_x = offset_x + cx1
                abs_offset_y = offset_y + cy1

            if min(roi.shape[:2]) < 80:
                return SolveResult(False, error="ROI captcha quá nhỏ")

            _save_debug(cv2, roi, f"roi_attempt{attempt}")

            # 2. Quyết định loại challenge
            kinds: list[str] = []
            ct = (challenge_type or "").lower()
            if ct in ("puzzle", "puzzle_slide"):
                kinds = ["puzzle"]
            elif ct in ("shapes", "shape"):
                kinds = ["shapes"]
            else:
                # Monitor báo INACTIVITY / CAPTCHA_CHALLENGE chung → thử cả hai
                kinds = ["puzzle", "shapes"]

            last_error = None
            for kind in kinds:
                try:
                    if kind == "puzzle":
                        return self._do_puzzle(cv2, roi, abs_offset_x, abs_offset_y)
                    else:
                        return self._do_shapes(cv2, roi, abs_offset_x, abs_offset_y)
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

    def _acquire_frame(self, cv2, np, mss, image_path: str | None):
        """Trả về (bgr, offset_x, offset_y) trong tọa độ màn hình."""
        # Ưu tiên ảnh monitor đã capture (đồng bộ với detection)
        if image_path:
            p = Path(image_path)
            if p.is_file():
                bgr = _load_image_from_path(cv2, np, p)
                # Ảnh monitor thường là crop cửa sổ Studio → offset = vị trí cửa sổ
                target = find_studio_window()
                if target and not target.get("minimized"):
                    rect = win32.window_rect(target["hwnd"])
                    if rect:
                        left, top, _, _ = rect
                        return bgr, left, top
                # Không lấy được cửa sổ → coi offset = 0 (ảnh đã là full screen)
                return bgr, 0, 0

        # Fallback: chụp cửa sổ Studio hiện tại
        target = find_studio_window()
        if not target or target.get("minimized"):
            raise AgentError("SOLVER_NO_WINDOW", "Không tìm thấy cửa sổ LIVE Studio")

        rect = win32.window_rect(target["hwnd"])
        if rect is None:
            raise AgentError("SOLVER_NO_RECT", "Không lấy được tọa độ cửa sổ")

        left, top, right, bottom = rect
        with (getattr(mss, "MSS", None) or mss.mss)() as sct:
            bounds = {
                "left": left,
                "top": top,
                "width": right - left,
                "height": bottom - top,
            }
            shot = np.array(sct.grab(bounds))
            bgr = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
        return bgr, left, top

    # ------------------------------------------------------------------
# Puzzle  (dựa trên puzzle_challenge_large.png)
# ------------------------------------------------------------------

def _do_puzzle(self, cv2, roi, offset_x: int, offset_y: int) -> SolveResult:
    """
    Layout mẫu Live Studio puzzle:
    - Phần trên ~75% chiều cao = canvas (cảnh + silhouette mảnh)
    - Phần dưới ~25% = thanh slider + nút mũi tên
    - Piece nằm ở bên trái canvas (~24% chiều rộng)
    """
    h, w = roi.shape[:2]

    # Canvas = phần trên, bỏ thanh slider
    canvas_bottom = int(h * 0.74)
    canvas = roi[0:canvas_bottom, 0:w]
    if min(canvas.shape[:2]) < 60:
        raise AgentError("SOLVER_CROP_FAILED", "Canvas puzzle quá nhỏ")

    ch, cw = canvas.shape[:2]

    # Piece = dải bên trái canvas (đúng cách extract của source SadCaptcha)
    piece_w = max(int(cw * 0.26), 48)
    piece = canvas[0:ch, 0:piece_w].copy()

    if min(piece.shape[:2]) < 24:
        raise AgentError("SOLVER_CROP_FAILED", "Piece puzzle quá nhỏ")

    # Che piece trên canvas trước khi gửi API (bắt buộc với SadCaptcha)
    canvas_for_api = canvas.copy()
    cv2.ellipse(
        canvas_for_api,
        (piece_w // 2, ch // 2),
        (piece_w // 2 + 6, ch // 2 + 6),
        0, 0, 360,
        (0, 0, 0),
        -1,
    )

    _save_debug(cv2, canvas_for_api, "puzzle_canvas_masked")
    _save_debug(cv2, piece, "puzzle_piece")

    puzzle_b64 = _b64_png(cv2, canvas_for_api)
    piece_b64  = _b64_png(cv2, piece)

    analysis = self._analyzer.analyze("puzzle", puzzle_b64, piece_b64)
    proportion = float(analysis["result"]["slideXProportion"])
    distance = int(proportion * cw)

    # Vị trí nút slider (mũi tên) – nằm ở đáy dialog, lệch trái
    # Theo ảnh mẫu: nút nằm khoảng y ≈ 0.86*h, x bắt đầu ≈ 0.06*w
    slider_y = offset_y + int(h * 0.86)
    start_x  = offset_x + int(w * 0.07)
    end_x    = start_x + distance

    log.info("Puzzle: kéo %d px (proportion=%.4f)", distance, proportion)
    _drag(start_x, slider_y, end_x, slider_y)
    time.sleep(1.6)

    return SolveResult(
        True,
        challenge_type="puzzle",
        detail=f"kéo {distance}px (prop={proportion:.3f})",
        attempts=1,
    )


# ------------------------------------------------------------------
# Shapes  (dựa trên shapes_challenge_popup_wrapped.png)
# ------------------------------------------------------------------

def _do_shapes(self, cv2, roi, offset_x: int, offset_y: int) -> SolveResult:
    """
    Layout mẫu:
    - Toàn bộ ROI là dialog (prompt + canvas object + Confirm + Audio)
    - SadCaptcha nhận toàn bộ ảnh dialog, trả về 2 điểm proportion
      tính trên toàn ảnh đó.
    - Nút Confirm nằm khoảng y = 0.78–0.88 chiều cao dialog
    """
    h, w = roi.shape[:2]
    if min(h, w) < 100:
        raise AgentError("SOLVER_CROP_FAILED", "ROI shapes quá nhỏ")

    _save_debug(cv2, roi, "shapes_roi")

    img_b64 = _b64_png(cv2, roi)
    analysis = self._analyzer.analyze("shapes", img_b64)
    r = analysis["result"]

    p1x = offset_x + int(w * r["pointOneProportionX"])
    p1y = offset_y + int(h * r["pointOneProportionY"])
    p2x = offset_x + int(w * r["pointTwoProportionX"])
    p2y = offset_y + int(h * r["pointTwoProportionY"])

    log.info(
        "Shapes: click (%.3f,%.3f) → (%.3f,%.3f)",
        r["pointOneProportionX"], r["pointOneProportionY"],
        r["pointTwoProportionX"], r["pointTwoProportionY"],
    )

    _click(p1x, p1y)
    time.sleep(random.uniform(0.28, 0.52))
    _click(p2x, p2y)
    time.sleep(random.uniform(0.30, 0.55))

    # Confirm theo ảnh mẫu: nút đỏ nằm khoảng 78–88% chiều cao
    confirm_x = offset_x + w // 2
    confirm_y = offset_y + int(h * 0.83)
    _click(confirm_x, confirm_y)

    time.sleep(1.3)
    return SolveResult(
        True,
        challenge_type="shapes",
        detail="đã click 2 điểm + Confirm",
        attempts=1,
    )