"""
Unit test solver trên 2 ảnh mẫu (không cần Live Studio).

Chạy:
    cd agent-core
    python -m pytest tests/test_solver_on_samples.py -v -s

Hoặc chạy thẳng:
    python tests/test_solver_on_samples.py
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path
from unittest.mock import MagicMock
import tempfile
import cv2
import numpy as np
import pytest

# Đường dẫn gốc project (điều chỉnh nếu cần)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sarae_agent.monitoring.solver import (
    CaptchaSolver,
    SolveResult,
    _b64_png,
    DEBUG_SAVE_CROPS,
)

# Đường dẫn 2 ảnh mẫu (bạn đang để trong attachments hoặc resources)
SAMPLES_DIR = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "sarae_agent"
    / "monitoring"
    / "resources"
)

PUZZLE_IMG = SAMPLES_DIR / "puzzle_challenge_large.png"
SHAPES_IMG = SAMPLES_DIR / "shapes_challenge_popup_wrapped.png"

OUT_DIR = Path(tempfile.gettempdir()) / "sarae_solver_debug"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_bgr(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    assert img is not None, f"Không đọc được {path}"
    return img


# ---------------------------------------------------------------------------
# Mock SadCaptchaAnalyzer – trả về giá trị giả lập hợp lệ
# ---------------------------------------------------------------------------

class MockAnalyzer:
    def analyze(self, kind: str, image_base64: str, piece_image_base64: str | None = None) -> dict:
        if kind == "puzzle":
            # Giả sử mảnh cần kéo khoảng 45% chiều rộng canvas
            return {
                "result": {"slideXProportion": 0.45},
                "state": "ANALYSIS_READY",
            }
        if kind == "shapes":
            # 2 điểm giả lập nằm trong vùng canvas object
            return {
                "result": {
                    "pointOneProportionX": 0.28,
                    "pointOneProportionY": 0.42,
                    "pointTwoProportionX": 0.62,
                    "pointTwoProportionY": 0.55,
                },
                "state": "ANALYSIS_READY",
            }
        raise ValueError(f"kind không hỗ trợ: {kind}")


# ---------------------------------------------------------------------------
# Test extract logic (không gọi mouse thật)
# ---------------------------------------------------------------------------

def test_puzzle_extract_and_plan():
    assert PUZZLE_IMG.is_file(), f"Thiếu file mẫu: {PUZZLE_IMG}"

    roi = load_bgr(PUZZLE_IMG)
    h, w = roi.shape[:2]
    print(f"\n[PUZZLE] size = {w}x{h}")

    # --- copy logic extract từ _do_puzzle ---
    canvas_bottom = int(h * 0.74)
    canvas = roi[0:canvas_bottom, 0:w]
    ch, cw = canvas.shape[:2]
    piece_w = max(int(cw * 0.26), 48)
    piece = canvas[0:ch, 0:piece_w].copy()

    canvas_for_api = canvas.copy()
    cv2.ellipse(
        canvas_for_api,
        (piece_w // 2, ch // 2),
        (piece_w // 2 + 6, ch // 2 + 6),
        0, 0, 360,
        (0, 0, 0),
        -1,
    )

    # Lưu debug
    cv2.imwrite(str(OUT_DIR / "puzzle_roi.png"), roi)
    cv2.imwrite(str(OUT_DIR / "puzzle_canvas.png"), canvas)
    cv2.imwrite(str(OUT_DIR / "puzzle_piece.png"), piece)
    cv2.imwrite(str(OUT_DIR / "puzzle_canvas_masked.png"), canvas_for_api)

    print(f"  canvas   : {cw}x{ch}")
    print(f"  piece    : {piece.shape[1]}x{piece.shape[0]}")
    print(f"  masked saved → {OUT_DIR}")

    # Gọi analyzer (mock)
    analyzer = MockAnalyzer()
    puzzle_b64 = _b64_png(cv2, canvas_for_api)
    piece_b64 = _b64_png(cv2, piece)
    analysis = analyzer.analyze("puzzle", puzzle_b64, piece_b64)
    proportion = float(analysis["result"]["slideXProportion"])
    distance = int(proportion * cw)

    # Tọa độ sẽ kéo (offset = 0 vì ảnh đã là dialog)
    slider_y = int(h * 0.86)
    start_x = int(w * 0.07)
    end_x = start_x + distance

    print(f"  proportion = {proportion:.4f}")
    print(f"  drag: ({start_x}, {slider_y}) → ({end_x}, {slider_y})  distance={distance}px")

    assert distance > 20, "distance quá nhỏ – kiểm tra lại proportion/crop"
    assert piece.shape[0] > 30 and piece.shape[1] > 30
    assert canvas_for_api.mean() < canvas.mean()  # đã bị che đen


def test_shapes_extract_and_plan():
    assert SHAPES_IMG.is_file(), f"Thiếu file mẫu: {SHAPES_IMG}"

    roi = load_bgr(SHAPES_IMG)
    h, w = roi.shape[:2]
    print(f"\n[SHAPES] size = {w}x{h}")

    cv2.imwrite(str(OUT_DIR / "shapes_roi.png"), roi)

    analyzer = MockAnalyzer()
    img_b64 = _b64_png(cv2, roi)
    analysis = analyzer.analyze("shapes", img_b64)
    r = analysis["result"]

    p1x = int(w * r["pointOneProportionX"])
    p1y = int(h * r["pointOneProportionY"])
    p2x = int(w * r["pointTwoProportionX"])
    p2y = int(h * r["pointTwoProportionY"])
    confirm_x = w // 2
    confirm_y = int(h * 0.83)

    print(f"  click1 : ({p1x}, {p1y})")
    print(f"  click2 : ({p2x}, {p2y})")
    print(f"  confirm: ({confirm_x}, {confirm_y})")

    # Vẽ điểm lên ảnh để kiểm tra mắt
    vis = roi.copy()
    cv2.circle(vis, (p1x, p1y), 8, (0, 255, 0), 2)
    cv2.circle(vis, (p2x, p2y), 8, (0, 255, 0), 2)
    cv2.circle(vis, (confirm_x, confirm_y), 10, (0, 0, 255), 2)
    cv2.imwrite(str(OUT_DIR / "shapes_planned_clicks.png"), vis)
    print(f"  planned clicks saved → {OUT_DIR / 'shapes_planned_clicks.png'}")

    assert 0 < p1x < w and 0 < p1y < h
    assert 0 < p2x < w and 0 < p2y < h


def test_solver_class_with_mock(monkeypatch):
    """Test CaptchaSolver.try_solve với mock mouse + mock analyzer."""
    if not PUZZLE_IMG.is_file():
        pytest.skip("Thiếu ảnh mẫu puzzle")

    # Chặn mouse thật
    monkeypatch.setattr("sarae_agent.monitoring.solver._drag", lambda *a, **k: None)
    monkeypatch.setattr("sarae_agent.monitoring.solver._click", lambda *a, **k: None)
    monkeypatch.setattr("sarae_agent.monitoring.solver.find_studio_window", lambda: None)

    emit = MagicMock()
    notify = MagicMock()
    solver = CaptchaSolver(analyzer=MockAnalyzer(), emit=emit, notify_owner=notify)

    # Giả lập image_path
    result = solver.try_solve(challenge_type="puzzle", image_path=str(PUZZLE_IMG))
    print(f"\n[CaptchaSolver] puzzle result: success={result.success}, detail={result.detail}, error={result.error}")

    # Vì find_studio_window trả None, _acquire_frame sẽ fail nếu không có image_path xử lý đúng.
    # Trong bản solver đã sửa, khi có image_path hợp lệ thì vẫn chạy được.
    # Nếu bạn chưa merge logic "if image_path: dùng nguyên ảnh", test này có thể fail.
    # Khi đó chỉ cần chạy 2 test extract ở trên là đủ để kiểm tra crop.


if __name__ == "__main__":
    print("=" * 60)
    print("Test extract + plan trên ảnh mẫu (không cần Live Studio)")
    print("=" * 60)

    test_puzzle_extract_and_plan()
    test_shapes_extract_and_plan()

    print("\n" + "=" * 60)
    print(f"Xong. Mở thư mục debug: {OUT_DIR}")
    print("  - puzzle_canvas_masked.png  → kiểm tra vùng piece đã bị che đen")
    print("  - puzzle_piece.png          → mảnh extract")
    print("  - shapes_planned_clicks.png → 2 điểm + Confirm")
    print("=" * 60)