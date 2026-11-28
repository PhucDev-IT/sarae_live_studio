from __future__ import annotations

import random

import pytest

from sarae_agent.studio.speech import UNITS, PriceReader, read_number, read_price


class Fixed:
    """A reader that always picks the first allowed unit, so outputs are predictable."""

    def choice(self, options):
        return options[0]


@pytest.mark.parametrize("n,words", [(0, "không"), (5, "năm"), (15, "mười lăm"), (21, "hai mươi mốt"), (105, "một trăm linh năm"),
                                     (109, "một trăm linh chín"), (250, "hai trăm năm mươi"), (1005, "một nghìn không trăm linh năm")])
def test_read_number(n, words):
    assert read_number(n) == words


@pytest.mark.parametrize("value,spoken", [
    (109000, "một trăm linh chín ca"), (99000, "chín mươi chín ca"), (1250000, "một triệu hai trăm năm mươi ca"),
    (1050000, "một triệu không trăm năm mươi ca"), (2000000, "hai triệu"), (1500, "một nghìn năm trăm đồng"),
])
def test_read_price_never_says_nghin_for_thousands(value, spoken):
    assert read_price(value, "ca") == spoken


@pytest.mark.parametrize("text,spoken", [
    ("Dạ set giá 109k ạ.", "Dạ set giá một trăm linh chín ca ạ."),
    ("Dạ set giá 109.000đ ạ.", "Dạ set giá một trăm linh chín ca ạ."),
    ("Giá 109.000 ₫ nha", "Giá một trăm linh chín ca nha"),
    ("Giá 109 nghìn đồng nha", "Giá một trăm linh chín ca nha"),
    ("Váy 1.250.000đ ạ", "Váy một triệu hai trăm năm mươi ca ạ"),
    ("Váy 1tr2 ạ", "Váy một triệu hai trăm ca ạ"),
    ("Váy 1,5tr ạ", "Váy một triệu năm trăm ca ạ"),
    ("Váy 2 triệu ạ", "Váy hai triệu ạ"),
    ("Giá 109000đ", "Giá một trăm linh chín ca"),
])
def test_prices_are_read_the_sellers_way(text, spoken):
    assert PriceReader(Fixed())(text) == spoken


@pytest.mark.parametrize("text", ["Bạn 52kg mặc size L nha.", "Cao 1m6 mặc vừa ạ.", "Quần dài 95cm.", "Size M 45-55kg.",
                                  "Còn 5 cái ạ.", "Giao 2-4 ngày."])
def test_other_numbers_are_left_alone(text):
    assert PriceReader(Fixed())(text) == text


def test_units_rotate_and_never_repeat_back_to_back():
    reader = PriceReader(random.Random(7))
    spoken = [reader("Giá 109k")[len("Giá một trăm linh chín "):] for _ in range(60)]
    assert set(spoken) == set(UNITS)
    assert all(a != b for a, b in zip(spoken, spoken[1:]))


def test_two_prices_in_one_reply_get_different_words():
    assert PriceReader(Fixed())("Áo 99k, quần 109k") == "Áo chín mươi chín ca, quần một trăm linh chín cá"
