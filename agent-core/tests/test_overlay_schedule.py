"""overlay/schedule.py against the golden vectors of the web implementation (web/src/overlay/overlaySchedule.ts)."""
import json
import unittest
from pathlib import Path

from sarae_agent.overlay.schedule import generator, is_planned, pin_spans, random_plan, seed_of, windows_of
from sarae_agent.overlay.service import shown_in, snapshot_problem

VECTORS = json.loads((Path(__file__).resolve().parent / "fixtures" / "overlay_schedule_vectors.json").read_text(encoding="utf-8"))


class ScheduleVectorTests(unittest.TestCase):
    def test_seed_and_generator_match_the_web(self):
        for case in VECTORS["seeds"]:
            self.assertEqual(seed_of(case["key"]), case["seed"], case["key"])
            next_ = generator(case["seed"])
            self.assertEqual([next_(), next_(), next_()], case["first"], case["key"])

    def test_pin_spans_match_the_web(self):
        self.assertTrue(VECTORS["pins"])
        for case in VECTORS["pins"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(pin_spans(case["markers"], case["duration_ms"]), case["spans"])

    def test_plans_match_the_web(self):
        self.assertTrue(VECTORS["cases"])
        self.assertEqual({c["schedule"]["mode"] for c in VECTORS["cases"]}, {"window", "random"})
        for case in VECTORS["cases"]:
            for expected in case["plans"]:
                with self.subTest(case=case["name"], iteration=expected["iteration"]):
                    self.assertEqual(random_plan(case["overlays"], case["media"], case["schedule"], case["timeline_id"],
                                                 expected["iteration"], case.get("video")), expected["plan"])

    def test_random_windows_come_from_the_pins(self):
        video = {"pins": [{"start_ms": 0, "end_ms": 400, "product_id": 3}, {"start_ms": 500, "end_ms": 900, "product_id": 3}]}
        free, pinned, never = ({"start_ms": 7, "duration_ms": 9, "product_id": p} for p in (None, 3, 4))
        self.assertEqual(windows_of(free, "random", video), [(0, 900)], "no duration: the last pin end")
        self.assertEqual(windows_of(free, "random", {**video, "duration_ms": 1200}), [(0, 1200)])
        self.assertEqual(windows_of(pinned, "random", video), [(0, 400), (500, 900)])
        self.assertEqual(windows_of(never, "random", video), [])
        self.assertEqual(windows_of(free, "random", None), [], "nothing known about the video: never shown")
        self.assertEqual(windows_of(pinned, "window", video), [(7, 16)])
        self.assertEqual([is_planned(m) for m in ("fixed", "window", "random", "other", None)], [False, True, True, False, False])

    def test_plans_keep_one_overlay_at_a_time_with_the_gap(self):
        for case in VECTORS["cases"]:
            for expected in case["plans"]:
                plan, gap = expected["plan"], case["schedule"]["min_gap_ms"]
                for a, b in zip(plan, plan[1:]):
                    self.assertGreaterEqual(b["start_ms"], a["end_ms"] + gap, case["name"])

    def test_shown_in_fixed_and_random(self):
        overlays = [{"id": i, "media_id": "m", "start_ms": 0, "duration_ms": 600_000, "show_ms": 5000} for i in range(6)]
        fixed = {"overlays": overlays, "timeline_id": 1}
        self.assertEqual(len(shown_in(fixed, "m", 1000)), 6, "fixed mode: the window is the appearance")
        self.assertEqual(len(shown_in({**fixed, "schedule": {"mode": "fixed", "every_ms": 60_000, "min_gap_ms": 0}}, "m", 1000)), 6)
        scene = {**fixed, "schedule": {"mode": "window", "every_ms": 60_000, "min_gap_ms": 10_000}}
        plan = random_plan(overlays, "m", scene["schedule"], 1, 2)
        first = plan[0]
        self.assertEqual(shown_in(scene, "m", first["start_ms"], 2), [overlays[first["index"]]])
        self.assertEqual(shown_in(scene, "m", first["end_ms"], 2), [])
        self.assertEqual(shown_in(scene, "other", first["start_ms"], 2), [])
        self.assertIsNotNone(snapshot_problem(fixed), "fixed: more than 4 at once is refused")
        self.assertIsNone(snapshot_problem(scene), "window: one at a time by construction")
        self.assertEqual(len(shown_in({**fixed, "schedule": {"mode": "later", "every_ms": 60_000, "min_gap_ms": 0}}, "m", 1000)), 6,
                         "unknown mode: fixed")

    def test_shown_in_random_follows_the_snapshot_pins(self):
        overlays = [{"id": 1, "media_id": "m", "start_ms": 0, "duration_ms": 1000, "show_ms": 5000, "product_id": 9},
                    {"id": 2, "media_id": "m", "start_ms": 0, "duration_ms": 1000, "show_ms": 5000}]
        scene = {"overlays": overlays, "timeline_id": 4, "schedule": {"mode": "random", "every_ms": 60_000, "min_gap_ms": 10_000},
                 "pins": {"m": [{"start_ms": 120_000, "end_ms": 180_000, "product_id": 9}]}, "durations": {"m": 600_000}}
        plan = random_plan(overlays, "m", scene["schedule"], 4, 0, {"pins": scene["pins"]["m"], "duration_ms": 600_000})
        self.assertTrue(any(a["index"] == 0 for a in plan))
        for a in plan:
            self.assertEqual(shown_in(scene, "m", a["start_ms"]), [overlays[a["index"]]])
            if a["index"] == 0:
                self.assertTrue(120_000 <= a["start_ms"] and a["end_ms"] <= 180_000, "only while its product is pinned")
        self.assertEqual(shown_in(scene, "other", plan[0]["start_ms"]), [])
        self.assertIsNone(snapshot_problem(scene))
