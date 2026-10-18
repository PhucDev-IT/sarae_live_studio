"""Bounded comment batch queue for each OWNER's LIVE product. No automatic speech or posting."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import RLock, Timer
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.identity import current_owner
from app.ownership import Owner
from app.owner_agents import AgentId, owned_agent
from app.per_owner import PerOwner, live_key
from app.catalog import Product
from app.database import SessionLocal
from app.comment_processing import monitors, normalize
from app import ai_service

router = APIRouter(prefix="/api/ai/live", tags=["AI LIVE Batching"], dependencies=[Depends(current_owner)])


class BatchOptions(BaseModel):
    enabled: bool = False
    window_seconds: float = Field(default=2.5, ge=1, le=10)
    max_comments: int = Field(default=8, ge=1, le=30)


class BatchState:
    """Pending comments, answer cache and history of one LIVE desk (an OWNER's Windows Agent)."""

    def __init__(self, key: str):
        self.key = key
        self.owner_id, self.agent_id = key.split(":", 1)
        self.lock = RLock()
        self.pending = {}
        self.results = deque(maxlen=150)
        self.cache = {}
        self.sequence = 0
        self.timers = {}
        self.stats = {"received": 0, "processed": 0, "cached": 0, "batches": 0, "skipped": 0}


states = PerOwner(BatchState)
# Batching options are an OWNER preference shared by all their Agents.
owner_options = PerOwner(lambda _owner_id: {"value": BatchOptions(), "lock": RLock()})
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sarae-ai-live")


def context(owner_id: str, agent_id: str):
    """The product live in the RUNNING session on that Agent, snapshotted when the comment arrives."""
    from app.live_sessions import LiveSession
    with SessionLocal() as db:
        session = db.scalars(select(LiveSession).where(LiveSession.owner_id == owner_id, LiveSession.agent_id == agent_id,
                                                       LiveSession.status == "RUNNING")
                             .order_by(LiveSession.started_at.desc())).first()
        if session is None or session.current_product_id is None:
            return None
        product = db.get(Product, session.current_product_id)
        if not product or product.owner_id != owner_id or product.status != "ACTIVE":
            return None
        details = {"id": product.id, "name": product.name, "price": product.price,
                   "description": product.description[:950], "status": product.status}
        fingerprint = sha256(json.dumps(details, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        return {"session_id": session.id, "product": details, "version": fingerprint}


def flush(st: BatchState, key):
    with st.lock:
        timer = st.timers.pop(key, None)
        if timer:
            timer.cancel()
        items = st.pending.pop(key, [])
        if not items:
            return
        st.sequence += 1
        batch_id = st.sequence
        st.stats["batches"] += 1
    executor.submit(run_batch, st, batch_id, items)


def run_batch(st: BatchState, batch_id, items):
    monitor = monitors(st.key)
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
    config = ai_service.load_config(st.owner_id)
    cache_key = sha256(json.dumps({"model": config.model, "provider": config.provider,
                                    "version": ctx["version"], "questions": [q["text"] for q in questions]},
                                   ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with st.lock:
        cached = st.cache.get(cache_key)
    if cached is not None:
        result["answer"] = cached
        result["cache_hit"] = True
        result["status"] = "ready"
        monitor.record("CACHE", "hit", f"Batch #{batch_id}: same questions, model and product version")
        with st.lock:
            st.stats["cached"] += 1
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
            response = ai_service.analyze(st.owner_id, body, log_key=st.key)
            result["answer"] = response
            result["status"] = "ready"
            with st.lock:
                st.cache[cache_key] = response
                if len(st.cache) > 250:
                    st.cache.pop(next(iter(st.cache)))
        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(getattr(exc, "detail", exc))[:300]
            monitor.record("BATCH_ERROR", "failed", f"Batch #{batch_id}: {result['error']}")
    with st.lock:
        st.stats["processed"] += len(items)
        st.results.append(result)


def enqueue(owner_id: str, agent_id: str, message):
    st = states(live_key(owner_id, agent_id))
    options = owner_options(owner_id)["value"]
    if not options.enabled:
        return
    ctx = context(owner_id, agent_id)
    if not ctx:
        monitors(st.key).record("BATCH_SKIP", "no_live_product", "Chưa có sản phẩm đang phát trong phiên LIVE; không gọi AI.")
        with st.lock:
            st.stats["skipped"] += 1
        return
    key = (ctx["session_id"], ctx["product"]["id"], ctx["version"])
    item = {"id": message.get("id"), "username": message.get("username") or "",
            "comment": message.get("comment") or "", "context": ctx}
    with st.lock:
        st.stats["received"] += 1
        queue = st.pending.setdefault(key, [])
        queue.append(item)
        if key not in st.timers:
            timer = Timer(options.window_seconds, flush, args=(st, key))
            timer.daemon = True
            st.timers[key] = timer
            timer.start()
        full = len(queue) >= options.max_comments
    if full:
        flush(st, key)


@router.get("/config")
def get_config(owner: Owner):
    opts = owner_options(owner["id"])
    with opts["lock"]:
        return opts["value"].model_dump()


@router.put("/config")
def set_config(body: BatchOptions, owner: Owner):
    opts = owner_options(owner["id"])
    with opts["lock"]:
        opts["value"] = body
        return body.model_dump()


@router.get("/status")
def get_status(owner: Owner, agent_id: AgentId, after: int = 0):
    st = states(live_key(owner["id"], owned_agent(owner, agent_id)["id"]))
    with st.lock:
        entries = [r for r in st.results if r["batch_id"] > after]
        return {"stats": st.stats.copy(), "last_id": st.results[-1]["batch_id"] if st.results else 0,
                "batches": entries, "pending": sum(map(len, st.pending.values()))}
