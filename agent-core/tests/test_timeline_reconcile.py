"""Single Video Timeline, step 4: what is really pinned on the Console (read back on start/reconnect and every
reconcile period), manual pins (from the Web or directly on the Console) and resuming the timeline."""
import unittest
from concurrent.futures import Future
from types import SimpleNamespace

from sarae_agent.cloud.commands import CommandRouter, lane
from sarae_agent.errors import AgentError
from sarae_agent.timeline.executors import ConsolePinExecutor, DryRunPinExecutor
from sarae_agent.timeline.scheduler import RESUME_AT_NEXT_MARKER, TimelineScheduler
from test_playback_engine import EngineTestBase
from test_timeline_scheduler import MIN, PLAN, Clock, Gate, product, timeline


class ConsoleExecutor:
    """Pins and reads a simulated Console; reads finish when the test says so."""
    dry_run = False

    def __init__(self, pinned=None):
        self.pinned = pinned
        self.calls = []
        self.reads = []

    def submit(self, action, target, current, done):
        self.calls.append((action, (target or {}).get("tiktok_product_id")))
        self.pinned = (target or {}).get("tiktok_product_id") if action == "PIN_PRODUCT" else None
        done(True, None)

    def read(self, done):
        self.reads.append(done)

    def answer(self, pinned="same", ok=True):
        done = self.reads.pop(0)
        done(ok, self.pinned if pinned == "same" else pinned, None if ok else "Console đóng")


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.events = []
        self.console = ConsoleExecutor(pinned="9002")  # product 2 left pinned from before the Agent restarted
        self.scheduler = TimelineScheduler(self.console, Gate(), self.events.append, clock=self.clock,
                                           reconcile_seconds=30)
        self.scheduler.load(7, timeline(PLAN))

    def at(self, position_ms, seconds=1):
        self.clock.now += seconds
        self.scheduler.observe(7, position_ms, 0)

    def overrides(self):
        return [e["detail"] for e in self.events if e["type"] == "MANUAL_OVERRIDE"]

    def test_unknown_state_is_read_back_before_acting(self):
        self.at(30_000)
        self.assertEqual((self.console.calls, len(self.console.reads)), ([], 1), "first read, nothing to pin yet")
        self.console.answer()
        self.assertEqual(self.scheduler.status()["actual_product_id"], 2)
        self.at(31_000)
        self.assertEqual(self.console.calls, [("UNPIN_PRODUCT", None)], "a leftover pin is not the timeline's: unpin")
        self.assertEqual(self.overrides(), [], "a state read while unknown is not a manual override")

    def test_a_pin_changed_on_the_console_holds_the_timeline(self):
        self.at(2 * MIN)
        self.console.answer()
        self.at(2 * MIN + 1000)
        self.assertEqual(self.console.calls, [("PIN_PRODUCT", "9001")])
        self.console.pinned = "9003"  # the operator pinned product 3 directly on TikTok
        self.at(2 * MIN + 40_000, seconds=40)
        self.console.answer()
        self.assertEqual((self.scheduler.status()["mode"], self.scheduler.status()["actual_product_id"]), ("MANUAL_HOLD", 3))
        self.assertEqual(self.overrides()[0]["source"], "console")
        for position in range(10 * MIN, 20 * MIN, 30_000):
            self.at(position, seconds=30)
            while self.console.reads:
                self.console.answer()
        self.assertEqual(len(self.console.calls), 1, "the operator's pin is never overwritten")
        self.scheduler.resume()
        self.at(20 * MIN)
        self.assertEqual(self.console.calls[-1], ("PIN_PRODUCT", "9002"))

    def test_an_unknown_card_on_the_console_counts_too(self):
        self.at(2 * MIN)
        self.console.answer()
        self.at(2 * MIN + 1000)
        self.console.pinned = "123456"
        self.at(2 * MIN + 40_000, seconds=40)
        self.console.answer()
        status = self.scheduler.status()
        self.assertEqual((status["mode"], status["actual_product_id"], status["actual_tiktok_product_id"]),
                         ("MANUAL_HOLD", None, "123456"))

    def test_a_reading_older_than_an_action_is_ignored(self):
        self.at(2 * MIN)
        self.console.answer()
        self.at(2 * MIN + 1000)  # pins 9001
        self.at(2 * MIN + 40_000, seconds=40)  # periodic read starts
        read = self.console.reads.pop(0)
        self.at(10 * MIN, seconds=1)  # no new read while one is on its way
        self.assertEqual(self.console.calls[-1], ("PIN_PRODUCT", "9002"))
        read(True, "9001", None)  # taken before the pin of 9002
        self.assertEqual((self.scheduler.status()["mode"], self.scheduler.status()["actual_product_id"]), ("TIMELINE", 2))

    def test_reads_are_spaced_and_a_failed_one_changes_nothing(self):
        for second in range(10):
            self.at(30_000 + second * 1000)
        self.assertEqual(len(self.console.reads), 1)
        self.console.answer(ok=False)
        self.assertFalse(self.scheduler.status()["actual_known"])
        self.at(45_000, seconds=19)  # 29 s after the first read
        self.assertEqual(len(self.console.reads), 0)
        self.at(46_000, seconds=2)
        self.assertEqual(len(self.console.reads), 1)

    def test_nothing_is_read_while_live_is_not_confirmed(self):
        self.scheduler.gate = Gate(None)
        self.at(2 * MIN, seconds=60)
        self.assertEqual(self.console.reads, [])

    def test_web_manual_pin_with_return_at_the_next_marker(self):
        self.at(2 * MIN)
        self.console.answer()
        self.at(2 * MIN + 1000)
        self.scheduler.manual_pin(3, RESUME_AT_NEXT_MARKER)
        self.console.pinned = "9003"
        self.at(5 * MIN, seconds=40)
        while self.console.reads:
            self.console.answer()
        self.assertEqual(self.overrides()[-1], {"product_id": 3, "policy": RESUME_AT_NEXT_MARKER})
        self.at(10 * MIN)
        self.assertEqual(self.console.calls[-1], ("PIN_PRODUCT", "9002"))


class DryRunReadTests(unittest.TestCase):
    def test_dry_run_reads_its_own_simulated_pin(self):
        executor, results = DryRunPinExecutor(), []
        executor.read(lambda ok, pinned, error: results.append(pinned))
        executor.submit("PIN_PRODUCT", product(2), lambda: True, lambda ok, err: None)
        executor.read(lambda ok, pinned, error: results.append(pinned))
        self.assertEqual(results, [None, "9002"])


class ConsoleReadTests(unittest.TestCase):
    def test_console_read_maps_the_pin_state(self):
        futures = []
        auto_pin = SimpleNamespace(submit_pin_state=lambda: futures.append(Future()) or futures[-1])
        executor, results = ConsolePinExecutor(auto_pin), []
        for _ in range(3):
            executor.read(lambda *args: results.append(args))
        futures[0].set_result({"pinned_product_id": "9001", "multiple_pins": False})
        futures[1].set_result({"pinned_product_id": None, "multiple_pins": True})
        futures[2].set_exception(RuntimeError("LIVE Console chưa mở"))
        self.assertEqual(results, [(True, "9001", None), (False, None, "Nhiều product card đang ghim"),
                                   (False, None, "LIVE Console chưa mở")])


class RouterHoldTests(EngineTestBase):
    def test_hold_and_resume_commands(self):
        scheduler = TimelineScheduler(DryRunPinExecutor(), Gate(), clock=Clock())
        router = CommandRouter(playback=self.engine, monitor=None, voice=None, timeline=scheduler)
        self.assertEqual((lane("timeline_hold"), lane("timeline_resume"), lane("PIN_PRODUCT")),
                         ("background", "background", "background"), "the hold runs before the pin it announces")
        router.execute("video_play", {"session_id": 7, "media_id": self.files["pl1"], "timeline": timeline(PLAN)})
        held = router.execute("timeline_hold", {"session_id": 7, "product_id": 3, "policy": "RESUME_AT_NEXT_MARKER"})
        self.assertEqual((held["mode"], held["manual_policy"]), ("MANUAL_HOLD", "RESUME_AT_NEXT_MARKER"))
        self.assertEqual(router.execute("timeline_resume", {"session_id": 7})["mode"], "TIMELINE")
        with self.assertRaises(AgentError):
            router.execute("timeline_hold", {"session_id": 8})
        with self.assertRaises(AgentError):
            router.execute("timeline_hold", {"session_id": 7, "policy": "FOREVER"})


if __name__ == "__main__":
    unittest.main()
