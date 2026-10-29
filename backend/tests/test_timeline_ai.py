"""AI Timeline Analyzer with fake providers (no model, no API call): only real catalog products, low confidence goes to
review, evidence and timestamps, cache by video/catalog/settings, review drafts, and ffmpeg on a generated video."""
import json
import re
import os
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.timeline_ai import api, pipeline
from app.timeline_ai.api import TimelineAISettings
from app.timeline_ai.providers import ProviderUnavailable
from support import SaasTestCase

CATALOG = [{"id": 1, "name": "Áo thun cotton basic", "sku": "AT-01", "category": "Áo", "details": "", "facts": "", "status": "ACTIVE"},
           {"id": 2, "name": "Quần jean ống suông", "sku": "QJ-02", "category": "Quần", "details": "", "facts": "", "status": "ACTIVE"},
           {"id": 3, "name": "Váy hoa nhí", "sku": "VH-03", "category": "Váy", "details": "", "facts": "", "status": "ACTIVE"}]


def words(start_ms, text, step=400):
    return [{"start_ms": start_ms + i * step, "end_ms": start_ms + (i + 1) * step, "word": w, "probability": 0.9}
            for i, w in enumerate(text.split())]


def seg(start_ms, end_ms, text):
    return {"start_ms": start_ms, "end_ms": end_ms, "text": text, "words": words(start_ms, text)}


TRANSCRIPT = [
    seg(0, 20_000, "chào cả nhà hôm nay shop lên đồ mới"),
    seg(60_000, 90_000, "đầu tiên là chiếc áo thun cotton basic mặc rất mát"),
    seg(90_000, 120_000, "áo thun này có ba màu nha cả nhà"),
    seg(300_000, 330_000, "tiếp theo quần jean ống suông tôn dáng"),
    seg(600_000, 630_000, "cái này mặc đi biển xinh lắm"),
]


class FakeLLM:
    name, version = "fake", "fake:1"

    def __init__(self, segments):
        self.segments, self.calls = segments, 0

    def complete_json(self, prompt, max_output_tokens):
        """Like a real model, answers only about the transcript window it was given."""
        self.calls += 1
        assert "Không tự tạo id" in prompt and "CATALOG" in prompt
        lines = [tuple(map(int, m)) for m in re.findall(r"^\[(\d+)-(\d+)\]", prompt.split("TRANSCRIPT:")[1], re.M)]
        low, high = lines[0][0], lines[-1][1]
        first = lines[0][0] == TRANSCRIPT[0]["start_ms"]
        mine = [s for s in self.segments if isinstance(s.get("start_ms"), int) and low <= s["start_ms"] <= high]
        mine += [s for s in self.segments if first and not isinstance(s.get("start_ms"), int)]
        mine += [s for s in self.segments if isinstance(s.get("start_ms"), int) and s["start_ms"] > TRANSCRIPT[-1]["end_ms"] and not first]
        return "```json\n" + json.dumps({"segments": mine}, ensure_ascii=False) + "\n```"


class FakeTranscriber:
    name, version = "fake", "fake:1"

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio_path, language):
        self.calls += 1
        return TRANSCRIPT


class Aligner:
    name, version = "words", "words:1"

    def align(self, audio_path, segments, language):
        return segments


LLM_SEGMENTS = [
    {"start_ms": 60_000, "end_ms": 90_000, "product_id": 1, "confidence": 0.95, "evidence_quote": "chiếc áo thun cotton basic", "reason": "nói tên"},
    {"start_ms": 90_000, "end_ms": 120_000, "product_id": 1, "confidence": 0.9, "evidence_quote": "áo thun này", "reason": "tiếp tục"},
    {"start_ms": 300_000, "end_ms": 330_000, "product_id": 2, "confidence": 0.9, "evidence_quote": "quần jean ống suông", "reason": "nói tên"},
    # Confident, but the speech never names it: must not be trusted alone.
    {"start_ms": 600_000, "end_ms": 630_000, "product_id": 3, "confidence": 0.9, "evidence_quote": "mặc đi biển", "reason": "đoán theo ngữ cảnh"},
    # Invented product: never kept.
    {"start_ms": 0, "end_ms": 20_000, "product_id": 99, "confidence": 0.99, "evidence_quote": "đồ mới", "reason": "?"},
    {"start_ms": "x", "end_ms": 5},
    {"start_ms": 700_000, "end_ms": 9_999_999, "product_id": 2, "confidence": 0.8, "evidence_quote": "", "reason": "ngoài video"},
]


def run(llm_segments=LLM_SEGMENTS, transcript=TRANSCRIPT, catalog=CATALOG, **settings):
    config = TimelineAISettings.model_validate(settings)
    providers = {"transcriber": FakeTranscriber(), "aligner": Aligner(), "llm": FakeLLM(llm_segments), "vision": None}
    return pipeline.analyze(video=None, duration_ms=650_000, catalog=catalog, config=config, providers=providers,
                            transcript=transcript), providers


class PipelineTests(unittest.TestCase):
    def test_only_real_products_and_low_confidence_goes_to_review(self):
        result, _ = run()
        rows = [(s["action"], s["product_id"], s["review_status"]) for s in result["suggestions"]]
        self.assertEqual(rows, [
            ("NO_ACTION", None, "NEEDS_REVIEW"),           # the invented product 99
            ("PIN_PRODUCT", 1, "APPROVED"),                # two segments of product 1 merged
            ("UNPIN_PRODUCT", None, "APPROVED"),           # nothing presented for > 60 s after it
            ("PIN_PRODUCT", 2, "APPROVED"),
            ("UNPIN_PRODUCT", None, "APPROVED"),
            ("PIN_PRODUCT", 3, "NEEDS_REVIEW"),            # LLM sure, speech silent → capped below the threshold
            ("UNPIN_PRODUCT", None, "NEEDS_REVIEW"),
        ])
        self.assertEqual(result["dropped_products"], 1)
        ids = {s["product_id"] for s in result["suggestions"]} - {None}
        self.assertTrue(ids <= {p["id"] for p in CATALOG}, "AI never introduces a product id")
        guessed = next(s for s in result["suggestions"] if s["product_id"] == 3)
        self.assertLessEqual(guessed["confidence"], 0.6)
        invented = result["suggestions"][0]
        self.assertIn("không có trong catalog", invented["match_reason"])

    def test_every_suggestion_has_its_evidence(self):
        result, _ = run()
        first = next(s for s in result["suggestions"] if s["product_id"] == 1)
        self.assertEqual((first["start_ms"], first["end_ms"]), (60_000, 120_000))
        self.assertEqual(first["evidence_transcript"], "chiếc áo thun cotton basic")
        self.assertEqual(first["evidence_timestamp_ms"], 60_000 + 3 * 400, "timestamp of the quote's first word")
        for s in result["suggestions"]:
            self.assertTrue({"start_ms", "end_ms", "product_id", "action", "confidence", "evidence_transcript",
                             "evidence_timestamp_ms", "match_reason", "review_status"} <= set(s))
        self.assertTrue(all(0 <= s["start_ms"] <= 650_000 for s in result["suggestions"]), "times clamped to the video")

    def test_settings_change_thresholds(self):
        result, _ = run(min_confidence=0.5, unpin_after_seconds=0)
        self.assertNotIn("UNPIN_PRODUCT", [s["action"] for s in result["suggestions"]])
        self.assertEqual(next(s for s in result["suggestions"] if s["product_id"] == 3)["review_status"], "APPROVED")

    def test_vietnamese_text_without_tones_still_matches(self):
        self.assertEqual(pipeline.lexical_score("ao thun cotton basic nay", CATALOG[0]), 1.0)
        self.assertEqual(pipeline.lexical_score("mã AT-01 nha", CATALOG[0]), 1.0)
        self.assertEqual(pipeline.lexical_score("cái này đẹp lắm", CATALOG[0]), 0.0)

    def test_nothing_found_and_bad_answers(self):
        result, _ = run(llm_segments=[])
        self.assertEqual(result["suggestions"], [])
        result, _ = run(transcript=[])
        self.assertEqual(result["notes"], ["Không nhận dạng được lời nói trong video"])
        with self.assertRaises(pipeline.AnalysisError):
            run(catalog=[])
        config = TimelineAISettings()
        bad = SimpleNamespace(complete_json=lambda prompt, tokens: "không phải json")
        with self.assertRaises(pipeline.AnalysisError):
            pipeline.analyze(video=None, duration_ms=1000, catalog=CATALOG, config=config,
                             providers={"llm": bad}, transcript=TRANSCRIPT)

    def test_long_transcripts_are_sent_in_windows(self):
        long = [seg(i * 60_000, i * 60_000 + 30_000, f"phần {i}") for i in range(30)]
        _, providers = run(transcript=long, llm_segments=[])
        self.assertEqual(providers["llm"].calls, 4)

    def test_vision_confirms_or_suggests_uncertain_segments(self):
        config = TimelineAISettings.model_validate({"vision": {"enabled": True}})
        vision = SimpleNamespace(describe_json=lambda image, prompt: json.dumps({"product_id": 3, "confidence": 0.9, "reason": "váy hoa trên người mẫu"}))
        providers = {"llm": FakeLLM(LLM_SEGMENTS[3:4]), "vision": vision}
        with patch.object(pipeline, "frame_at", return_value=b"jpeg"):
            result = pipeline.analyze(video="video.mp4", duration_ms=650_000, catalog=CATALOG, config=config,
                                      providers=providers, transcript=TRANSCRIPT)
        pin = result["suggestions"][0]
        self.assertEqual((pin["product_id"], pin["review_status"]), (3, "APPROVED"))
        self.assertIn("hình ảnh xác nhận", pin["match_reason"])
        self.assertIn("vision", pin["sources"])
        liar = SimpleNamespace(describe_json=lambda image, prompt: json.dumps({"product_id": 77, "confidence": 1}))
        with patch.object(pipeline, "frame_at", return_value=b"jpeg"):
            result = pipeline.analyze(video="video.mp4", duration_ms=650_000, catalog=CATALOG, config=config,
                                      providers={"llm": FakeLLM(LLM_SEGMENTS[3:4]), "vision": liar}, transcript=TRANSCRIPT)
        self.assertEqual((result["suggestions"][0]["product_id"], result["suggestions"][0]["review_status"]), (3, "NEEDS_REVIEW"))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed")
class FfmpegTests(unittest.TestCase):
    def test_metadata_audio_and_frame_of_a_generated_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "v.mp4")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:d=3", "-f", "lavfi",
                            "-i", "sine=frequency=440:duration=3", "-shortest", "-c:v", "libx264", "-c:a", "aac", video], check=True)
            self.assertAlmostEqual(pipeline.probe_ms("ffprobe", video), 3000, delta=150)
            wav = os.path.join(tmp, "a.wav")
            pipeline.extract_audio("ffmpeg", video, wav)
            self.assertGreater(os.path.getsize(wav), 3 * 16000 * 2 * 0.9, "16 kHz mono 16-bit")
            self.assertEqual(pipeline.frame_at("ffmpeg", video, 1500)[:2], b"\xff\xd8", "a JPEG frame")
            try:
                import numpy  # noqa: F401 - comes with faster-whisper; read_wav needs it
            except ImportError:
                numpy = None
            if numpy is not None:
                from app.timeline_ai.providers import ProviderUnavailable, read_wav
                samples = read_wav(wav)  # what Whisper gets instead of a path (PyAV is never used)
                self.assertAlmostEqual(len(samples), 3 * 16000, delta=16000 * 0.1)
                self.assertTrue(-1.0 <= float(samples.min()) and float(samples.max()) <= 1.0)
                stereo = os.path.join(tmp, "stereo.wav")
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-ac", "2", "-ar", "44100", stereo], check=True)
                with self.assertRaises(ProviderUnavailable):
                    read_wav(stereo)
            with self.assertRaises(pipeline.AnalysisError):
                pipeline.probe_ms("ffprobe", os.path.join(tmp, "missing.mp4"))


class AnalysisApiTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        self.ao, self.quan, self.vay = self.tiktok_products(self.owner_a, "Áo thun cotton basic", "Quần jean ống suông", "Váy hoa nhí")
        agent = self.activate_agent(self.owner_a)
        account = self.console_sync(agent, product_ids=(self.ao, self.quan, self.vay))
        self.main, self.other = self.library_videos(self.owner_a, "main.mp4", "other.mp4")
        with self.Session() as db:
            from app.storage_models import MediaItem
            db.get(MediaItem, self.main).duration_seconds = 650.0
            db.commit()
        self.sid = self.client.post("/api/live/sessions", json={"name": "S", "tiktok_account_id": account,
            "playback_mode": "SINGLE_VIDEO_TIMELINE", "main_media_id": self.main}).json()["id"]
        self.base = f"/api/live/sessions/{self.sid}/timeline-analysis"
        segments = [dict(s, product_id={1: self.ao, 2: self.quan, 3: self.vay}.get(s.get("product_id"), s.get("product_id")))
                    for s in LLM_SEGMENTS if isinstance(s.get("start_ms"), int)]
        self.transcriber, self.llm = FakeTranscriber(), FakeLLM(segments)
        patches = [patch.object(api, "start", lambda target: target()),
                   patch.object(api, "build_providers", lambda owner_id, config: {"transcriber": self.transcriber, "aligner": Aligner(),
                                                                                  "llm": self.llm, "vision": None}),
                   patch("app.media_clips._download", lambda db, media, path: open(path, "wb").write(b"video")),
                   patch.object(pipeline, "extract_audio", lambda ffmpeg, video, wav: None),
                   patch.object(api, "_tool", lambda name, required=True: name)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def analyze(self, **body):
        started = self.client.post(self.base, json=body)
        self.assertEqual(started.status_code, 202, started.text)
        return self.client.get(f"{self.base}/{started.json()['id']}").json()

    def test_analysis_job_suggests_with_evidence(self):
        job = self.analyze()
        self.assertEqual((job["status"], job["cached"], job["progress"]), ("DONE", False, 1.0))
        self.assertEqual(job["summary"]["dropped_products"], 1)
        names = [s["product_name"] for s in job["suggestions"] if s["action"] == "PIN_PRODUCT"]
        self.assertEqual(names, ["Áo thun cotton basic", "Quần jean ống suông", "Váy hoa nhí"])
        self.assertEqual(job["config"]["transcript"]["language"], "vi")
        self.assertEqual(len(self.client.get(self.base).json()), 1)

    def test_cache_by_video_catalog_and_settings(self):
        self.analyze()
        per_run = self.llm.calls  # one call per transcript window
        again = self.analyze()
        self.assertEqual((again["cached"], self.transcriber.calls, self.llm.calls), (True, 1, per_run), "nothing ran again")
        with self.Session() as db:
            from app.catalog import Product
            db.get(Product, self.vay).name = "Váy hoa nhí cổ vuông"
            db.commit()
        changed = self.analyze()
        self.assertEqual((changed["cached"], self.transcriber.calls, self.llm.calls), (False, 1, 2 * per_run),
                         "catalog changed: transcript from cache, products analysed again")
        self.assertEqual(self.client.put("/api/ai/timeline-settings", json={"min_confidence": 0.5}).status_code, 200)
        self.analyze()
        self.assertEqual(self.llm.calls, 3 * per_run, "settings are part of the cache key")
        self.analyze(force=True)
        self.assertEqual((self.transcriber.calls, self.llm.calls), (2, 4 * per_run))

    def test_draft_from_suggestions_needs_review_before_approval(self):
        job = self.analyze()
        created = self.client.post(f"{self.base}/{job['id']}/draft")
        self.assertEqual(created.status_code, 201, created.text)
        draft = created.json()
        self.assertEqual(draft["status"], "DRAFT")
        markers = draft["markers"]
        self.assertTrue(all(m["source"] == "AI" for m in markers))
        self.assertEqual(sum(1 for m in markers if m["review_status"] == "NEEDS_REVIEW"), 3)
        evidence = json.loads(next(m for m in markers if m["product_id"] == self.ao)["evidence"])
        self.assertEqual((evidence["transcript"], evidence["analysis_id"]), ("chiếc áo thun cotton basic", job["id"]))
        tl = f"/api/live/sessions/{self.sid}/timelines/{draft['id']}"
        self.assertEqual(self.client.post(f"{tl}/approve").status_code, 409, "AI suggestions are never applied unreviewed")
        reviewed = [{k: m[k] for k in ("at_ms", "action", "product_id", "label", "source", "confidence", "evidence")}
                    | {"review_status": "APPROVED" if m["product_id"] or m["action"] == "UNPIN_PRODUCT" else "REJECTED"}
                    for m in markers]
        self.assertEqual(self.client.put(f"{tl}/markers", json={"markers": reviewed}).status_code, 200)
        self.assertEqual(self.client.post(f"{tl}/approve").status_code, 200)
        self.assertEqual(self.client.get(f"{self.base}/{job['id']}").json()["timeline_id"], draft["id"])

    def test_failures_are_reported_not_hidden(self):
        with patch.object(api, "build_providers", side_effect=ProviderUnavailable("Chưa có API key Gemini")):
            job = self.analyze()
        self.assertEqual((job["status"], job["error"]), ("FAILED", "Chưa có API key Gemini"))
        self.assertEqual(self.client.post(f"{self.base}/{job['id']}/draft").status_code, 409)

    def test_video_changed_and_scoping(self):
        job = self.analyze()
        self.client.patch(f"/api/live/sessions/{self.sid}", json={"main_media_id": self.other})
        self.assertEqual(self.client.post(f"{self.base}/{job['id']}/draft").status_code, 409)
        self.assertEqual(self.client.get(f"{self.base}/{job['id']}", headers=self.B).status_code, 404)
        legacy = self.client.post("/api/live/sessions", json={"name": "L", "tiktok_account_id": 1}).json().get("id")
        if legacy:
            self.assertEqual(self.client.post(f"/api/live/sessions/{legacy}/timeline-analysis", json={}).status_code, 409)

    def test_settings_are_validated(self):
        current = self.client.get("/api/ai/timeline-settings").json()
        self.assertEqual((current["transcript"]["provider"], current["llm"]["provider"], current["vision"]["enabled"]),
                         ("faster_whisper", "gemini", False))
        self.assertIn("faster_whisper", current["installed"])
        self.assertTrue(current["resolved"]["llm"]["model"], "the owner's AI model when none is set")
        for bad in ({"min_confidence": 1.5}, {"llm": {"provider": "unknown"}}, {"transcript": {"model": "a b"}},
                    {"alignment": {"provider": "magic"}}):
            self.assertEqual(self.client.put("/api/ai/timeline-settings", json=bad).status_code, 422, bad)
        saved = self.client.put("/api/ai/timeline-settings", json={"llm": {"model": "custom-model-1"}, "alignment": {"provider": "whisperx"}}).json()
        self.assertEqual((saved["resolved"]["llm"]["model"], saved["alignment"]["provider"]), ("custom-model-1", "whisperx"))


if __name__ == "__main__":
    unittest.main()
