"""Private R2 adapter. Durable upload state is recovered from ListParts, never ETag-as-MD5."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Iterable
from urllib.parse import quote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.orm import Session

from app import credentials
from app.config import settings
from app.storage_errors import StorageError
from app.storage_models import StorageCredential
from app.storage_providers import ReadStream, StorageProvider, StoredObject, UploadProgress

PART_SIZE = 8 * 1024 * 1024


def configuration(db: Session) -> tuple[dict, str]:
    # A DB row is an explicit override, including a disconnected row; never silently fall back to ENV.
    row = db.get(StorageCredential, "R2")
    if row is not None and row.scopes != "ENV":
        value = credentials.decrypt(row.refresh_token_enc)
        if not value:
            raise StorageError("STORAGE_NOT_CONNECTED", "R2 đã ngắt kết nối hoặc khóa mã hóa không còn hợp lệ")
        return json.loads(value), "DATABASE"
    return dict(account_id=settings.sarae_r2_account_id, bucket=settings.sarae_r2_bucket,
                access_key_id=settings.sarae_r2_access_key_id, secret_access_key=settings.sarae_r2_secret_access_key,
                endpoint=settings.sarae_r2_endpoint, region=settings.sarae_r2_region), "ENV"


def validate_config(value: dict) -> dict:
    value = dict(value)
    if not all(value.get(k) for k in ("account_id", "bucket", "access_key_id", "secret_access_key")):
        raise StorageError("STORAGE_NOT_CONFIGURED", "Nhập đủ Account ID, bucket và hai khóa R2")
    if any(len(value[k]) > 200 for k in ("access_key_id", "secret_access_key")):
        raise StorageError("STORAGE_NOT_CONFIGURED", "Khóa R2 không hợp lệ")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", value["account_id"]):
        raise StorageError("STORAGE_NOT_CONFIGURED", "Account ID phải gồm 32 ký tự hex")
    endpoint = value.get("endpoint") or f"https://{value['account_id']}.r2.cloudflarestorage.com"
    try:
        url = urlsplit(endpoint)
        port = url.port
    except ValueError:
        raise StorageError("STORAGE_NOT_CONFIGURED", "Endpoint R2 không hợp lệ") from None
    allowed = {f"{value['account_id']}{suffix}.r2.cloudflarestorage.com" for suffix in ("", ".eu", ".fedramp")}
    if url.scheme != "https" or url.hostname not in allowed or port not in (None, 443) or url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
        raise StorageError("STORAGE_NOT_CONFIGURED", "Endpoint phải là HTTPS S3 API của tài khoản Cloudflare R2 này")
    value["endpoint"], value["region"] = endpoint.rstrip("/"), value.get("region") or "auto"
    return value


class CloudflareR2StorageProvider(StorageProvider):
    name = "R2"

    def __init__(self, db: Session, require_connected: bool = True):
        super().__init__(db)
        self.config, self.source = configuration(db)
        row = db.get(StorageCredential, "R2")
        if require_connected and row is not None and row.status == "DISCONNECTED":
            raise StorageError("STORAGE_NOT_CONNECTED", "Kho R2 chưa kết nối; hãy kiểm tra kết nối trong Admin")
        self.config = validate_config(self.config)
        self.bucket = self.config["bucket"]
        self.revision = hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()
        self.client = boto3.client("s3", endpoint_url=self.config["endpoint"], region_name=self.config["region"],
                                   aws_access_key_id=self.config["access_key_id"],
                                   aws_secret_access_key=self.config["secret_access_key"],
                                   config=Config(signature_version="s3v4", connect_timeout=10, read_timeout=120,
                                                 retries={"mode": "standard", "max_attempts": 3},
                                                 s3={"addressing_style": "path"},
                                                 request_checksum_calculation="when_required",
                                                 response_checksum_validation="when_required"))

    def _call(self, operation: str, **kwargs):
        try:
            return getattr(self.client, operation)(Bucket=self.bucket, **kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            errors = {
                "InvalidAccessKeyId": ("STORAGE_CREDENTIAL_INVALID", "Access Key ID R2 không hợp lệ"),
                "SignatureDoesNotMatch": ("STORAGE_CREDENTIAL_INVALID", "Secret Access Key hoặc chữ ký R2 không hợp lệ"),
                "AccessDenied": ("STORAGE_FORBIDDEN", "Token R2 không đủ quyền truy cập bucket"),
                "403": ("STORAGE_FORBIDDEN", "R2 từ chối truy cập; kiểm tra hai khóa và quyền bucket"),
                "401": ("STORAGE_CREDENTIAL_INVALID", "Thông tin xác thực R2 không hợp lệ"),
                "NoSuchBucket": ("STORAGE_BUCKET_NOT_FOUND", "Bucket R2 không tồn tại"),
                "NoSuchKey": ("STORAGE_OBJECT_NOT_FOUND", "Video không tồn tại trên R2"),
                "404": ("STORAGE_OBJECT_NOT_FOUND", "Không tìm thấy bucket hoặc video trên R2"),
                "NoSuchUpload": ("STORAGE_UPLOAD_EXPIRED", "Multipart upload đã hết hạn hoặc bị hủy"),
                "InvalidRange": ("STORAGE_RANGE_INVALID", "Range vượt kích thước video"),
                "BadDigest": ("STORAGE_CHECKSUM_MISMATCH", "Checksum phần upload không khớp"),
            }
            mapped, message = errors.get(code, ("STORAGE_PROVIDER_ERROR", "R2 không thể hoàn tất thao tác; hãy thử lại"))
            if operation == "head_bucket" and code == "404":
                mapped, message = "STORAGE_BUCKET_NOT_FOUND", "Bucket R2 không tồn tại; kiểm tra tên bucket và Account ID"
            raise StorageError(mapped, message) from None
        except BotoCoreError:
            raise StorageError("STORAGE_UNREACHABLE", "Kết nối R2 timeout hoặc gián đoạn; hãy thử lại") from None

    def create_container(self, owner_id: str) -> str:
        return f"owners/{quote(owner_id, safe='')}/"

    def begin_upload(self, owner_id: str, media_id: str, filename: str, mime_type: str, size: int) -> str:
        key = self.create_container(owner_id) + f"media/{media_id}/video"
        result = self._call("create_multipart_upload", Key=key, ContentType=mime_type,
                            Metadata={"sarae-media-id": media_id})
        return json.dumps(dict(key=key, upload_id=result["UploadId"], revision=self.revision))

    def _state(self, session: str) -> dict:
        state = json.loads(session)
        if state["revision"] != self.revision:
            raise StorageError("STORAGE_CONFIG_CHANGED", "Cấu hình R2 đã thay đổi; hãy hủy và upload lại")
        return state

    def _parts(self, state: dict) -> list[dict]:
        parts, marker = [], 0
        while True:
            result = self._call("list_parts", Key=state["key"], UploadId=state["upload_id"], PartNumberMarker=marker)
            parts.extend(result.get("Parts", []))
            if not result.get("IsTruncated"):
                return parts
            marker = result["NextPartNumberMarker"]

    def _finish(self, session: str, state: dict, parts: list[dict], total: int) -> UploadProgress:
        received = sum(p["Size"] for p in parts)
        if received != total:
            return UploadProgress(session, received)
        self._call("complete_multipart_upload", Key=state["key"], UploadId=state["upload_id"],
                   MultipartUpload={"Parts": [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts]})
        return UploadProgress(session, total, self._verified(state["key"]))

    def upload_chunk(self, session: str, start: int, data: bytes | Iterable[bytes], length: int, total: int) -> UploadProgress:
        state = self._state(session)
        if start % PART_SIZE or length != min(PART_SIZE, total - start):
            raise StorageError("UPLOAD_CHUNK_ALIGN", "R2 yêu cầu phần 8 MiB, trừ phần cuối")
        if not isinstance(data, bytes):
            data = b"".join(data)
        if len(data) != length:
            raise StorageError("UPLOAD_LENGTH_MISMATCH", "Dung lượng phần upload không khớp")
        parts = self._parts(state)
        received = sum(p["Size"] for p in parts)
        if start > received:
            raise StorageError("UPLOAD_OFFSET_MISMATCH", "Hãy tải lại trạng thái upload để tiếp tục")
        number = start // PART_SIZE + 1
        if start == received:
            self._call("upload_part", Key=state["key"], UploadId=state["upload_id"], PartNumber=number,
                       Body=data, ContentLength=length,
                       ContentMD5=base64.b64encode(hashlib.md5(data).digest()).decode())
        return self._finish(session, state, self._parts(state), total)

    def upload_status(self, session: str, total: int) -> UploadProgress:
        state = self._state(session)
        try:
            return self._finish(session, state, self._parts(state), total)
        except StorageError as exc:
            if exc.code != "STORAGE_UPLOAD_EXPIRED":
                raise
            obj = self._verified(state["key"])
            if obj is None or obj.size_bytes != total:
                raise exc
            return UploadProgress(session, total, obj)

    def cancel_upload(self, session: str) -> None:
        state = self._state(session)
        try:
            self._call("abort_multipart_upload", Key=state["key"], UploadId=state["upload_id"])
        except StorageError as exc:
            if exc.code != "STORAGE_UPLOAD_EXPIRED":
                raise
            # Complete may have succeeded while its response was lost; cancellation removes that object too.
            if self.describe(state["key"]) is not None:
                self.delete(state["key"])

    def open_read(self, object_id: str, range_header: str | None = None) -> ReadStream:
        result = self._call("get_object", Key=object_id, **({"Range": range_header} if range_header else {}))
        body = result["Body"]
        headers = {"Content-Length": str(result["ContentLength"]), "Accept-Ranges": "bytes",
                   "Content-Type": result.get("ContentType", "video/mp4")}
        if "ContentRange" in result:
            headers["Content-Range"] = result["ContentRange"]
        def chunks():
            try:
                yield from body.iter_chunks(chunk_size=1024 * 1024)
            except BotoCoreError:
                raise StorageError("STORAGE_UNREACHABLE", "Tải R2 gián đoạn; hãy tiếp tục bằng Range") from None
        return ReadStream(206 if "ContentRange" in result else 200, headers, chunks(), body.close)

    def delete(self, object_id: str) -> None:
        self._call("delete_object", Key=object_id)

    def describe(self, object_id: str) -> StoredObject | None:
        try:
            result = self._call("head_object", Key=object_id)
        except StorageError as exc:
            if exc.code == "STORAGE_OBJECT_NOT_FOUND":
                return None
            raise
        return StoredObject(object_id, result["ContentLength"])

    def _verified(self, object_id: str) -> StoredObject | None:
        obj = self.describe(object_id)
        if obj is None:
            return None
        # Reconstruct a durable whole-file checksum even after a Backend restart or lost chunk response.
        digest = hashlib.sha256()
        for chunk in self.open_read(object_id).iter():
            digest.update(chunk)
        obj.sha256 = digest.hexdigest()
        return obj

    def health(self) -> dict:
        self._call("head_bucket")
        return {"provider": self.name, "bucket": self.bucket}
