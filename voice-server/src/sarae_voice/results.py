"""Result cache for job audio (separate from the VoiceClonePrompt cache).

Key = sha256 of everything that decides the audio AND who may get it: owner_ref, voice_id, voice rev, prompt compat
(checkpoint revision, omnivoice version, prompt format, preprocessing), preset generation config, exact text, seed.
A cached file is only handed to a new job whose key is identical, so a result never crosses owners, voices,
checkpoints or configurations. Reading still requires that job's own read token (or the orchestrator token).
Bounded by TTL and total bytes; files referenced by unfinished jobs are never evicted.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path


class ResultCache:
    def __init__(self, root: Path, ttl_s: float, max_bytes: int):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_s = ttl_s
        self.max_bytes = max_bytes
        self.pinned: dict[str, int] = {}
        self._lock = threading.Lock()
        self.stats = {"hit": 0, "miss": 0, "stored": 0, "evicted": 0, "rejected": 0}

    @staticmethod
    def key(**parts) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:40]

    def path(self, key: str) -> Path:
        return self.root / f"{key}.wav"

    def lookup(self, key: str, basis: dict) -> dict | None:
        wav, meta_path = self.path(key), self.root / f"{key}.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("basis") != basis or wav.stat().st_size != meta.get("bytes") \
                    or time.time() - meta.get("created", 0) > self.ttl_s:
                self.stats["rejected"] += 1
                return None
            os.utime(wav)
            self.stats["hit"] += 1
            return meta
        except (OSError, ValueError):
            self.stats["miss"] += 1
            return None

    def store(self, key: str, data: bytes, basis: dict, info: dict) -> dict:
        meta = {"key": key, "basis": basis, "bytes": len(data), "created": time.time(),
                "sha256": hashlib.sha256(data).hexdigest(), **info}
        tmp = self.root / f".{key}.{os.getpid()}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, self.path(key))
        (self.root / f"{key}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        self.stats["stored"] += 1
        return meta

    def pin(self, key: str) -> None:
        with self._lock:
            self.pinned[key] = self.pinned.get(key, 0) + 1

    def unpin(self, key: str) -> None:
        with self._lock:
            n = self.pinned.get(key, 0) - 1
            if n <= 0:
                self.pinned.pop(key, None)
            else:
                self.pinned[key] = n

    def usage(self) -> dict:
        files = list(self.root.glob("*.wav"))
        return {"files": len(files), "bytes": sum(f.stat().st_size for f in files if f.exists())}

    def cleanup(self) -> int:
        """Drop expired files, then the least recently used until under max_bytes; also stray temp files."""
        now = time.time()
        removed = 0
        for tmp in self.root.glob(".*.tmp"):
            if now - tmp.stat().st_mtime > 3600:
                tmp.unlink(missing_ok=True)
        entries = []
        for wav in self.root.glob("*.wav"):
            key = wav.stem
            try:
                st = wav.stat()
                meta = json.loads((self.root / f"{key}.json").read_text(encoding="utf-8"))
                created = meta.get("created", st.st_mtime)
            except (OSError, ValueError):
                created, st = 0, None
            entries.append((key, created, st.st_mtime if st else 0, st.st_size if st else 0))
        total = sum(e[3] for e in entries)
        with self._lock:
            pinned = set(self.pinned)
        for key, created, last_used, size in sorted(entries, key=lambda e: e[2]):
            if key in pinned:
                continue
            if now - created > self.ttl_s or total > self.max_bytes:
                self.path(key).unlink(missing_ok=True)
                (self.root / f"{key}.json").unlink(missing_ok=True)
                total -= size
                removed += 1
        for meta in self.root.glob("*.json"):          # metadata whose audio is gone
            if not self.path(meta.stem).exists():
                meta.unlink(missing_ok=True)
        self.stats["evicted"] += removed
        return removed
