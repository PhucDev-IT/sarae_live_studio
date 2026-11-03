import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

from audio_metrics import (align, audio_stats, leading_silence_ms, reference_qc, trailing_silence_ms,  # noqa: E402
                           word_report, words)

SR = 24000


def tone(seconds: float, amplitude: float = 0.3) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return amplitude * np.sin(2 * np.pi * 220 * t)


def test_words_normalizes_vietnamese_case_and_punctuation():
    assert words("Dạ, shop chào CẢ NHÀ!") == ["dạ", "shop", "chào", "cả", "nhà"]


def test_align_marks_deletion_substitution_and_insertion():
    assert [op for op, _, _ in align(["a", "b", "c", "d"], ["b", "c", "d"])] == ["del", "match", "match", "match"]
    assert [op for op, _, _ in align(["a", "b"], ["a", "x"])] == ["match", "sub"]
    assert [op for op, _, _ in align(["a", "b"], ["a", "b", "c"])] == ["match", "match", "ins"]


def test_word_report_detects_missing_first_words():
    report = word_report("Dạ, shop chào cả nhà nha.", "chào cả nhà nha")
    assert report["head_missing"] == 2 and report["head_deleted"] == 2 and report["tail_missing"] == 0
    assert report["wer"] == round(2 / 6, 4)


def test_word_report_detects_missing_last_word():
    report = word_report("Hàng về đủ size rồi đó nha.", "hàng về đủ size rồi đó")
    assert report["head_missing"] == 0 and report["tail_missing"] == 1


def test_word_report_perfect_match():
    assert word_report("Cả nhà thả tim ạ.", "cả nhà thả tim ạ")["wer"] == 0


def test_silence_edges_are_measured():
    audio = np.concatenate([np.zeros(int(SR * 0.3)), tone(1.0), np.zeros(int(SR * 0.5))])
    assert abs(leading_silence_ms(audio, SR) - 300) <= 10
    assert abs(trailing_silence_ms(audio, SR) - 500) <= 10


def test_reference_qc_flags_cut_end_but_not_clean_end():
    clean = np.concatenate([np.zeros(int(SR * 0.3)), tone(5.0), np.zeros(int(SR * 0.4))])
    cut = np.concatenate([np.zeros(int(SR * 0.3)), tone(5.0)])
    assert "end_may_be_cut" not in reference_qc(clean, SR)["warnings"]
    assert "end_may_be_cut" in reference_qc(cut, SR)["warnings"]


def test_reference_qc_duration_limits():
    assert "duration_out_of_range" in reference_qc(tone(25.0), SR)["warnings"]
    assert "duration_not_recommended" in reference_qc(np.concatenate([tone(2.0), np.zeros(SR // 2)]), SR)["warnings"]


def test_audio_stats_reads_int16_and_counts_clipping():
    audio = (np.concatenate([tone(0.5, 1.0), tone(0.5)]) * 32767).astype(np.int16)
    stats = audio_stats(audio, SR)
    assert stats["duration_s"] == 1.0 and stats["clipped_samples"] > 0


def test_golden_set_edges_are_plain_words():
    items = json.loads((Path(__file__).resolve().parents[1] / "bench" / "golden_set.json").read_text(encoding="utf-8"))["items"]
    assert len({i["id"] for i in items}) == len(items)
    for item in items:
        w = words(item["text"])
        assert all(not any(c.isdigit() for c in x) for x in w[:2] + w[-2:]), item["id"]


def test_recheck_verdicts_separate_asr_spelling_from_tts_loss():
    from recheck_asr import edge_similarity, plain, verdict
    assert plain("Sốp") == "sop" and plain("Đơn") == "don"
    assert edge_similarity("Dạ, shop chào cả nhà", "dạ sốp chào cả nhà", head=True) >= 0.5
    assert verdict(1, 1, 1, 1, 0.0, 0.1) == "tts_suspect"     # both ASRs: word deleted
    assert verdict(1, 1, 0, 0, 0.0, 0.8) == "tts_suspect"     # sound before the first recognized word
    assert verdict(1, 0, 0, 0, 0.0, 0.1) == "asr_error"       # the second ASR heard it
    assert verdict(1, 1, 0, 0, 0.86, 0.1) == "asr_error"      # misspelled, sound in place
    assert verdict(1, 1, 0, 0, 0.2, 0.1) == "listen"
