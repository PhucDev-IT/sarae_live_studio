"""Shop knowledge: validation, instant Q&A matching, money parsing and the light check on AI replies."""
from __future__ import annotations

import pytest

from sarae_agent.studio.knowledge import (DEFAULT, MAX_ANSWER, MAX_KEYWORDS, MAX_POLICY, MAX_QA, POLICY_FIELDS,
                                          allowed_money, fold, load_shop, money_values, qa_match,
                                          shop_prompt_context, tidy_reply, validate_shop)
from sarae_agent.studio.models import StudioError

EMPTY_POLICIES = {field: "" for field in POLICY_FIELDS}


def error_of(params) -> StudioError:
    with pytest.raises(StudioError) as caught:
        validate_shop(params)
    assert caught.value.code == "VALIDATION_ERROR"
    return caught.value


def test_fold_lowercases_strips_accents_and_punctuation():
    assert fold("Kiểm hàng được ko?") == "kiem hang duoc ko"
    assert fold("  ĐỔI   trả!!  ") == "doi tra"
    assert fold("???") == ""


# --- validate_shop ---------------------------------------------------------------------------

def test_validate_shop_defaults():
    assert validate_shop({}) == {"policies": EMPTY_POLICIES, "qa": []}
    assert validate_shop({"policies": {}, "qa": []}) == {"policies": EMPTY_POLICIES, "qa": []}
    assert validate_shop(DEFAULT) == {"policies": EMPTY_POLICIES, "qa": []}


def test_validate_shop_trims_whitespace():
    shop = validate_shop({"policies": {"shipping": "  Giao   2-4\nngày  ", "payment": "   "},
                          "qa": [{"keywords": ["  kiểm   hàng "], "answer": "  Được   kiểm\thàng ạ. "}]})
    assert shop["policies"] == {**EMPTY_POLICIES, "shipping": "Giao 2-4 ngày"}
    assert shop["qa"] == [{"keywords": ["kiểm hàng"], "answer": "Được kiểm hàng ạ."}]


def test_validate_shop_removes_duplicate_keywords_ignoring_accents_and_case():
    shop = validate_shop({"qa": [{"keywords": ["Kiểm hàng", "kiem hang", "KIỂM  HÀNG", "đồng kiểm", "dong kiem?"],
                                  "answer": "Có ạ"}]})
    assert shop["qa"][0]["keywords"] == ["Kiểm hàng", "đồng kiểm"]


def test_validate_shop_accepts_the_limits():
    shop = validate_shop({"policies": {field: "x" * MAX_POLICY for field in POLICY_FIELDS},
                          "qa": [{"keywords": [f"k{n}" for n in range(MAX_KEYWORDS)], "answer": "a" * MAX_ANSWER}] * MAX_QA})
    assert len(shop["qa"]) == MAX_QA and len(shop["qa"][0]["keywords"]) == MAX_KEYWORDS
    assert validate_shop({"qa": [{"keywords": ["x" * 40], "answer": "ok"}]})["qa"][0]["keywords"] == ["x" * 40]


@pytest.mark.parametrize("params,field", [
    (None, None), ([], None), ({"extra": 1}, None), ({"policies": {}, "qa": [], "x": 1}, None),
    ({"policies": {"warranty": "1 năm"}}, "policies"), ({"policies": "giao nhanh"}, "policies"),
    ({"policies": {"shipping": "x" * (MAX_POLICY + 1)}}, "policies.shipping"),
    ({"policies": {"returns": 7}}, "policies.returns"), ({"policies": {"other": "a\x00b"}}, "policies.other"),
    ({"qa": {}}, "qa"), ({"qa": [{"keywords": ["a"], "answer": "b"}] * (MAX_QA + 1)}, "qa"),
    ({"qa": ["kiểm hàng"]}, "qa"), ({"qa": [{"keywords": ["a"], "answer": "b", "id": 1}]}, "qa"),
    ({"qa": [{"keywords": [], "answer": "b"}]}, "qa"), ({"qa": [{"keywords": "ship", "answer": "b"}]}, "qa"),
    ({"qa": [{"keywords": [f"k{n}" for n in range(MAX_KEYWORDS + 1)], "answer": "b"}]}, "qa"),
    ({"qa": [{"keywords": ["???"], "answer": "b"}]}, "qa"), ({"qa": [{"keywords": ["x" * 41], "answer": "b"}]}, "qa"),
    ({"qa": [{"keywords": [1], "answer": "b"}]}, "qa"), ({"qa": [{"keywords": ["a"]}]}, "qa"),
    ({"qa": [{"keywords": ["a"], "answer": "   "}]}, "qa"), ({"qa": [{"keywords": ["a"], "answer": "b" * (MAX_ANSWER + 1)}]}, "qa"),
    ({"qa": [{"keywords": ["a"], "answer": "b\x00"}]}, "qa"),
])
def test_validate_shop_rejects_bad_input(params, field):
    error = error_of(params)
    if field is not None:
        assert error.details["field"] == field


def test_validate_shop_reports_the_bad_qa_index():
    error = error_of({"qa": [{"keywords": ["ship"], "answer": "ok"}, {"keywords": [], "answer": "ok"}]})
    assert error.details == {"field": "qa", "index": 1} and "Câu Q&A 2" in error.message


class FakeSettings:
    def __init__(self, value):
        self.value = value

    def get_setting(self, key, default):
        assert key == "shop"
        return dict(default) if self.value is None else self.value


def test_load_shop_falls_back_to_defaults_on_missing_or_broken_settings():
    assert load_shop(FakeSettings(None)) == {"policies": EMPTY_POLICIES, "qa": []}
    assert load_shop(FakeSettings({"policies": {"warranty": "x"}})) == {"policies": EMPTY_POLICIES, "qa": []}
    stored = {"policies": {"payment": "COD"}, "qa": []}
    assert load_shop(FakeSettings(stored))["policies"]["payment"] == "COD"


def test_shop_prompt_context_uses_labels_and_skips_empty_parts():
    assert shop_prompt_context({"policies": EMPTY_POLICIES, "qa": []}) is None
    context = shop_prompt_context({"policies": {**EMPTY_POLICIES, "returns": "Đổi trong 3 ngày"}, "qa": []})
    assert context == {"chinh_sach": {"đổi trả": "Đổi trong 3 ngày"}, "hoi_dap": None}
    context = shop_prompt_context({"policies": EMPTY_POLICIES, "qa": [{"keywords": ["ship"], "answer": "2-4 ngày"}]})
    assert context == {"chinh_sach": None, "hoi_dap": [{"tu_khoa": ["ship"], "tra_loi": "2-4 ngày"}]}


# --- qa_match --------------------------------------------------------------------------------

QA = [{"keywords": ["kiểm hàng"], "answer": "Bạn được kiểm hàng trước khi nhận ạ."},
      {"keywords": ["đổi", "size"], "answer": "Shop đổi size trong 3 ngày ạ."},
      {"keywords": ["ship", "cod"], "answer": "Shop ship COD toàn quốc ạ."}]


@pytest.mark.parametrize("comment,answer", [
    ("Kiểm hàng được không shop?", "Bạn được kiểm hàng trước khi nhận ạ."),
    ("kiem hang dc ko", "Bạn được kiểm hàng trước khi nhận ạ."),
    ("KIỂM HÀNG ko", "Bạn được kiểm hàng trước khi nhận ạ."),
    ("size ko vừa đổi được ko", "Shop đổi size trong 3 ngày ạ."),
    ("doi size duoc khong", "Shop đổi size trong 3 ngày ạ."),
])
def test_qa_match_finds_the_one_entry(comment, answer):
    assert qa_match(comment, QA)["answer"] == answer


@pytest.mark.parametrize("comment", [
    "đổi size rồi kiểm hàng được không",  # two entries match
    "shop ơi cho mình hỏi là nếu nhận hàng rồi mà muốn kiểm hàng thì có được không ạ",  # longer than 12 words
    "ship tỉnh không",  # only part of the ship+cod keywords
    "đổi màu được không",  # "đổi" without "size"
    "kiểmhàng",  # keywords are whole words
    "", "???",
])
def test_qa_match_returns_none(comment):
    assert qa_match(comment, QA) is None


def test_qa_match_counts_words_after_folding_and_needs_whole_words():
    twelve = "shop ơi cho mình hỏi chút là kiểm hàng được không"
    assert len(fold(twelve).split()) <= 12 and qa_match(twelve, QA) is not None
    assert qa_match("shipping cod", QA) is None  # "ship" is not "shipping"
    assert qa_match("kiểm hàng", []) is None


# --- money -----------------------------------------------------------------------------------

@pytest.mark.parametrize("text,values", [
    ("109k", {109000}), ("109K", {109000}), ("109.000đ", {109000}), ("109.000 ₫", {109000}), ("109,000đ", {109000}),
    ("109 nghìn", {109000}), ("109 ngàn", {109000}), ("109.000 VNĐ", {109000}), ("109000 đồng", {109000}),
    ("2tr", {2000000}), ("1.200.000đ", {1200000}), ("Áo 99k, quần 109.000đ", {99000, 109000}),
    ("50kg", set()), ("1m6", set()), ("1m60 48kg", set()), ("size M 45-55kg", set()), ("Còn 5 đôi", set()),
    ("giao 2-4 ngày", set()), ("giảm 50đ", set()), ("", set()),
])
def test_money_values(text, values):
    assert money_values(text) == values


def test_allowed_money_collects_numbers_and_amounts_in_text():
    allowed = allowed_money({"gia_ban": "389.000đ", "gia_goc": None, "ton_kho": "còn 4"}, None,
                            {"chinh_sach": {"giao hàng": "Freeship đơn từ 300k"}}, [{"gia_ban": "259.000đ"}], 109000, True)
    assert {389000, 300000, 259000, 109000} <= allowed
    assert 1 not in allowed  # booleans are not money
    assert allowed_money(None) == set()


# --- tidy_reply ------------------------------------------------------------------------------

def test_tidy_drops_an_unknown_price_and_keeps_the_rest():
    assert tidy_reply("Áo giá 99k ạ. Size M hợp 50kg.", {389000}) == ("Size M hợp 50kg.", ["Áo giá 99k ạ."])


@pytest.mark.parametrize("reply", ["Áo giá 109.000đ ạ.", "Áo giá 109k ạ!", "Áo 109 nghìn nha", "Giá 109.000 ₫ bạn nhé."])
def test_tidy_keeps_a_known_price_in_any_spelling(reply):
    assert tidy_reply(reply, {109000}) == (reply, [])


@pytest.mark.parametrize("reply", [
    "Shop sẽ kiểm tra và phản hồi ngay ạ", "Shop sẽ kiểm tra lại cho bạn nha.", "Em xin phép kiểm tra rồi báo bạn.",
    "Shop báo lại sau nha.", "Shop phản hồi bạn sớm nhé.", "Bạn chờ shop kiểm tra đơn nha.",
    "Shop check lại rồi báo bạn ạ.",
])
def test_tidy_drops_promises_to_check_back(reply):
    assert tidy_reply(reply, set()) == ("", [reply])


@pytest.mark.parametrize("reply", [
    "Bạn được kiểm tra hàng trước khi nhận ạ.", "Shop cho kiểm tra hàng trước khi nhận ạ.",
    "Bạn được đồng kiểm khi nhận hàng ạ.", "Size M hợp 45-55kg, cao 1m6 mặc vừa ạ.",
])
def test_tidy_keeps_legit_sentences(reply):
    assert tidy_reply(reply, set()) == (reply, [])


def test_tidy_mixed_reply_and_edge_cases():
    reply = "Bạn được kiểm tra hàng trước khi nhận ạ. Shop sẽ kiểm tra và phản hồi ngay ạ. Áo giá 109k nha."
    assert tidy_reply(reply, {109000}) == ("Bạn được kiểm tra hàng trước khi nhận ạ. Áo giá 109k nha.",
                                           ["Shop sẽ kiểm tra và phản hồi ngay ạ."])
    assert tidy_reply("", set()) == ("", [])
    # A decimal point inside a price does not split the sentence.
    assert tidy_reply("Áo giá 109.000đ. Size M hợp 50kg.", {109000}) == ("Áo giá 109.000đ. Size M hợp 50kg.", [])


@pytest.mark.parametrize("text,values", [("1,5tr", {1500000}), ("1.5tr", {1500000}), ("2,5k", {2500}),
                                         ("11.5tr", {11500000}), ("Áo giá 1,5 triệu", {1500000})])
def test_money_values_reads_decimal_amounts(text, values):
    assert money_values(text) == values


@pytest.mark.parametrize("reply", [
    "Shop kiểm tra kỹ từng đơn trước khi gửi ạ.", "Nhận hàng mình kiểm tra thoải mái nha.",
    "Bạn sẽ được kiểm tra hàng khi nhận ạ.", "Khi nhận bạn sẽ kiểm tra hàng rồi mới thanh toán nha.",
    "Mình sẽ kiểm tra hàng trước khi trả tiền nha.",
])
def test_tidy_keeps_inspection_policies(reply):
    assert tidy_reply(reply, set()) == (reply, [])


def test_tidy_keeps_a_decimal_price_from_the_data():
    assert tidy_reply("Áo giá 1,5tr ạ.", {1500000}) == ("Áo giá 1,5tr ạ.", [])
