"""Prices written for the ear, the way sellers say them on LIVE.

The AI and the TikTok comment keep "109k" / "109.000đ"; only the voice reads "một trăm linh chín ca". The shop asked
never to hear "nghìn" in a price: the thousands are read with "ca", "cá" or "đồng", rotated so the LIVE does not
repeat itself; millions keep "triệu" ("một triệu hai trăm năm mươi cá"). Number words follow backend/app/speech_text.py.
"""
from __future__ import annotations

import random
import re

DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
GROUPS = ["", "nghìn", "triệu", "tỷ"]
UNITS = ("ca", "cá", "đồng")
_CURRENCY = r"(?:đồng|đ|₫|vnđ|vnd)"
MONEY = re.compile(
    r"(?<![\w.,])(?:"
    r"(?P<mil>\d{1,4})(?:[.,](?P<mildec>\d))?\s*(?:tr|triệu)(?P<milrest>\d{1,3})?(?:\s*" + _CURRENCY + r")?"
    r"|(?P<k>\d{1,6}(?:[.,]\d{1,2})?)\s*(?:k|nghìn|ngàn|ca|cá)(?:\s*" + _CURRENCY + r")?"
    r"|(?P<sep>\d{1,3}(?:[.,]\d{3})+)\s*" + _CURRENCY + r"?"
    r"|(?P<plain>\d{4,10})\s*" + _CURRENCY +
    r")(?![\w])", re.IGNORECASE)


def _three(n: int, full: bool) -> list[str]:
    """0..999 in words. ``full`` reads the leading zeros of an inner group ("không trăm linh năm")."""
    hundreds, tens, units = n // 100, n // 10 % 10, n % 10
    words = []
    if full or hundreds:
        words += [DIGITS[hundreds], "trăm"]
    if tens == 0:
        if units and words:
            words.append("linh")
        if units:
            words.append(DIGITS[units])
    elif tens == 1:
        words.append("mười")
        if units:
            words.append("lăm" if units == 5 else DIGITS[units])
    else:
        words += [DIGITS[tens], "mươi"]
        if units:
            words.append({1: "mốt", 4: "tư", 5: "lăm"}.get(units, DIGITS[units]))
    return words


def read_number(n: int) -> str:
    """Vietnamese words for a non-negative integer."""
    if n == 0:
        return "không"
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    words: list[str] = []
    for index in range(len(groups) - 1, -1, -1):
        if groups[index]:
            words += _three(groups[index], full=bool(words))
            if GROUPS[index]:
                words.append(GROUPS[index])
    return " ".join(words)


def _value(match: re.Match) -> int:
    if match.group("mil"):
        value = int(match.group("mil")) * 1_000_000 + int(match.group("mildec") or 0) * 100_000
        rest = match.group("milrest")
        return value + (int(rest.ljust(3, "0")) * 1000 if rest else 0)
    if match.group("k"):
        return round(float(match.group("k").replace(",", ".")) * 1000)
    if match.group("sep"):
        return int(re.sub(r"[.,]", "", match.group("sep")))
    return int(match.group("plain"))


def read_price(value: int, unit: str) -> str:
    """109000 -> "một trăm linh chín ca"; 1250000 -> "một triệu hai trăm năm mươi ca"; 2000000 -> "hai triệu"."""
    if value < 1000 or value % 1000:
        return f"{read_number(value)} đồng"  # an odd amount ("1.500đ"): read exactly
    thousands = value // 1000
    millions, rest = divmod(thousands, 1000)
    words = [read_number(millions), "triệu"] if millions else []
    if rest:
        words += [*_three(rest, full=bool(millions)), unit]
    return " ".join(words)


class PriceReader:
    """Rotates "ca" / "cá" / "đồng", never the same word twice in a row."""

    def __init__(self, rng: random.Random | None = None):
        self._rng = rng or random.Random()
        self._last: str | None = None

    def _unit(self) -> str:
        unit = self._rng.choice([u for u in UNITS if u != self._last])
        self._last = unit
        return unit

    def __call__(self, text: str) -> str:
        return MONEY.sub(lambda match: read_price(_value(match), self._unit()), text)
