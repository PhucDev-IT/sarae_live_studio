# Auto Pin — Windows Agent (experimental)

## Scope

The Tauri GUI has a **Tự động ghim** row (switch + ⌄ with two steps). Both steps use one
*dedicated Chrome profile* under `%LOCALAPPDATA%\\SaraeAgent\\chrome-auto-pin-profile` (not a Chrome extension):

1. **Đăng nhập TikTok (Chrome thường)** — `autopin.login` starts the installed `chrome.exe` as an ordinary
   process (`--user-data-dir=<profile> --no-first-run <LIVE Console URL>`, found through the App Paths registry key
   or the standard install folders). No Playwright, no automation flags: the operator signs in by hand, as in a
   normal browser. Signing in through a Playwright-launched Chrome made TikTok see a new, automated device, ask
   for e-mail codes and lock the account ("Maximum number of attempts").
2. **Bắt đầu Auto Pin** — after "Đã đăng nhập xong" (`autopin.login_done` closes that Chrome gracefully with
   `taskkill` without `/F`, so the session is saved) or after the operator closes it, Playwright opens the *same*
   profile and reuses its cookies. It refuses to start while the login Chrome (or its profile `lockfile`) is
   still open, because Chrome cannot run twice on one profile.

If the controlled page lands on a login / verify URL, Auto Pin stops (`state: login_required`) and asks for
step 1 again. The Agent never types credentials, never touches CAPTCHA or verification dialogs and adds no
stealth / fingerprint changes. If TikTok answers "Maximum number of attempts", wait a few hours before signing in.

Supported local IPC methods:

| Method | Parameters | Result |
|---|---|---|
| `autopin.login` | `{url?}` | Status (`state: login`, `login_open: true`) |
| `autopin.login_done` | `{}` | Status (`state: ready`, `browser_open: true`) |
| `autopin.open` | `{url}` | Status; Playwright on the profile (refused while the login Chrome is open) |
| `autopin.scan` | `{}` | Status + `products_found [{name, pinned}]`; the page's products become the pin list |
| `autopin.configure` | `{products: string[], interval_seconds: number}` | Status | (fallback: names typed by hand) |
| `autopin.start` | `{}` | Status (opens Playwright first if needed; with no list, scans the page first) |
| `autopin.stop` | `{}` | Status |
| `autopin.status` | `{}` | Status |

Only allowlisted HTTPS TikTok seller/LIVE Console hosts are accepted. The Agent does not capture passwords,
read browser cookies or bypass CAPTCHA.

Products are read from the console, not typed: a product row is the largest element around a Ghim/Pin
button that holds no other such button, and its name is the longest text line that is not a button label,
price or count (`SCAN_PRODUCTS_JS`, read only). The first connection after login scans automatically; the
operator unticks products not to pin. The generic rule must be checked on the real console once.

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
