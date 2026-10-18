# Auto Pin — Windows Agent (experimental)

## Scope

The Tauri GUI contains a **Tự động ghim** page. The Python Agent Core uses
Playwright to launch a *dedicated, visible Chrome profile* under
`%LOCALAPPDATA%\\SaraeAgent\\chrome-auto-pin-profile`, not a Chrome extension.

Supported local IPC methods:

| Method | Parameters | Result |
|---|---|---|
| `autopin.open` | `{url}` | Auto Pin status |
| `autopin.configure` | `{products: string[], interval_seconds: number}` | Status |
| `autopin.start` | `{}` | Status |
| `autopin.stop` | `{}` | Status |
| `autopin.status` | `{}` | Status |

Only allowlisted HTTPS TikTok seller/LIVE Console hosts are accepted. The user
signs in directly in the opened Chrome. The Agent does not capture passwords,
read browser cookies or bypass CAPTCHA.

Every interval, the service locates the next *exactly named* product in the
currently opened page, looks for a unique Ghim/Pin button within that row,
clicks it, then checks that Bỏ ghim/Unpin is visible. If a button or product
cannot be uniquely identified, the service **stops rather than guessing**.

## Limitations (must verify before production)

- TikTok LIVE Console DOM, locales, product row structure, and URL can change;
  the included locator is an initial implementation, **not verified against a
  live session**. Test selectors against the customer's actual LIVE Console.
- No sync-to-OBS-product-ID mapping yet: current implementation rotates through
  user-supplied names on a timer. It does not promise one pin per playback event.
- Current cycle does not explicitly unpin a previously pinned product. Verify
  LIVE Console pin semantics (single pin vs multiple pins) before distribution.
- Playwright needs Chrome installed and its Playwright driver packaged into the
  Agent Core executable. Ensure the PyInstaller build collects Playwright's
  package data/driver and smoke-test the installed NSIS build, not just Python.
- `playwright.sync_api` is used solely on the dedicated service worker.
  Browser access is stopped during Agent Core shutdown.
- If TikTok requires an additional user verification step, a person must
  complete it directly in Chrome. The service doesn't bypass CAPTCHA.

## Local test

```powershell
cd agent-core
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -p "test_auto_pin.py"
```

Then launch Windows Agent, visit **Tự động ghim**, open the separate Chrome,
sign in to LIVE Console, enter exact product names/IDs from the page, choose
the interval and start. If Auto Pin stops with "Không xác định được duy nhất
nút Ghim", collect the DOM structure of the authorized LIVE Console page and
adjust the product-row locator in `auto_pin.py` before enabling it for customers.
