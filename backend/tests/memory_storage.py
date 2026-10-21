"""In-memory StorageProvider: proves the Media Library works with a second provider (stand-in for S3/R2)."""
import hashlib
import itertools
import json
import re

from app.storage_errors import StorageError
from app.storage_providers import ReadStream, StorageProvider, StoredObject, UploadProgress


class MemoryStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.ids = itertools.count(1)
        self.corrupt_writes = False


class MemoryStorageProvider(StorageProvider):
    name = "MEMORY"
    store = MemoryStore()

    def create_container(self, owner_id):
        return f"owners/{owner_id}"

    def begin_upload(self, owner_id, media_id, filename, mime_type, size):
        return json.dumps({"key": f"{self.owner_container(owner_id)}/{media_id}", "size": size, "data": ""})

    def upload_chunk(self, session, start, data, length, total):
        state = json.loads(session)
        buffer = bytes.fromhex(state["data"])
        if start != len(buffer):
            raise StorageError("STORAGE_PROVIDER_ERROR", "offset")
        buffer += data if isinstance(data, bytes) else b"".join(data)
        if len(buffer) < total:
            return UploadProgress(json.dumps({**state, "data": buffer.hex()}), len(buffer))
        if self.store.corrupt_writes:
            buffer = b"X" + buffer[1:]
        key = f"{state['key']}-{next(self.store.ids)}"
        self.store.objects[key] = buffer
        return UploadProgress(session, len(buffer), StoredObject(key, len(buffer), hashlib.md5(buffer).hexdigest()))

    def upload_status(self, session, total):
        return UploadProgress(session, len(bytes.fromhex(json.loads(session)["data"])))

    def cancel_upload(self, session):
        pass

    def open_read(self, object_id, range_header=None):
        if object_id not in self.store.objects:
            raise StorageError("STORAGE_OBJECT_NOT_FOUND", "missing", 404)
        data = self.store.objects[object_id]
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header or "")
        if match:
            start, end = int(match.group(1)), int(match.group(2) or len(data) - 1)
            part = data[start:end + 1]
            headers = {"content-range": f"bytes {start}-{start + len(part) - 1}/{len(data)}", "content-length": str(len(part))}
            return ReadStream(206, headers, iter([part]), lambda: None)
        return ReadStream(200, {"content-length": str(len(data))}, iter([data[i:i + 65536] for i in range(0, len(data), 65536)]), lambda: None)

    def delete(self, object_id):
        self.store.objects.pop(object_id, None)
