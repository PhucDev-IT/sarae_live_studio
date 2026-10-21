# TikTok LIVE Observer — PoC evidence report

Date: 2026-10-21. Scope: Windows Agent observer and event contract only. This
module is **not connected to production LIVE control**, Auto Pin, Cloud, or OBS.

## Evidence inspected

- `agent-core/src/sarae_agent/auto_pin.py` already listens to Playwright
  `response` during explicit product sync. It recognizes
  `/api/v1/streamer_desktop/account_info/get` and
  `/api/v1/streamer_desktop/live_product/list`, then reloads the Console to
  let the page generate its own signed requests. The current DOM fallback
  scans pre-LIVE order inputs; it does not establish a pinned product.
- `docs/TOOLS_SARAE_BUSINESS_SPEC_2026-10-21.md` records two observed
  `account_info/get` samples: Seller `user_id=7494647113328657914`,
  `user_type=1`; Affiliate `user_id=7495962653689350865`, `user_type=3`.
  These are examples, not a universal user-type rule.
- No captured Network or DOM evidence in this repo establishes a stable
  response field for current pin state, manual pin/unpin, or actual TikTok LIVE
  state/end. The existing `live_product/list` contract gives product IDs and
  Seller IDs in the cart, not broadcaster identity or pin state.

## Implemented PoC

`agent-core/src/sarae_agent/tiktok_observer.py` attaches read-only Playwright
Page listeners and accepts existing responses from allowed Console hosts.
It never constructs signed URLs, clicks buttons, starts/ends TikTok LIVE, or
changes OBS. It emits UTC timestamped events with account ID, source, and
confidence. Product events contain TikTok product ID and Seller ID when present.
An account change clears prior cart, pin, LIVE state, and Agent intents.
Navigation/reload and disconnection set pin and LIVE state to `UNKNOWN`.
Repeated identical snapshots are suppressed.

The `PinEvidence` and `LiveEvidence` inputs are an **integration contract for
a future verified parser**. No parser calls these methods today. Synthetic
fixtures test the reducer and event shape, but do not prove TikTok supplies
those signals. A manual `PIN_CHANGED` event has `actor=EXTERNAL` only if no
matching Agent command is pending. Explicit command correlation marks
`actor=AGENT`; temporal overlap without correlation marks `actor=UNKNOWN`
to avoid falsely pausing Auto Pin. The proposed manual-action consumer can
pause Auto Pin and switch video only on confirmed `actor=EXTERNAL` events;
that consumer is outside this PoC.

`TIKTOK_LIVE_ENDED` is separate from `LIVE_STATE_CHANGED` and emitted only
for a confirmed `LIVE → ENDED` transition. OBS stopping, browser closing, or
missing observations never emit it. Without verified pin/LIVE parsers, those
states remain `UNKNOWN`, so the PoC must not trigger automatic control.

## Fixture verification

`tests/fixtures/tiktok_observer_seller.json` and
`tests/fixtures/tiktok_observer_affiliate.json` model the documented account
identities and cart structure. The tests cover both account types, multiple
Affiliate Sellers, cart delta and deduplication, manual pin/unpin contract,
Agent correlation ambiguity, reload/disconnect/account epoch, UNKNOWN,
untrusted/incomplete responses, passive Page listeners, and OBS independence.

On this machine `pytest` is unavailable. The six dependency-free test
functions passed with:

```powershell
$env:PYTHONPATH='src;tests'
python -B -c "import test_tiktok_observer as t; fs=[getattr(t,n) for n in dir(t) if n.startswith('test_')]; [f() for f in fs]; print(f'{len(fs)} observer tests passed')"
```

Result: `6 observer tests passed`.

## Required real Console validation before integration

Capture redacted Network metadata and response schemas while an operator
manually pins/unpins, changes the cart, starts/ends TikTok LIVE, reloads, and
switches between Seller and Affiliate accounts. Record endpoint path, response
field names, ordering, and whether events are pushed or polled. Strip cookies,
headers, signed query strings, personal names, and tokens. Confirm the pin
product ID/Seller ID mapping and LIVE end signal across both account types.
Only then implement and test a parser for `PinEvidence`/`LiveEvidence`.
