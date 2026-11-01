"""Text written for the ear (TTS on LIVE) and the safety checks every AI reply passes before anyone hears or reads it.

Pure functions, no I/O. The LLM writes numbers as digits ("200k", "55kg"); this module turns them into Vietnamese words
so the voice reads them right, and checks that a reply never states a price the product does not have.
"""
import re
import unicodedata

DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
GROUPS = ["", "nghìn", "triệu", "tỷ"]


def _three(n: int, full: bool) -> list[str]:
    """0..999 in words. `full` reads the leading zeros of an inner group ("không trăm linh năm")."""
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
    """Vietnamese words for a non-negative integer (up to hundreds of billions)."""
    if n == 0:
        return "không"
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    words = []
    for index in range(len(groups) - 1, -1, -1):
        value = groups[index]
        if value == 0:
            continue
        words += _three(value, full=bool(words))
        if GROUPS[index]:
            words.append(GROUPS[index])
    return " ".join(words)


def read_decimal(text: str) -> str:
    whole, _, fraction = text.replace(",", ".").partition(".")
    spoken = read_number(int(whole))
    return f"{spoken} phẩy {read_number(int(fraction))}" if fraction else spoken


# ----- money -------------------------------------------------------------------------------------------

_MONEY = re.compile(r"(?<![\w.,])(?:"
                    r"(?P<mil>\d{1,4})\s*(?:tr|triệu)(?P<milrest>\d{1,3})?(?![\w])"
                    r"|(?P<k>\d{1,6}(?:[.,]\d)?)\s*[kK](?![\w])"
                    r"|(?P<sep>\d{1,3}(?:[.,]\d{3})+)\s*(?:đ|đồng|vnd|vnđ|VND|VNĐ)?(?![\w])"
                    r"|(?P<plain>\d{4,10})\s*(?:đ|đồng|vnd|vnđ|VND|VNĐ)(?![\w])"
                    # A bare 5+ digit number in a sales reply is a price ("250000"): checked like one.
                    r"|(?P<bare>\d{5,10})(?![\w.,])"
                    r")", re.IGNORECASE)


def _money_value(match: re.Match) -> int:
    if match.group("mil"):
        rest = match.group("milrest") or ""
        return int(match.group("mil")) * 1_000_000 + (int(rest.ljust(3, "0")) * 1000 if rest else 0)
    if match.group("k"):
        return round(float(match.group("k").replace(",", ".")) * 1000)
    if match.group("sep"):
        return int(re.sub(r"[.,]", "", match.group("sep")))
    return int(match.group("plain") or match.group("bare"))


def money_amounts(text: str) -> list[int]:
    """Every amount of money written with digits in `text` ("200k", "1tr2", "199.000đ", "250000 VND")."""
    return [_money_value(m) for m in _MONEY.finditer(text or "")]


def price_short(value: float | None) -> str:
    """How a seller writes a price in chat: 199000 -> 199k, 1200000 -> 1tr2, 199500 -> 199.500đ."""
    if value is None:
        return ""
    n = int(round(value))
    if n >= 1_000_000 and n % 100_000 == 0:
        millions, rest = divmod(n, 1_000_000)
        return f"{millions}tr{str(rest // 100_000) if rest else ''}"
    if n % 1000 == 0:
        return f"{n // 1000}k"
    return f"{n:,}".replace(",", ".") + "đ"


# ----- spoken form -------------------------------------------------------------------------------------

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F\U0000200D\U00002190-\U000021FF\U00002B00-\U00002BFF]")
_URL = re.compile(r"(https?://\S+|www\.\S+|\b\S+\.(?:com|vn|net|shop|me|ly)\S*)", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?84|0)\d(?:[\s.-]?\d){7,9}(?!\d)")
_OPENERS = re.compile(r"^\s*(?:dạ+|vâng|xin chào|chào(?:\s+(?:anh chị|anh/chị|a/c|ac|các bạn|cả nhà|mọi người|bạn|shop|nhé|nha))*"
                      r"|cảm ơn[^,.!?]{0,40}|hello|hi)\b[\s,.!:;-]*", re.IGNORECASE)
# Size letters read the Vietnamese way: "size L" -> "size Lờ", "XL" -> "ích Lờ".
_SIZE = {"XS": "ích Ét", "S": "Ét", "M": "Mờ", "L": "Lờ", "XL": "ích Lờ", "XXL": "hai ích Lờ", "XXXL": "ba ích Lờ",
         "2XL": "hai ích Lờ", "3XL": "ba ích Lờ", "FREESIZE": "free size"}
# Short forms a seller types; only whole words are replaced, never inside other words.
_WORDS = {"ko": "không", "k": "không", "kh": "không", "hok": "không", "dc": "được", "đc": "được", "sp": "sản phẩm",
          "ib": "nhắn riêng", "inbox": "nhắn riêng", "sz": "size", "hn": "Hà Nội", "hcm": "Sài Gòn",
          "tphcm": "Sài Gòn", "sg": "Sài Gòn", "freeship": "miễn phí ship", "cod": "thanh toán khi nhận hàng"}


def _money_words(match: re.Match) -> str:
    if match.group("mil") and match.group("milrest") and len(match.group("milrest")) == 1:
        # "1tr2" is said "một triệu hai", the way sellers say it.
        return f"{read_number(int(match.group('mil')))} triệu {DIGITS[int(match.group('milrest'))]}"
    return read_number(_money_value(match))


def _height(match: re.Match) -> str:
    metres, rest = int(match.group(1)), match.group(2)
    return f"{read_number(metres)} mét {read_number(int(rest)) if rest else ''}".strip()


def _centimetres(match: re.Match) -> str:
    value = int(match.group(1))
    if 100 <= value < 300:
        metres, rest = divmod(value, 100)
        return f"{read_number(metres)} mét {read_number(rest) if rest else ''}".strip()
    return f"{read_number(value)} phân"


def _size(match: re.Match) -> str:
    code = match.group(2).upper()
    return f"{match.group(1)}{_SIZE.get(code, ' '.join(code))}"


def spoken(text: str, end: bool = True) -> str:
    """The reply as the voice should read it: no emoji, links or symbols, numbers and short forms in words.
    `end` adds the final period a full sentence needs (not for an intro like "Lan ơi,")."""
    t = unicodedata.normalize("NFC", text or "")
    t = _EMOJI.sub(" ", t)
    t = _URL.sub(" ", t)
    t = re.sub(r"[#@*_~`\"“”<>\[\]{}|\\]", " ", t)
    t = _MONEY.sub(_money_words, t)
    t = re.sub(r"(?<![\w])(\d)m(\d{1,2})?(?![\w])", _height, t)
    t = re.sub(r"(?<![\w])(\d{2,3})\s*cm(?![\w])", _centimetres, t, flags=re.IGNORECASE)
    t = re.sub(r"(?<![\w])(\d{1,3}(?:[.,]\d)?)\s*(?:kg|ký|kí|ki lô)(?![\w])", lambda m: f"{read_decimal(m.group(1))} ký", t,
               flags=re.IGNORECASE)
    t = re.sub(r"(?<![\w])(\d{1,3})\s*%", lambda m: f"{read_number(int(m.group(1)))} phần trăm", t)
    # "8h", "8h30" -> "tám giờ", "tám giờ ba mươi"
    t = re.sub(r"(?<![\w])(\d{1,2})h(\d{2})?(?![\w])", lambda m: f"{read_number(int(m.group(1)))} giờ" +
               (f" {read_number(int(m.group(2)))}" if m.group(2) else ""), t)
    # "2-4 ngày" -> "hai đến bốn ngày"
    t = re.sub(r"(?<![\w.,])(\d{1,3})\s*[-–]\s*(\d{1,3})(?![\w.,])",
               lambda m: f"{read_number(int(m.group(1)))} đến {read_number(int(m.group(2)))}", t)
    t = re.sub(r"\b((?:size|sz|cỡ)\s*)(XXXL|XXL|XL|XS|2XL|3XL|S|M|L|FREESIZE)\b", _size, t, flags=re.IGNORECASE)
    t = re.sub(r"\b((?:lấy|mặc|mang|bận|chọn|còn|có)\s+)(XXXL|XXL|XL|XS|2XL|3XL|S|M|L)\b", _size, t)
    t = re.sub(r"\bfree\s*ship\b", "miễn phí ship", t, flags=re.IGNORECASE)
    t = re.sub(r"[\wÀ-ỹ]+", lambda m: _WORDS.get(m.group(0).casefold(), m.group(0)), t)
    t = re.sub(r"(?<![\w])(\d+(?:[.,]\d+)?)(?![\w])", lambda m: read_decimal(m.group(1)), t)
    t = re.sub(r"[/+=&^$]", " ", t)
    t = re.sub(r"([!?.,])\1+", r"\1", t)
    t = re.sub(r"\s+([,.!?])", r"\1", t)
    t = " ".join(t.split())
    if end and t and t[-1] not in ".!?":
        t += "."
    return t[:1].upper() + t[1:]


def strip_openers(text: str) -> str:
    """Drop the robotic "Dạ chào anh chị, ..." / "Cảm ơn bạn đã hỏi" opening; keep the answer itself."""
    t = (text or "").strip()
    for _ in range(3):
        stripped = _OPENERS.sub("", t, count=1).strip()
        if stripped == t or not stripped:
            break
        t = stripped
    return t[:1].upper() + t[1:] if t else t


def clip(text: str, limit: int) -> str:
    """At most `limit` characters, cut at a sentence or word boundary."""
    t = " ".join((text or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end >= limit // 2 else cut.rsplit(" ", 1)[0].rstrip(",;:") + "."


# ----- safety ------------------------------------------------------------------------------------------

def fold(text: str) -> str:
    """Lowercase ASCII without Vietnamese marks, for matching ("Giá BN" -> "gia bn")."""
    t = unicodedata.normalize("NFD", (text or "").casefold().replace("đ", "d"))
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


# Typed abbreviations of swear words: matched without marks.
_BLOCKED_ASCII = ("dm", "dmm", "dcm", "vcl", "vkl", "clm", "cmm", "dkm", "suc vat")
# Words that are ordinary once their marks are gone ("còn cho", "dù mà"): matched with marks only.
_BLOCKED_MARKED = ("lồn", "cặc", "đéo", "địt", "đụ", "đụ má", "buồi", "ngu", "mẹ mày", "con chó", "óc chó", "thằng chó",
                   "lừa đảo", "khốn nạn")


def _words(text: str) -> str:
    return " " + " ".join(re.findall(r"\w+", text)) + " "


def blocked_words(text: str) -> list[str]:
    folded, marked = _words(fold(text)), _words((text or "").casefold())
    return ([w for w in _BLOCKED_ASCII if f" {w} " in folded] +
            [w for w in _BLOCKED_MARKED if f" {w} " in marked])


# Remarks on a viewer's body ("63kg nặng quá"): customers take them badly, so such a reply waits for the operator.
# Matched with marks only, as whole words.
_BODY_REMARKS = ("béo", "mập", "ú", "bự", "ục ịch", "mũm mĩm", "thừa cân", "quá khổ", "to con", "nặng quá", "quá nặng",
                 "hơi nặng", "nặng ký", "to quá", "quá to", "gầy quá", "quá gầy", "ốm quá", "quá ốm", "ốm nhom", "còi")


def body_remarks(text: str) -> list[str]:
    marked = _words((text or "").casefold())
    return [w for w in _BODY_REMARKS if f" {w} " in marked]


def safety_issues(text: str, allowed_amounts: set[int]) -> list[str]:
    """Why this reply must not go out automatically; empty when it may."""
    issues = []
    if blocked_words(text):
        issues.append("Có từ ngữ không phù hợp")
    remarks = body_remarks(text)
    if remarks:
        issues.append(f"Có từ nhận xét vóc dáng / cân nặng: {', '.join(remarks)}")
    if _URL.search(text or ""):
        issues.append("Có đường link")
    if _PHONE.search(text or ""):
        issues.append("Có số điện thoại")
    wrong = list(dict.fromkeys(a for a in money_amounts(_PHONE.sub(" ", text or "")) if a not in allowed_amounts))
    if wrong:
        issues.append(f"Số tiền không khớp dữ liệu sản phẩm: {', '.join(price_short(a) for a in wrong)}")
    if re.search(r"\b(nghìn|ngàn|triệu)\b", text or "", re.IGNORECASE) and not money_amounts(text):
        issues.append("Số tiền viết bằng chữ, không kiểm tra được")
    return issues


def allowed_amounts(product: dict) -> set[int]:
    """Prices a reply may say: the product's price and original price, and any amount its description states."""
    amounts = {int(round(v)) for v in (product.get("price"), product.get("origin_price")) if v}
    amounts.update(money_amounts(product.get("description") or ""))
    # Shop policies ("freeship đơn từ 300k") may be said too.
    amounts.update(money_amounts(" ".join((product.get("policies") or {}).values())))
    return amounts


# ----- viewer names ------------------------------------------------------------------------------------

# Words that are not a person's name: placeholders ("Khách 1"), TikTok's default "user123456", shop words.
_NOT_NAMES = {"khach", "user", "users", "guest", "viewer", "nguoi", "xem", "tiktok", "tiktoker", "shop", "admin", "acc",
              "account", "official", "store", "ban", "anh", "chi", "em", "test", "unknown", "customer", "live",
              "me", "bo", "ba", "co", "chu", "di", "bac", "ong", "cau", "mo", "thim", "be", "nha", "fan", "clone"}


def spoken_name(nickname: str | None, username: str | None) -> str | None:
    """A short name the voice can say ("Lan"), or None when there is nothing safe, readable and personal to say."""
    for raw in (nickname, re.sub(r"[_.\d]+", " ", username or "")):
        t = unicodedata.normalize("NFC", raw or "")
        t = _EMOJI.sub(" ", t)
        words = [w for w in re.findall(r"[A-Za-zÀ-ỹ]+", t) if 2 <= len(w) <= 12 and fold(w) not in _NOT_NAMES]
        if not words:
            continue
        name = words[0]
        if blocked_words(name) or blocked_words(t):
            return None
        return name.capitalize() if name.isupper() or name.islower() else name
    return None
