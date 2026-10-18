import cv2
import numpy as np
import pytest

from conftest import windows_only
from sarae_agent.config import ConfigStore, paths
from sarae_agent.errors import AgentError
from sarae_agent.monitoring.captcha import CaptchaMonitor, match_reference
from sarae_agent.monitoring.studio import is_studio_executable


def synthetic_dialog() -> np.ndarray:
    """A fake CAPTCHA dialog: framed box with a title bar and text-like strokes."""
    img = np.full((140, 260), 235, np.uint8)
    cv2.rectangle(img, (4, 4), (255, 135), 40, 3)
    cv2.rectangle(img, (4, 4), (255, 34), 90, -1)
    cv2.putText(img, "Verify you are human", (14, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, 255, 1)
    cv2.putText(img, "Drag the slider", (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, 20, 2)
    cv2.rectangle(img, (30, 100), (230, 122), 60, 2)
    return img


def screen_with(dialog: np.ndarray | None, scale: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(7)
    screen = rng.integers(0, 40, (720, 1280), dtype=np.uint8)  # dark, noisy LIVE preview
    cv2.rectangle(screen, (100, 600), (500, 680), 120, -1)
    if dialog is not None:
        d = cv2.resize(dialog, None, fx=scale, fy=scale)
        screen[300:300 + d.shape[0], 500:500 + d.shape[1]] = d
    return screen


def test_matcher_detects_dialog_and_tolerates_scale():
    template = synthetic_dialog()[4:60, 4:255]  # title + first line, as an operator would crop
    present, _ = match_reference(cv2, np, screen_with(synthetic_dialog()), template)
    scaled, scale = match_reference(cv2, np, screen_with(synthetic_dialog(), 1.1), template)
    absent, _ = match_reference(cv2, np, screen_with(None), template)
    assert present > 0.82  # same display scaling as the template: above the default threshold
    assert absent < 0.5
    # 10 % scale drift still separates from "no dialog", but falls below the default 0.82:
    # templates must be captured at the Studio's actual Windows display scaling (documented).
    assert scaled > absent + 0.2


def test_template_without_detail_rejected():
    with pytest.raises(ValueError):
        match_reference(cv2, np, screen_with(None), np.full((40, 40), 128, np.uint8))


@pytest.mark.parametrize("name,expected", [
    ("TikTok LIVE Studio.exe", True), ("TikTokLiveStudio.exe", True), ("tiktok_live_studio.EXE", True),
    ("TikTok.exe", False), ("obs64.exe", False), ("TikTok LIVE Studio", False),
])
def test_studio_executable_identity(name, expected):
    assert is_studio_executable(name) is expected


def test_custom_executable_names():
    assert is_studio_executable("LiveStudioBeta.exe", "livestudiobeta")


@windows_only
def test_templates_and_start_guard(home, events, tmp_path):
    monitor = CaptchaMonitor(ConfigStore(), events)
    with pytest.raises(AgentError) as exc:
        monitor.start()
    assert exc.value.code == "TEMPLATE_MISSING"
    good = tmp_path / "captcha.png"
    cv2.imwrite(str(good), synthetic_dialog())
    added = monitor.add_template(str(good))
    assert (paths.monitor_templates / added["name"]).is_file()
    flat = tmp_path / "flat.png"
    cv2.imwrite(str(flat), np.full((60, 60, 3), 200, np.uint8))
    with pytest.raises(AgentError) as exc:
        monitor.add_template(str(flat))
    assert exc.value.code == "INVALID_TEMPLATE"
    with pytest.raises(AgentError):
        monitor.remove_template("..\\..\\config.json")
    config = monitor.configure({"interval_seconds": 1.5, "threshold": 0.9, "unknown": 1})
    assert config["interval_seconds"] == 1.5 and config["threshold"] == 0.9
    # Starting with a template runs the loop; with no TikTok window it reports that state.
    monitor.start()
    try:
        import time
        deadline = time.monotonic() + 10
        while monitor.status()["state"]["status"] in ("starting",) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert monitor.status()["state"]["status"] in ("window_unavailable", "watching", "captcha_detected")
    finally:
        stopped = monitor.stop()
    assert stopped["state"]["running"] is False
    assert any(e["kind"] == "monitor_stopped" for e in stopped["events"])
