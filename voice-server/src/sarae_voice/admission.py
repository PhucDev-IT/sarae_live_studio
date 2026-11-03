"""LIVE leases (docs/TTS_SERVER.md 6).

Sarae Backend renews PUT /v1/admission/live every ~30 s for each LIVE session that uses this voice server; a lease
that is not renewed expires by itself, so a lost Backend never leaves the GPU in LIVE mode.
While at least one lease is active:
  * new long/benchmark jobs are rejected (423 LIVE_ACTIVE); those queued before the lease are held (not started)
    until no lease is active or their deadline passes;
  * prepare jobs are accepted only when their estimated audio is short (prepare_live_max_audio_s).
A GPU call that already started cannot be pre-empted. The lease response reports it (class, elapsed, a rough
estimate of what is left) and `ready_for_live: false`; it is an estimate, never a promise.
"""
from __future__ import annotations

import threading
import time

from sarae_voice.errors import ServiceError

HELD_DURING_LIVE = ("long", "benchmark")


class LiveAdmission:
    def __init__(self, max_ttl_s: float):
        self.max_ttl_s = max_ttl_s
        self._leases: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()
        self.expired_total = 0

    def _sweep(self, now: float) -> None:
        for key in [k for k, v in self._leases.items() if v["expires"] <= now]:
            del self._leases[key]
            self.expired_total += 1

    def put(self, owner_ref: str, session_id: str, ttl_s: float) -> dict:
        if not 5 <= ttl_s <= self.max_ttl_s:
            raise ServiceError("INVALID_PARAMS", f"ttl_s must be 5-{self.max_ttl_s:g}", 422)
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            lease = self._leases.get((owner_ref, session_id))
            created = lease["created"] if lease else time.time()
            self._leases[(owner_ref, session_id)] = {"expires": now + ttl_s, "created": created, "renewed": time.time(),
                                                     "renewals": (lease["renewals"] + 1) if lease else 0}
        return self.view()

    def delete(self, owner_ref: str, session_id: str) -> bool:
        with self._lock:
            return self._leases.pop((owner_ref, session_id), None) is not None

    def active(self) -> bool:
        with self._lock:
            self._sweep(time.monotonic())
            return bool(self._leases)

    def view(self) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            return [{"owner_ref": o, "session_id": s, "expires_in_s": round(v["expires"] - now, 1),
                     "renewals": v["renewals"]} for (o, s), v in sorted(self._leases.items())]

    def holds(self, klass: str) -> bool:
        return klass in HELD_DURING_LIVE and self.active()
