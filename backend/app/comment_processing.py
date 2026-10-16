"""Observable deterministic comment-processing POC. No model/API call or TikTok reply."""
from collections import deque
from datetime import datetime, timezone
import re
import threading
import time
import unicodedata


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def classify(text: str):
    t = normalize(text)
    if any(term in t for term in ("xem mẫu", "xem mau", "cho xem", "xem áo", "xem ao", "mẫu nào", "mau nao")):
        return "SHOW_PRODUCT", "Khách yêu cầu xem sản phẩm; cần đối chiếu danh mục LIVE trước khi chuyển."
    if any(term in t for term in ("bao nhiêu", "bao nhieu", "giá", "gia bao", "nhiêu tiền")):
        return "ASK_PRICE", "Cần giá của sản phẩm hiện tại từ database trước khi trả lời."
    if any(term in t for term in ("size", "kích cỡ", "vừa không", "vua khong")):
        return "ASK_SIZE", "Cần bảng size và sản phẩm cụ thể trước khi trả lời."
    if any(term in t for term in ("ship", "vận chuyển", "giao hàng")):
        return "ASK_SHIPPING", "Cần chính sách vận chuyển trước khi trả lời."
    return "OTHER", "Chưa thấy ý định bán hàng đủ rõ; không phản hồi tự động."


class Monitor:
    def __init__(self):
        self.lock = threading.RLock()
        self.logs = deque(maxlen=1000)
        self.seq = 0
        self.event_seen = {}
        self.spam_seen = {}
        self.selected = 0
        self.duplicate = 0
        self.spam = 0
        self.simulated = False

    def reset(self):
        with self.lock:
            self.logs.clear()
            self.seq = self.selected = self.duplicate = self.spam = 0
            self.event_seen.clear()
            self.spam_seen.clear()

    def _log(self, stage, decision, text, comment_id=None, username=None):
        self.seq += 1
        self.logs.append({"id": self.seq, "timestamp": datetime.now(timezone.utc).isoformat(),
                          "stage": stage, "decision": decision, "text": text,
                          "comment_id": comment_id, "username": username})

    def process(self, message, event_id=None):
        user = message.get("username") or "unknown"
        content = message.get("comment") or ""
        mid = message.get("id")
        now = time.monotonic()
        with self.lock:
            self._log("RECEIVED", "accepted", content, mid, user)
            # A delivery event ID is not the same as comment text. Never collapse real
            # messages just because they say the same thing.
            if event_id:
                # Prune bounded TTL maps.
                self.event_seen = {k:v for k,v in self.event_seen.items() if now-v < 600}
                if event_id in self.event_seen:
                    self.duplicate += 1
                    self._log("DEDUP", "duplicate_event", "Cùng TikTok message ID; bỏ qua lần giao trùng.", mid, user)
                    return "duplicate"
                self.event_seen[event_id] = now
                self._log("DEDUP", "unique_event", "TikTok message ID chưa xuất hiện.", mid, user)
            else:
                self._log("DEDUP", "unverified", "Không có message ID ổn định; không thể xác minh trùng event.", mid, user)

            key = (user, normalize(content))
            self.spam_seen = {k:v for k,v in self.spam_seen.items() if now-v < 60}
            if key in self.spam_seen and now-self.spam_seen[key] < 20:
                self.spam += 1
                self._log("ANTI_SPAM", "suppressed", "Cùng người và nội dung trong 20 giây; giữ chat, không xử lý lần nữa.", mid, user)
                return "spam"
            self.spam_seen[key] = now
            self._log("ANTI_SPAM", "pass", "Cho phép xử lý.", mid, user)
            self.selected += 1
            self._log("SELECT", "selected", "Chọn bình luận này để phân loại.", mid, user)
            intent, explanation = classify(content)
            self._log("INTENT_RULES", intent, explanation, mid, user)
            self._log("RESPONSE", "not_generated", "Chưa kết nối AI model; chưa tạo câu trả lời thực. Chỉ phân loại bằng quy tắc.", mid, user)
            return "selected"

    def snapshot(self, after=0):
        with self.lock:
            return {"last_id": self.seq, "selected": self.selected, "duplicates": self.duplicate,
                    "spam_suppressed": self.spam, "mode": "rules_only_no_ai",
                    "logs": [x for x in self.logs if x["id"] > after]}


monitor = Monitor()
