"""Tiny Prometheus-text metrics registry (no extra dependency). Labels carry ids and classes, never text or tokens."""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable

SECONDS_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 21, 34, 60, 120, 300)


def _labels(labels: dict) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple, float]] = defaultdict(lambda: defaultdict(float))
        self._hist: dict[str, dict[tuple, list]] = defaultdict(dict)
        self._help: dict[str, tuple[str, str]] = {}
        self._gauges: list[Callable[[], list[tuple[str, dict, float, str]]]] = []

    def inc(self, name: str, help_: str = "", value: float = 1.0, **labels) -> None:
        with self._lock:
            self._help.setdefault(name, ("counter", help_))
            self._counters[name][tuple(sorted(labels.items()))] += value

    def observe(self, name: str, seconds: float, help_: str = "", **labels) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._help.setdefault(name, ("histogram", help_))
            h = self._hist[name].setdefault(key, [[0] * len(SECONDS_BUCKETS), 0.0, 0, []])
            for i, bound in enumerate(SECONDS_BUCKETS):
                if seconds <= bound:
                    h[0][i] += 1
            h[1] += seconds
            h[2] += 1
            h[3].append(seconds)
            if len(h[3]) > 1000:
                del h[3][:500]

    def gauges(self, fn: Callable[[], list[tuple[str, dict, float, str]]]) -> None:
        self._gauges.append(fn)

    def counter(self, name: str, **labels) -> float:
        with self._lock:
            return sum(v for k, v in self._counters[name].items() if all(dict(k).get(a) == b for a, b in labels.items()))

    def quantiles(self, name: str, **labels) -> dict:
        """p50/p95 over the last observations of the matching series (for /v1/status)."""
        with self._lock:
            values = sorted(v for k, h in self._hist.get(name, {}).items()
                            if all(dict(k).get(a) == b for a, b in labels.items()) for v in h[3])
        if not values:
            return {"n": 0}

        def q(p):
            i = (len(values) - 1) * p
            lo, hi = int(i), min(int(i) + 1, len(values) - 1)
            return round((values[lo] + (values[hi] - values[lo]) * (i - lo)) * 1000, 1)
        return {"n": len(values), "p50_ms": q(0.5), "p95_ms": q(0.95), "max_ms": round(values[-1] * 1000, 1)}

    def render(self) -> str:
        lines = []
        with self._lock:
            for name, series in sorted(self._counters.items()):
                kind, help_ = self._help.get(name, ("counter", ""))
                lines += [f"# HELP {name} {help_}", f"# TYPE {name} counter"]
                lines += [f"{name}{_labels(dict(k))} {v:g}" for k, v in sorted(series.items())]
            for name, series in sorted(self._hist.items()):
                _, help_ = self._help.get(name, ("histogram", ""))
                lines += [f"# HELP {name} {help_}", f"# TYPE {name} histogram"]
                for k, (buckets, total, count, _) in sorted(series.items()):
                    labels = dict(k)
                    for bound, n in zip(SECONDS_BUCKETS, buckets):
                        lines.append(f"{name}_bucket{_labels({**labels, 'le': bound})} {n}")
                    lines.append(f"{name}_bucket{_labels({**labels, 'le': '+Inf'})} {count}")
                    lines.append(f"{name}_sum{_labels(labels)} {total:.6f}")
                    lines.append(f"{name}_count{_labels(labels)} {count}")
        for fn in self._gauges:
            seen = set()
            for name, labels, value, help_ in fn():
                if name not in seen:
                    lines += [f"# HELP {name} {help_}", f"# TYPE {name} gauge"]
                    seen.add(name)
                lines.append(f"{name}{_labels(labels)} {value:g}")
        return "\n".join(lines) + "\n"
