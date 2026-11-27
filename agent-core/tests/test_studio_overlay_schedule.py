"""Overlay schedules of a LIVE session (dev LIVE_OVERLAYS.md §13–14): fixed / window / random, show_ms, pin spans
from the video's markers, the snapshot the Agent plans from, and the planned-mode checks."""
from __future__ import annotations

import shutil
import sqlite3
import subprocess

import pytest

from fake_obs import FakeWs
from sarae_agent.overlay import schedule
from sarae_agent.studio import storage as storage_module
from sarae_agent.studio.live import StudioLive
from sarae_agent.studio.service import StudioError, StudioService
from sarae_agent.studio.transport import METHODS
from studio_helpers import create_product

FIXED_KEYS = {"canvas", "overlays", "products", "assets", "revision", "version", "timeline_id"}
PLANNED_KEYS = FIXED_KEYS | {"schedule", "pins", "durations"}


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setenv("SARAE_AGENT_HOME", str(tmp_path / "agent-must-not-be-used"))
    monkeypatch.delenv("SARAE_STUDIO_DATA_DIR", raising=False)


@pytest.fixture
def clip(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe are required to generate and validate real video")
    path = tmp_path / "clip60.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=32x32:r=5:d=60", "-an", "-c:v", "mpeg4", str(path)],
                   check=True, capture_output=True, timeout=60)
    return path


@pytest.fixture
def studio(tmp_path):
    instance = StudioService(data_dir=tmp_path / "studio")
    yield instance
    instance.close()


def video(studio, clip):
    return studio.dispatch("studio.media.import", {"source_path": str(clip)})["media"]


def overlay(start_ms=1000, duration_ms=3000, product=None, **changes):
    item = {"template": "PRODUCT_INFO", "label": "Giá", "start_ms": start_ms, "duration_ms": duration_ms,
            "product_id": product["id"] if product else None,
            "layout": {"x": 0.05, "y": 0.7, "width": 0.9, "height": 0.12, "z_index": 1, "opacity": 1},
            "elements": [{"kind": "shape", "fill": "#000000A0", "radius": 0.2},
                         ({"kind": "product_field", "field": "price", "suffix": "đ"} if product
                          else {"kind": "text", "text": "Freeship"})]}
    item.update(changes)
    return item


def save(studio, media_id, *items):
    return studio.dispatch("studio.media.overlays.set", {"media_id": media_id, "overlays": list(items)})


def markers(studio, media_id, *items):
    studio.dispatch("studio.media.markers.set", {"media_id": media_id, "markers": [
        {"at_ms": at, "action": "PIN_PRODUCT", "product_id": p["id"]} if p else {"at_ms": at, "action": "UNPIN_PRODUCT"}
        for at, p in items]})


def session_of(studio, media_ids, **changes):
    return studio.save_live_session(None, {"title": "Phiên", "video_ids": media_ids, **changes})


def error_code(operation) -> str:
    with pytest.raises(StudioError) as caught:
        operation()
    return caught.value.code


def test_migration_9_adds_the_schedule_with_dev_defaults(tmp_path, monkeypatch):
    root = tmp_path / "studio"
    with monkeypatch.context() as patch:  # a database of the previous version, with a session
        patch.setattr(storage_module, "MIGRATIONS", storage_module.MIGRATIONS[:8])
        old = StudioService(data_dir=root)
        old._storage.connection.execute("INSERT INTO live_sessions(id, title, created_at, updated_at) "
                                        "VALUES ('00000000-0000-4000-8000-000000000001', 'Cũ', 'x', 'x')")
        old.close()
    service = StudioService(data_dir=root)
    try:
        session = service.live_session("00000000-0000-4000-8000-000000000001")
        assert (session["overlay_mode"], session["overlay_every_ms"], session["overlay_min_gap_ms"]) == ("fixed", 120_000, 20_000)
        with pytest.raises(sqlite3.IntegrityError):  # the database refuses what validation refuses
            service._storage.connection.execute("UPDATE live_sessions SET overlay_mode = 'chaos'")
    finally:
        service.close()


@pytest.mark.parametrize("bad", [
    {"overlay_mode": "shuffle"}, {"overlay_every_ms": 9999}, {"overlay_every_ms": 3_600_001},
    {"overlay_every_ms": True}, {"overlay_every_ms": 20000.5}, {"overlay_min_gap_ms": -1},
    {"overlay_min_gap_ms": 600_001},
])
def test_schedule_fields_are_validated_with_dev_limits(studio, clip, bad):
    media = video(studio, clip)
    assert error_code(lambda: session_of(studio, [media["id"]], **bad)) == "VALIDATION_ERROR"


def test_schedule_fields_are_saved_and_editable(studio, clip):
    media = video(studio, clip)
    session = session_of(studio, [media["id"]], overlay_mode="window", overlay_every_ms=10_000, overlay_min_gap_ms=0)
    assert (session["overlay_mode"], session["overlay_every_ms"], session["overlay_min_gap_ms"]) == ("window", 10_000, 0)
    updated = studio.save_live_session(session["id"], {"overlay_mode": "random", "overlay_every_ms": 3_600_000,
                                                       "overlay_min_gap_ms": 600_000})
    assert (updated["overlay_mode"], updated["overlay_every_ms"], updated["overlay_min_gap_ms"]) == ("random", 3_600_000, 600_000)


@pytest.mark.parametrize("show_ms, ok", [(999, False), (1000, True), (600_000, True), (600_001, False)])
def test_show_ms_is_kept_and_validated(studio, clip, show_ms, ok):
    media = video(studio, clip)
    if ok:
        assert save(studio, media["id"], overlay(show_ms=show_ms))["overlays"][0]["show_ms"] == show_ms
    else:
        assert error_code(lambda: save(studio, media["id"], overlay(show_ms=show_ms))) == "VALIDATION_ERROR"


def test_show_ms_defaults_to_dev_value(studio, clip):
    media = video(studio, clip)
    assert save(studio, media["id"], overlay())["overlays"][0]["show_ms"] == 6000


def test_fixed_snapshot_keeps_its_shape(studio, clip):
    media = video(studio, clip)
    save(studio, media["id"], overlay())
    session = session_of(studio, [media["id"]])
    snapshot = studio.overlay_snapshot(session)
    assert set(snapshot) == FIXED_KEYS and snapshot["timeline_id"] is None
    assert snapshot == studio.overlay_snapshot([media["id"]])  # same as before sessions had a schedule


def test_planned_snapshots_add_schedule_pins_and_durations(studio, clip):
    a, b = create_product(studio, "A"), create_product(studio, "B")
    media = video(studio, clip)
    save(studio, media["id"], overlay(product=a))
    markers(studio, media["id"], (10_000, a), (20_000, a), (30_000, None), (40_000, b))
    session = session_of(studio, [media["id"]], overlay_mode="window", overlay_every_ms=15_000)
    window = studio.overlay_snapshot(session)
    assert set(window) == PLANNED_KEYS
    assert window["schedule"] == {"mode": "window", "every_ms": 15_000, "min_gap_ms": 20_000}
    assert window["timeline_id"] == schedule.seed_of(session["id"]) and isinstance(window["timeline_id"], int)
    duration = window["durations"][media["id"]]
    assert 59_000 <= duration <= 61_000
    # Re-pin of the same product continues the span; an unpin ends it; nothing before the first marker.
    assert window["pins"] == {media["id"]: [{"start_ms": 10_000, "end_ms": 30_000, "product_id": a["id"]},
                                            {"start_ms": 40_000, "end_ms": duration, "product_id": b["id"]}]}
    session = studio.save_live_session(session["id"], {"overlay_mode": "random"})
    random = studio.overlay_snapshot(session)
    assert set(random) == PLANNED_KEYS and random["schedule"]["mode"] == "random"
    assert random["revision"] != window["revision"]  # a live change reaches the OverlayService's next sync
    markers(studio, media["id"], (10_000, a), (20_000, b), (25_000, a))
    assert studio.overlay_snapshot(session)["pins"][media["id"]] == [
        {"start_ms": 10_000, "end_ms": 20_000, "product_id": a["id"]},
        {"start_ms": 20_000, "end_ms": 25_000, "product_id": b["id"]},
        {"start_ms": 25_000, "end_ms": duration, "product_id": a["id"]}]
    assert studio.overlay_snapshot(session)["revision"] != random["revision"]


def test_agent_plan_of_random_snapshot_stays_inside_the_pinned_span(studio, clip):
    a, b = create_product(studio, "A"), create_product(studio, "B")
    media = video(studio, clip)
    save(studio, media["id"], overlay(product=a, show_ms=2000, label="A"), overlay(product=b, show_ms=2000, label="B"),
         overlay(show_ms=2000, label="Freeship"))
    markers(studio, media["id"], (15_000, a), (35_000, None), (45_000, b))
    session = session_of(studio, [media["id"]], overlay_mode="random", overlay_every_ms=10_000, overlay_min_gap_ms=0)
    snapshot = studio.overlay_snapshot(session)
    seen = set()
    for iteration in range(20):
        plan = schedule.plan_of(snapshot, media["id"], iteration)
        assert plan and plan == schedule.plan_of(snapshot, media["id"], iteration)  # pure
        for appearance in plan:
            label = snapshot["overlays"][appearance["index"]]["label"]
            seen.add(label)
            if label == "A":
                assert 15_000 <= appearance["start_ms"] and appearance["end_ms"] <= 35_000
            elif label == "B":
                assert 45_000 <= appearance["start_ms"]
            assert appearance["end_ms"] <= snapshot["durations"][media["id"]]
    assert seen == {"A", "B", "Freeship"}


def test_window_mode_issues(studio, clip):
    media = video(studio, clip)
    save(studio, media["id"],
         overlay(0, 5000, show_ms=8000),                                            # longer than its window
         overlay(0, 20_000, show_ms=15_000),                                        # longer than a slot
         overlay(25_000, 5000, show_ms=1200, animation_in={"name": "fade", "duration_ms": 700},
                 animation_out={"name": "fade", "duration_ms": 700}))              # animations longer than a show
    session = session_of(studio, [media["id"]], overlay_mode="window", overlay_every_ms=10_000)
    result = studio.dispatch("studio.live.sessions.overlay_issues", {"id": session["id"]})
    assert result["schedule"] == {"mode": "window", "every_ms": 10_000, "min_gap_ms": 20_000}
    codes = {(i["code"], i.get("overlay")) for i in result["issues"]}
    assert {("SHOW_LONGER_THAN_WINDOW", 0), ("SHOW_LONGER_THAN_SLOT", 1), ("ANIMATION_LONGER_THAN_SHOW", 2),
            ("DENSITY_TOO_HIGH", None), ("NO_OVERLAY_IN_SLOTS", None)} <= codes
    errors = [i for i in result["issues"] if i["level"] == "error"]
    assert all(i["media_id"] == media["id"] for i in errors)
    empty = next(i for i in result["issues"] if i["code"] == "NO_OVERLAY_IN_SLOTS")
    # Slots 0–10 s and 10–20 s: nothing fits (#0 longer than its window, #1 than a slot, #2 shows from 25 s).
    assert empty["media_id"] == media["id"] and empty["slots"] == 2


def test_random_mode_issues_and_fixed_has_none(studio, clip):
    a, b, c = create_product(studio, "A"), create_product(studio, "B"), create_product(studio, "C")
    media = video(studio, clip)
    save(studio, media["id"], overlay(product=a, show_ms=2000), overlay(product=b, show_ms=2000),
         overlay(product=c, show_ms=2000), overlay(show_ms=2000))
    markers(studio, media["id"], (10_000, a), (11_000, b), (12_000, None))
    session = session_of(studio, [media["id"]], overlay_mode="random", overlay_every_ms=10_000, overlay_min_gap_ms=0)
    issues = studio.session_overlay_issues(session["id"])["issues"]
    assert {(i["code"], i.get("overlay")) for i in issues} == {("PIN_TOO_SHORT", 0), ("PIN_TOO_SHORT", 1),
                                                                ("PRODUCT_NEVER_PINNED", 2)}
    studio.save_live_session(session["id"], {"overlay_mode": "fixed"})
    assert studio.session_overlay_issues(session["id"]) == {
        "schedule": {"mode": "fixed", "every_ms": 10_000, "min_gap_ms": 0}, "issues": []}
    assert "studio.live.sessions.overlay_issues" in METHODS
    assert error_code(lambda: studio.dispatch("studio.live.sessions.overlay_issues", {"id": "nope"})) in {
        "VALIDATION_ERROR", "NOT_FOUND"}


def test_live_session_schedule_reaches_the_agent_snapshot(tmp_path, clip):
    service = StudioService(data_dir=tmp_path / "studio")
    live = StudioLive(service, obs_client_factory=lambda cfg, password: FakeWs())
    try:
        media = video(service, clip)
        save(service, media["id"], overlay())
        session = live.sessions_create({"title": "Phiên", "video_ids": [media["id"]]})["session"]
        live.start({"session_id": session["id"]})
        fixed = live._overlay_snapshot(session["id"])
        assert set(fixed) == FIXED_KEYS
        syncs = []
        live.overlay.request_sync = lambda: syncs.append(True)
        updated = live.sessions_update({"id": session["id"], "overlay_mode": "window", "overlay_every_ms": 10_000})
        assert updated["session"]["overlay_mode"] == "window"
        assert syncs, "a schedule change while live must reach OBS at once"
        planned = live._overlay_snapshot(session["id"])
        assert set(planned) == PLANNED_KEYS and planned["revision"] != fixed["revision"]
        live.stop()
    finally:
        live.close()
        service.close()
