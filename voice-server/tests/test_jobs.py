"""Phase 2 with FakeEngine: job lifecycle, idempotency, result cache, scheduler, admission, read tokens, operation."""
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH, make_wav, put_voice, settings_for, wait_ready
from sarae_voice.api import create_app
from sarae_voice.engine import FakeEngine
from sarae_voice.instance import InstanceLock

TEXT = "Dạ có size Mờ nha chị."


def job(c, text=TEXT, priority="live", key=None, **extra):
    headers = {**AUTH, **({"Idempotency-Key": key} if key else {})}
    body = {"voice_id": "shop_main", "text": text, "priority": priority, "reader_agent_id": "agent-1", **extra}
    return c.post("/v1/tts/jobs", headers=headers, json=body)


def wait_state(c, job_id, states=("done", "failed", "cancelled", "expired"), timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = c.get(f"/v1/tts/jobs/{job_id}", headers=AUTH, params={"wait": 1}).json()
        if body.get("state") in states:
            return body
    raise AssertionError(f"job {job_id} still {body.get('state')}")


def wait_running(c, job_id, timeout=5.0):
    return wait_state(c, job_id, ("running",), timeout)


def started(c) -> list[str]:
    """Job ids in the order the GPU worker started them."""
    return [label.split(":", 1)[1] for _, label in list(c.app.state.gate.order) if label.startswith("job:")]


def audio(c, job_id, token, agent="agent-1", **params):
    headers = {"Authorization": f"Bearer {token}", **({"X-Sarae-Agent-Id": agent} if agent else {})}
    return c.get(f"/v1/tts/jobs/{job_id}/audio", headers=headers, params=params)


@pytest.fixture
def svc(app_factory):
    c, engine = app_factory()
    wait_ready(c)
    put_voice(c)
    return c, engine


@pytest.fixture
def blocked(app_factory):
    """A service whose GPU calls wait for engine.block: lets tests line jobs up deterministically."""
    def make(**overrides):
        engine = FakeEngine()
        c, _ = app_factory(engine, **overrides)
        wait_ready(c)
        put_voice(c)
        engine.block = threading.Event()
        return c, engine
    return make


def release(engine):
    engine.block.set()


# --- lifecycle -------------------------------------------------------------------------------------
def test_job_lifecycle_and_read_token(svc):
    c, engine = svc
    r = job(c)
    assert r.status_code == 202
    body = r.json()
    assert body["created"] == "new" and body["read_token"] and body["state"] in ("queued", "running", "done")
    assert body["generation_config"]["num_step"] == 32 and body["voice_rev"]
    a = audio(c, body["job_id"], body["read_token"], wait=5)
    assert a.status_code == 200 and a.headers["content-type"] == "audio/wav" and a.headers["X-Source"] == "generated"
    view = c.get(f"/v1/tts/jobs/{body['job_id']}", headers=AUTH).json()
    assert view["state"] == "done" and view["reads"] == 1 and "read_token" not in view
    assert view["timings"]["inference_ms"] >= 0 and view["audio"]["sample_rate"] == 24000


def test_orchestrator_can_read_audio_without_read_token(svc):
    c, _ = svc
    jid = job(c, reader_agent_id=None).json()["job_id"]
    r = c.get(f"/v1/tts/jobs/{jid}/audio", headers=AUTH, params={"wait": 5})
    assert r.status_code == 200


def test_text_is_only_stripped_like_the_demo(svc):
    c, engine = svc
    raw = "  Dạ  có\nsize   Mờ nha.  "
    jid = job(c, text=raw).json()["job_id"]
    wait_state(c, jid)
    assert engine.synth_texts[-1] == raw.strip()
    c.post("/v1/tts/synthesize", headers=AUTH, json={"voice_id": "shop_main", "text": raw})
    assert engine.synth_texts[-1] == raw.strip()


def test_audio_202_while_running(blocked):
    c, engine = blocked()
    body = job(c).json()
    wait_running(c, body["job_id"])
    r = audio(c, body["job_id"], body["read_token"])
    assert r.status_code == 202 and r.json()["state"] == "running" and r.headers["Retry-After"] == "1"
    release(engine)
    assert audio(c, body["job_id"], body["read_token"], wait=5).status_code == 200


# --- idempotency and result cache ------------------------------------------------------------------
def test_same_key_same_request_is_one_job(svc):
    c, engine = svc
    first = job(c, key="utt-1")
    second = job(c, key="utt-1", deadline_at=(datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat())
    assert first.status_code == 202 and second.status_code == 200
    assert second.json()["created"] == "replay" and second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["read_token"]
    wait_state(c, first.json()["job_id"])
    assert engine.calls["synthesize"] == 1


def test_same_key_other_request_conflicts(svc):
    c, _ = svc
    job(c, key="utt-2")
    r = job(c, key="utt-2", text="Một câu khác nha.")
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert job(c, key="utt-2", reader_agent_id="agent-2").status_code == 409


def test_result_cache_reuses_identical_audio_only(svc):
    c, engine = svc
    first = job(c).json()
    wait_state(c, first["job_id"])
    again = job(c).json()
    assert again["created"] == "result_cache" and again["state"] == "done" and again["source"] == "result_cache"
    assert engine.calls["synthesize"] == 1
    assert audio(c, again["job_id"], again["read_token"]).content == audio(c, first["job_id"], first["read_token"]).content
    other_seed = job(c, seed=7).json()
    assert other_seed["created"] == "new"
    status = c.get("/v1/status", headers=AUTH).json()["result_cache"]
    assert status["hit"] == 1 and status["miss"] >= 2


def test_result_cache_not_reused_after_checkpoint_change(app_factory):
    c1, e1 = app_factory()
    wait_ready(c1)
    put_voice(c1)
    wait_state(c1, job(c1).json()["job_id"])
    c2, e2 = app_factory(FakeEngine(revision="another-rev"))
    wait_ready(c2)
    assert job(c2).json()["created"] == "new"


def test_voice_owner_isolation(app_factory):
    c, _ = app_factory()
    wait_ready(c)
    put_voice(c, owner_ref="owner-a")
    r = job(c, owner_ref="owner-b")
    assert r.status_code == 404 and r.json()["error"]["code"] == "VOICE_NOT_FOUND"
    assert job(c).status_code == 404
    ok = job(c, owner_ref="owner-a").json()
    wait_state(c, ok["job_id"])
    assert job(c, owner_ref="owner-a").json()["created"] == "result_cache"


# --- scheduler ------------------------------------------------------------------------------------
def test_priority_order(blocked):
    c, engine = blocked()
    first = job(c, text="đầu tiên nha.").json()
    wait_running(c, first["job_id"])
    labels = {}
    for prio, text in (("long", "dài nha."), ("prepare", "chuẩn bị nha."), ("preview", "nghe thử nha."),
                       ("live", "trực tiếp nha.")):
        labels[job(c, text=text, priority=prio).json()["job_id"]] = prio
    release(engine)
    for jid in labels:
        wait_state(c, jid)
    assert [labels.get(j) for j in started(c)][1:] == ["live", "preview", "prepare", "long"]


def test_starving_prepare_job_gets_a_fair_turn(blocked):
    c, engine = blocked(starvation_s=0.3, fair_every=4)
    first = job(c, text="đầu tiên nha.").json()
    wait_running(c, first["job_id"])
    prep = job(c, text="chuẩn bị nha.", priority="prepare").json()["job_id"]
    time.sleep(0.6)                        # prepare is now starving
    lives = [job(c, text=f"trực tiếp {i} nha.").json()["job_id"] for i in range(6)]
    release(engine)
    for j in [prep, *lives]:
        wait_state(c, j)
    order = [j for j in started(c) if j in (prep, *lives)]
    assert order == lives[:4] + [prep] + lives[4:]         # one fair turn after 4 live starts, not before
    assert c.get("/v1/status", headers=AUTH).json()["gate"]["fair_turns"] == 1


def test_fresh_lower_priority_jobs_never_jump_ahead_of_live(blocked):
    c, engine = blocked(starvation_s=30)
    first = job(c, text="đầu tiên nha.").json()
    wait_running(c, first["job_id"])
    prep = [job(c, text=f"chuẩn bị {i} nha.", priority="prepare").json()["job_id"] for i in range(5)]
    lives = [job(c, text=f"trực tiếp {i} nha.").json()["job_id"] for i in range(5)]
    release(engine)
    for j in prep + lives:
        wait_state(c, j)
    order = [j for j in started(c) if j in prep + lives]
    assert order == lives + prep


def test_queue_full_per_class(blocked):
    c, engine = blocked(queue_live=2)
    running = job(c, text="đang chạy nha.").json()["job_id"]
    wait_running(c, running)
    assert job(c, text="chờ một nha.").status_code == 202
    assert job(c, text="chờ hai nha.").status_code == 202
    full = job(c, text="chờ ba nha.")
    assert full.status_code == 429 and full.json()["error"]["code"] == "TTS_BUSY" and full.headers["Retry-After"]
    assert job(c, text="chuẩn bị nha.", priority="prepare").status_code == 202    # other classes unaffected
    release(engine)


def test_many_concurrent_jobs_one_gpu_lane_priority_and_fifo(blocked):
    c, engine = blocked(queue_live=20, queue_prepare=20, queue_preview=20)
    head = job(c, text="mở đầu nha.").json()["job_id"]
    wait_running(c, head)
    created = []
    for i in range(36):
        prio = ("live", "preview", "prepare")[i % 3]
        created.append((job(c, text=f"câu số {i} nha.", priority=prio).json()["job_id"], prio, i))
    release(engine)
    final = {jid: wait_state(c, jid, timeout=20) for jid, _, _ in created}
    assert all(v["state"] == "done" for v in final.values())
    assert c.app.state.gate.max_active_seen == 1 and engine.max_active == 1
    order = started(c)
    by_start = sorted(created, key=lambda t: order.index(t[0]))
    prios = [p for _, p, _ in by_start]
    assert prios == ["live"] * 12 + ["preview"] * 12 + ["prepare"] * 12
    for p in ("live", "preview", "prepare"):
        seq = [i for _, prio, i in by_start if prio == p]
        assert seq == sorted(seq)                   # FIFO inside a class


# --- cancel and deadline ----------------------------------------------------------------------------
def test_cancel_queued_job_never_runs(blocked):
    c, engine = blocked()
    head = job(c, text="đang chạy nha.").json()["job_id"]
    wait_running(c, head)
    queued = job(c, text="sẽ bị hủy nha.").json()
    r = c.delete(f"/v1/tts/jobs/{queued['job_id']}", headers=AUTH)
    assert r.status_code == 200
    release(engine)
    assert wait_state(c, queued["job_id"])["state"] == "cancelled"
    wait_state(c, head)
    assert "sẽ bị hủy nha." not in engine.synth_texts
    assert audio(c, queued["job_id"], queued["read_token"]).status_code == 410


def test_cancel_running_job_discards_audio(blocked):
    c, engine = blocked()
    body = job(c).json()
    wait_running(c, body["job_id"])
    view = c.delete(f"/v1/tts/jobs/{body['job_id']}", headers=AUTH).json()
    assert view["state"] == "running" and view["cancel_requested"] is True
    release(engine)
    final = wait_state(c, body["job_id"])
    assert final["state"] == "cancelled" and final["audio"] is None
    r = audio(c, body["job_id"], body["read_token"])
    assert r.status_code == 410 and r.json()["error"]["code"] == "JOB_CANCELLED"
    assert c.get("/v1/status", headers=AUTH).json()["discarded_after_inference"] == 1
    assert job(c).json()["created"] == "new"            # discarded audio never enters the result cache


def test_cancel_done_job_revokes_audio(svc):
    c, _ = svc
    body = job(c).json()
    wait_state(c, body["job_id"])
    assert c.delete(f"/v1/tts/jobs/{body['job_id']}", headers=AUTH).json()["state"] == "cancelled"
    assert audio(c, body["job_id"], body["read_token"]).status_code == 410


def test_cancel_by_tag(blocked):
    c, engine = blocked()
    head = job(c, text="đang chạy nha.", tags=["session:9"]).json()["job_id"]
    wait_running(c, head)
    ids = [job(c, text=f"câu {i} nha.", tags=["session:9"]).json()["job_id"] for i in range(3)]
    other = job(c, text="phiên khác nha.", tags=["session:10"]).json()["job_id"]
    assert c.delete("/v1/tts/jobs", headers=AUTH, params={"tag": "session:9"}).json()["cancelled"] == 4
    release(engine)
    assert [wait_state(c, j)["state"] for j in ids] == ["cancelled"] * 3
    assert wait_state(c, head)["state"] == "cancelled" and wait_state(c, other)["state"] == "done"


def test_expired_job_never_starts(blocked):
    c, engine = blocked()
    head = job(c, text="đang chạy nha.").json()["job_id"]
    wait_running(c, head)
    soon = (datetime.now(timezone.utc) + timedelta(seconds=0.4)).isoformat()
    late = job(c, text="quá hạn nha.", deadline_at=soon).json()
    time.sleep(0.8)
    release(engine)
    final = wait_state(c, late["job_id"])
    assert final["state"] == "expired" and final["error"]["code"] == "TTS_EXPIRED"
    assert "quá hạn nha." not in engine.synth_texts
    assert audio(c, late["job_id"], late["read_token"]).json()["error"]["code"] == "JOB_EXPIRED"


def test_deadline_validation(svc):
    c, _ = svc
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert job(c, deadline_at=past).json()["error"]["code"] == "DEADLINE_PASSED"
    assert job(c, deadline_at="2030-01-01T00:00:00").json()["error"]["code"] == "INVALID_PARAMS"   # no timezone
    far = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    assert job(c, deadline_at=far).status_code == 422
    assert job(c, text="a" * 451).json()["error"]["code"] == "TEXT_TOO_LONG"
    assert job(c, text="a" * 451, priority="long").status_code == 202


# --- voice snapshot ----------------------------------------------------------------------------------
def test_voice_change_while_queued_keeps_the_snapshot(blocked):
    c, engine = blocked()
    head = job(c, text="đang chạy nha.").json()["job_id"]
    wait_running(c, head)
    queued = job(c, text="giọng cũ nha.").json()
    old_rev = queued["voice_rev"]
    change = threading.Thread(target=lambda: put_voice(c, transcript="Giọng mới hoàn toàn."))
    change.start()
    time.sleep(0.2)
    release(engine)
    change.join(10)
    final = wait_state(c, queued["job_id"])
    i = engine.synth_texts.index("giọng cũ nha.")
    assert final["state"] == "done" and final["voice_rev"] == old_rev
    assert engine.synth_prompts[i] == "Đây là giọng nói mẫu của shop."           # the old prompt
    assert c.get("/v1/voices/shop_main", headers=AUTH).json()["rev"] != old_rev
    assert job(c, text="giọng cũ nha.").json()["voice_rev"] != old_rev           # new jobs use the new voice


# --- LIVE admission ----------------------------------------------------------------------------------
def test_long_job_running_before_live(blocked):
    c, engine = blocked()
    long_job = job(c, text="đoạn dài " * 40, priority="long").json()["job_id"]
    wait_running(c, long_job)
    queued_long = job(c, text="đoạn dài thứ hai " * 30, priority="long").json()["job_id"]
    lease = c.put("/v1/admission/live", headers=AUTH, json={"owner_ref": "o1", "session_id": "s1", "ttl_s": 60}).json()
    assert lease["live_active"] is True and lease["ready_for_live"] is False and lease["reason"] == "LONG_JOB_RUNNING"
    assert lease["gpu"]["running"]["class"] == "long" and lease["gpu"]["running"]["estimated_remaining_s"] is not None
    rejected = job(c, text="đoạn dài mới " * 30, priority="long")
    assert rejected.status_code == 423 and rejected.json()["error"]["code"] == "LIVE_ACTIVE"
    assert job(c, text="chuẩn bị " * 48, priority="prepare").status_code == 423       # ~23 s estimate during LIVE
    live = job(c, text="trực tiếp nha.").json()["job_id"]
    release(engine)
    assert wait_state(c, live)["state"] == "done"
    time.sleep(0.5)
    held = c.get(f"/v1/tts/jobs/{queued_long}", headers=AUTH).json()
    assert held["state"] == "queued"                                  # held while LIVE is on
    c.delete("/v1/admission/live", headers=AUTH, params={"owner_ref": "o1", "session_id": "s1"})
    assert wait_state(c, queued_long)["state"] == "done"


def test_multiple_leases_and_expiry(svc):
    c, _ = svc
    for s in ("s1", "s2"):
        c.put("/v1/admission/live", headers=AUTH, json={"owner_ref": "o1", "session_id": s, "ttl_s": 30})
    c.put("/v1/admission/live", headers=AUTH, json={"owner_ref": "o2", "session_id": "s1", "ttl_s": 30})
    view = c.get("/v1/admission/live", headers=AUTH).json()
    assert len(view["leases"]) == 3 and view["ready_for_live"] is True
    renewed = c.put("/v1/admission/live", headers=AUTH, json={"owner_ref": "o1", "session_id": "s1", "ttl_s": 30}).json()
    assert next(l for l in renewed["leases"] if l["session_id"] == "s1" and l["owner_ref"] == "o1")["renewals"] == 1
    assert c.delete("/v1/admission/live", headers=AUTH, params={"owner_ref": "o1", "session_id": "s2"}).json()["removed"]
    adm = c.app.state.admission
    for lease in adm._leases.values():
        lease["expires"] = time.monotonic() - 1        # not renewed: expired
    assert c.get("/v1/admission/live", headers=AUTH).json()["live_active"] is False
    assert job(c, text="đoạn dài " * 40, priority="long").status_code == 202
    assert c.put("/v1/admission/live", headers=AUTH, json={"owner_ref": "o", "session_id": "s", "ttl_s": 1}).status_code == 422


# --- read tokens ---------------------------------------------------------------------------------------
def test_read_token_scope(svc):
    c, _ = svc
    a = job(c, text="câu A nha.").json()
    b = job(c, text="câu B nha.").json()
    wait_state(c, a["job_id"])
    wait_state(c, b["job_id"])
    assert audio(c, b["job_id"], a["read_token"]).json()["error"]["code"] == "TOKEN_SCOPE"       # other job
    assert audio(c, a["job_id"], a["read_token"], agent="agent-2").json()["error"]["code"] == "TOKEN_SCOPE"
    assert audio(c, a["job_id"], a["read_token"], agent=None).status_code == 403             # header required
    tampered = a["read_token"][:-2] + ("AA" if not a["read_token"].endswith("AA") else "BB")
    assert audio(c, a["job_id"], tampered).status_code == 401
    expired = c.app.state.jobs.tokens.issue(a["job_id"], "agent-1", time.time() - 1)
    assert audio(c, a["job_id"], expired).json()["error"]["code"] == "TOKEN_EXPIRED"
    assert c.get(f"/v1/tts/jobs/{a['job_id']}/audio").status_code == 401
    assert audio(c, a["job_id"], "not-a-token").status_code == 401
    # a read token is not an orchestrator token
    assert c.get(f"/v1/tts/jobs/{a['job_id']}", headers={"Authorization": f"Bearer {a['read_token']}"}).status_code == 401
    assert a["read_token"] not in c.get("/v1/status", headers=AUTH).text
    assert a["read_token"] not in c.get("/metrics", headers=AUTH).text


# --- failures, restart, housekeeping -------------------------------------------------------------------
def test_oom_job_fails_and_service_stays_ready(svc):
    c, engine = svc
    engine.fail_next = "oom"
    body = job(c, text="lỗi bộ nhớ nha.").json()
    final = wait_state(c, body["job_id"])
    assert final["state"] == "failed" and final["error"]["code"] == "TTS_OOM"
    assert audio(c, body["job_id"], body["read_token"]).json()["error"]["code"] == "JOB_FAILED"
    assert wait_state(c, job(c).json()["job_id"])["state"] == "done"
    assert c.get("/health/ready").status_code == 200


def test_output_size_limit(app_factory):
    c, _ = app_factory(max_output_bytes=1000)
    wait_ready(c)
    put_voice(c)
    final = wait_state(c, job(c).json()["job_id"])
    assert final["state"] == "failed" and final["error"]["code"] == "OUTPUT_TOO_LARGE"


def test_jobs_from_an_earlier_run_are_lost_not_replayed(app_factory):
    c1, e1 = app_factory()
    wait_ready(c1)
    put_voice(c1)
    old = job(c1, key="utt-9").json()
    wait_state(c1, old["job_id"])
    c2, e2 = app_factory()
    wait_ready(c2)
    r = c2.get(f"/v1/tts/jobs/{old['job_id']}", headers=AUTH)
    assert r.status_code == 410 and r.json()["error"]["code"] == "JOB_LOST"
    assert audio(c2, old["job_id"], old["read_token"]).status_code == 401      # tokens of the old run are invalid
    assert e2.calls["synthesize"] == 0
    assert c2.get("/v1/tts/jobs/abc", headers=AUTH).json()["error"]["code"] == "JOB_NOT_FOUND"


def test_cleanup_purges_jobs_and_result_files(app_factory, tmp_path):
    c, _ = app_factory(job_retention_s=0, result_ttl_s=0.2, cleanup_interval_s=0.5)
    wait_ready(c)
    put_voice(c)
    body = job(c, deadline_at=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()).json()
    wait_state(c, body["job_id"])
    results = tmp_path / "data" / "results"
    assert list(results.glob("*.wav"))
    time.sleep(3.5)
    r = c.get(f"/v1/tts/jobs/{body['job_id']}", headers=AUTH)
    assert r.status_code == 410 and r.json()["error"]["code"] == "JOB_GONE"
    assert not list(results.glob("*.wav")) and not list(results.glob("*.json"))
    assert c.get("/v1/status", headers=AUTH).json()["generated_not_consumed"] == 1


def test_result_cache_size_cap(app_factory, tmp_path):
    c, _ = app_factory(result_max_bytes=1, job_retention_s=0, cleanup_interval_s=0.5)
    wait_ready(c)
    put_voice(c)
    soon = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    for i in range(3):
        wait_state(c, job(c, text=f"câu {i} nha.", deadline_at=soon).json()["job_id"])
    time.sleep(3.5)
    assert not list((tmp_path / "data" / "results").glob("*.wav"))


def test_metrics_and_status(svc):
    c, _ = svc
    jid = job(c).json()["job_id"]
    wait_state(c, jid)
    job(c)                                               # result cache
    job(c, key="k1")
    job(c, key="k1")                                     # replay
    assert c.get("/metrics").status_code == 401
    text = c.get("/metrics", headers=AUTH).text
    for needle in ('tts_jobs_created_total{how="new",priority="live"}', 'how="result_cache"', 'how="replay"',
                   "tts_queue_wait_seconds_bucket", "tts_inference_seconds_count", 'tts_result_cache_total{result="hit"}',
                   "tts_http_request_seconds_bucket", "tts_engine_ready 1", 'tts_queue_depth{priority="live"}',
                   'tts_jobs_finished_total{priority="live",state="done"}'):
        assert needle in text, needle
    assert TEXT not in text
    s = c.get("/v1/status", headers=AUTH).json()
    assert s["jobs"]["live"]["done"] >= 1 and "job_queue_wait" in s["latency"] and s["boot_id"]


def test_graceful_shutdown_withdraws_queued_jobs(app_factory):
    engine = FakeEngine()
    c, _ = app_factory(engine)
    wait_ready(c)
    put_voice(c)
    engine.block = threading.Event()
    head = job(c, text="đang chạy nha.").json()["job_id"]
    wait_running(c, head)
    queued = job(c, text="đang chờ nha.").json()["job_id"]
    jobs = c.app.state.jobs
    threading.Timer(0.5, engine.block.set).start()
    c.__exit__(None, None, None)
    assert jobs.jobs[queued].state == "cancelled" and "shutting down" in jobs.jobs[queued].error["message"]
    assert "đang chờ nha." not in engine.synth_texts


def test_stop_file_and_cuda_failure_request_exit(app_factory, tmp_path):
    c, engine = app_factory()
    wait_ready(c)
    put_voice(c)
    calls = []
    c.app.state.request_exit = lambda: calls.append("exit")
    (tmp_path / "data" / "server.stop").write_text("stop")
    time.sleep(1.5)
    assert calls == ["exit"]
    c2, e2 = app_factory()
    wait_ready(c2)
    put_voice(c2)
    calls2 = []
    c2.app.state.request_exit = lambda: calls2.append("exit")
    e2.fail_next = "cuda"
    c2.post("/v1/tts/synthesize", headers=AUTH, json={"voice_id": "shop_main", "text": TEXT})
    c2.get("/health/live")
    assert calls2 == ["exit"]


def test_instance_lock(tmp_path):
    a, b = InstanceLock(tmp_path / ".instance.lock"), InstanceLock(tmp_path / ".instance.lock")
    a.acquire()
    with pytest.raises(RuntimeError):
        b.acquire()
    a.release()
    b.acquire()
    b.release()
