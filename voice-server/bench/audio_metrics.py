"""Measurements for the Phase 0 baseline: reference QC, output audio levels and ASR word alignment.

Pure numpy, no model: everything here only *reports*; nothing changes the audio it is given.
The audio part lives in the service package (sarae_voice.audio) so the bench and the service measure the same way.
"""
from __future__ import annotations

import re
import unicodedata

import numpy as np

from sarae_voice.audio import (EDGE_CUT_DB, SILENCE_DB, audio_stats, edge_db, frame_db,  # noqa: F401 - re-exported
                               leading_silence_ms, reference_qc, to_float, trailing_silence_ms)


# --- text -------------------------------------------------------------------------------------
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text or "").lower()
    return _PUNCT.sub(" ", text).split()


def align(ref: list[str], hyp: list[str]) -> list[tuple[str, int | None, int | None]]:
    """Levenshtein alignment: (op, ref_index, hyp_index) with op in match/sub/del/ins."""
    n, m = len(ref), len(hyp)
    cost = np.zeros((n + 1, m + 1), dtype=np.int32)
    cost[:, 0] = np.arange(n + 1)
    cost[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i, j] = min(cost[i - 1, j] + 1, cost[i, j - 1] + 1,
                             cost[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]))
    ops = []
    i, j = n, m
    while i or j:
        if i and j and cost[i, j] == cost[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]):
            ops.append(("match" if ref[i - 1] == hyp[j - 1] else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and cost[i, j] == cost[i - 1, j] + 1:
            ops.append(("del", i - 1, None))
            i -= 1
        else:
            ops.append(("ins", None, j - 1))
            j -= 1
    return ops[::-1]


def word_report(target: str, hypothesis: str, edge: int = 2) -> dict:
    """WER plus how many of the first/last `edge` target words the ASR did not hear."""
    ref, hyp = words(target), words(hypothesis)
    ops = align(ref, hyp)
    errors = sum(op != "match" for op, _, _ in ops)
    status = {r: op for op, r, _ in ops if r is not None}
    head = sum(status.get(k) != "match" for k in range(min(edge, len(ref))))
    tail = sum(status.get(k) != "match" for k in range(max(0, len(ref) - edge), len(ref)))
    return {"wer": round(errors / max(1, len(ref)), 4), "ref_words": len(ref), "hyp_words": len(hyp),
            "head_missing": head, "tail_missing": tail,
            "head_deleted": sum(status.get(k) == "del" for k in range(min(edge, len(ref))))}
