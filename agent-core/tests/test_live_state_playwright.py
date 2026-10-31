"""LIVE_STATE_JS against elements copied from the real Seller LIVE Console, in real Chrome (read only, never clicks)."""
import pytest

from sarae_agent.auto_pin import LIVE_LABELS, LIVE_STATE_JS, find_chrome

# "Phát LIVE" button shown before going live (copied from the Console).
GO_LIVE = ('<button data-tid="m4b_button" class="arco-btn arco-btn-primary arco-btn-size-default arco-btn-shape-square '
           'm4b-button text-body-s-medium" type="button" style="border-radius: 4px; min-width: 75px; height: 24px;">'
           '<span>Phát LIVE</span></button>')
# Icon-only end LIVE control shown while live (copied from the Console, path shortened).
END_LIVE = ('<div class="text-neutral-white cursor-pointer ml-6 rounded-4 w-24 h-24 flex items-center justify-center">'
            '<svg class="arco-icon arco-icon-im_close_chat " width="1em" height="1em" viewBox="0 0 16 16" font-size="16">'
            '<path d="M7.6 1.3h.7v5h-.7z"></path></svg></div>')
HIDDEN_END_LIVE = f'<div style="display: none">{END_LIVE}</div>'


@pytest.fixture(scope="module")
def page():
    try:
        chrome = find_chrome()
    except RuntimeError:
        pytest.skip("Google Chrome is not installed")
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chrome)
        yield browser.new_page()
        browser.close()


@pytest.mark.parametrize("html, state", [
    (GO_LIVE, "OFFLINE"),
    (END_LIVE, "LIVE"),
    (GO_LIVE + HIDDEN_END_LIVE, "OFFLINE"),
    (GO_LIVE + END_LIVE, "UNKNOWN"),
    (END_LIVE + END_LIVE, "UNKNOWN"),
    ("<p>Console is loading</p>", "UNKNOWN"),
])
def test_live_state_from_console_controls(page, html, state):
    page.set_content(f"<body onclick=\"window.CLICKED = true\">{html}</body>")
    result = page.evaluate(LIVE_STATE_JS, LIVE_LABELS)
    assert result["state"] == state, result
    assert page.evaluate("window.CLICKED") is None  # reading the state never clicks anything
