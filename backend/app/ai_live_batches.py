"""Bounded comment batch queue for the active LIVE product. No automatic speech or posting."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import RLock, Timer
from time import monotonic
import json

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.catalog import Product
from app.database import SessionLocal
from app.live import state
from app.comment_processing import monitor, normalize
from app import ai_service

router = APIRouter(prefix="/api/ai/live", tags=["AI LIVE Batching"])

class BatchOptions(BaseModel):
    enabled: bool = False
    window_seconds: float = Field(default=2.5, ge=1, le=10)
    max_comments: int = Field(default=8, ge=1, le=30)

lock = RLock()
options = BatchOptions()
pending = {}
results = deque(maxlen=150)
cache = {}
sequence = 0
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sarae-ai-live")
timers = {}
stats = {"received": 0, "processed": 0, "cached": 0, "batches": 0, "skipped": 0}

def context():
    # Snapshot when message arrives, never when a scheduled batch happens later.
    with state.lock:
        sid = state.session_id if state.active else None
        pid = state.product_id if state.active and state.session_mode == "product" else None
    if not pid:
        return None
    with SessionLocal() as db:
        product = db.get(Product, pid)
        if not product or product.status != "ACTIVE":
            return None
        details = {"id": product.id, "name": product.name, "price": product.price,
                   "description": product.description[:1200], "status": product.status}
        fingerprint = sha256(json.dumps(details, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        return {"session_id": sid, "product": details, "version": fingerprint}

def flush(key):
    global sequence
    with lock:
        timers.pop(key, None)
        items = pending.pop(key, [])
        if not items:
            return
        sequence += 1
        batch_id = sequence
        stats["batches"] += 1
    executor.submit(run_batch, batch_id, items)

def run_batch(batch_id, items):
    ctx = items[0]["context"]
    product = ctx["product"]
    # Exact normalized repetitions are one intent request, but keep all affected users.
    groups = {}
    for item in items:
        group_key = normalize(item["comment"])
        groups.setdefault(group_key, []).append(item)
    questions = [{"id": i + 1, "text": group[0]["comment"], "users": [x["username"] for x in group]}
                 for i, group in enumerate(groups.values())]
    result = {"batch_id": batch_id, "session_id": ctx["session_id"], "product_id": product["id"],
              "product_name": product["name"], "comments": items, "questions": questions,
              "answer": None, "status": "processing", "cache_hit": False}
    monitor.record("BATCH", "grouped", f"#{batch_id}: {len(items)} comments → {len(questions)} unique; product #{product['id']}")
    cache_key = sha256(json.dumps({"model": ai_service.load_config().model,
                                    "provider": ai_service.load_config().provider,
                                    "version": ctx["version"], "questions": [q["text"] for q in questions]},
                                   ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with lock:
        cached = cache.get(cache_key)
    if cached is not None:
        result["answer"] = cached
        result["cache_hit"] = True
        result["status"] = "ready"
        monitor.record("CACHE", "hit", f"Batch #{batch_id}: same questions, model and product version")
        with lock:
            stats["cached"] += 1
    else:
        try:
            # A single LLM call for every question in the same time window.
            question_text = json.dumps(questions, ensure_ascii=False)
            body = ai_service.AnalyzeBody(
                comment="Phân loại tất cả bình luận sau, gom các câu hỏi cùng ý, tạo MỘT câu trả lời ngắn gọn chung nếu phù hợp. "
                        "Khi không chắc hoặc thiếu dữ liệu, hỏi lại; không tự bịa size/giá. "
                        "Trả thêm 'groups' là danh sách các nhóm {intent, question_ids, reply}, ngoài các trường JSON tiêu chuẩn.\n"
                        + question_text,
                username="live-batch",
                product_name=product["name"],
                product_details=f"Giá: {product['price']} VND; trạng thái: {product['status']}; mô tả: {product['description']}"
            )
            response = ai_service.analyze(body)
            result["answer"] = response
            result["status"] = "ready"
            with lock:
                cache[cache_key] = response
                if len(cache) > 250:
                    cache.pop(next(iter(cache)))
        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(getattr(exc, "detail", exc))[:300]
            monitor.record("BATCH_ERROR", "failed", f"Batch #{batch_id}: {result['error']}")
    with lock:
        stats["processed"] += len(items)
        results.append(result)

def enqueue(message):
    with lock:
        if not options.enabled:
            return
    ctx = context()
    if not ctx:
        monitor.record("BATCH_SKIP", "no_live_product", "Chưa có sản phẩm đang phát trên OBS; không gọi AI.")
        with lock:
            stats["skipped"] += 1
        return
    key = (ctx["session_id"], ctx["product"]["id"], ctx["version"])
    item = {"id": message.get("id"), "username": message.get("username") or "",
            "comment": message.get("comment") or "", "context": ctx}
    with lock:
        stats["received"] += 1
        queue = pending.setdefault(key, [])
        queue.append(item)
        if key not in timers:
            timer = Timer(options.window_seconds, flush, args=(key,))
            timer.daemon = True
            timers[key] = timer
            timer.start()
        full = len(queue) >= options.max_comments
    if full:
        with lock:
            timer = timers.pop(key, None)
            if timer:
                timer.cancel()
        flush(key)

@router.get("/config")
def get_config():
    with lock:
        return options.model_dump()

@router.put("/config")
def set_config(body: BatchOptions):
    global options
    with lock:
        options = body
        return options.model_dump()

@router.get("/status")
def get_status(after: int = 0):
    with lock:
        entries = [r for r in results if r["batch_id"] > after]
        return {"stats": stats.copy(), "last_id": sequence, "batches": entries, "pending": sum(map(len, pending.values()))}
