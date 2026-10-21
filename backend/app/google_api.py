"""Thin Google OAuth + Drive v3 HTTP client for the system storage account (GoogleDriveStorageProvider).

- Scope: drive.file only — Sarae manages the files it creates (root folder, per-owner folders, uploaded videos).
  The Phase 1 PoC (docs/GOOGLE_DRIVE_OAUTH_POC.md) showed drive.file cannot see files added outside the app; with
  centralized storage every video is uploaded through Sarae, so that limitation no longer applies.
- No Google SDK: plain httpx, so tests swap the transport (`set_transport`) for an in-process fake Google.
- Never logs tokens or resumable session URIs (both grant access); errors carry a stable `code`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import urllib.parse
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.storage_errors import StorageError

log = logging.getLogger("sarae.storage.google")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
DRIVE = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SCOPE_FILE = "https://www.googleapis.com/auth/drive.file"
SCOPES = (SCOPE_FILE,)
FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id,name,mimeType,size,md5Checksum,trashed,parents,hasThumbnail,videoMediaMetadata(width,height,durationMillis)"

_transport: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | None):
    """Tests route every Google call to a fake; None restores the network."""
    global _transport
    _transport = transport


def client(timeout: float = 30) -> httpx.Client:
    return httpx.Client(timeout=timeout, transport=_transport, follow_redirects=False)


class GoogleError(StorageError):
    """A Google failure, already expressed with the provider-neutral storage codes."""


def redact(text: object) -> str:
    return " ".join("<redacted>" if w.strip('"\',:').startswith(("ya29.", "1//", "https://www.googleapis.com/upload"))
                    else w for w in str(text).split())


def configured() -> bool:
    return bool(settings.sarae_google_client_id and settings.sarae_google_client_secret and settings.sarae_google_redirect_uri)


# ----- OAuth (admin-only connect of the system account) -----------------------------------------

def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(state: str, challenge: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": settings.sarae_google_client_id,
        "redirect_uri": settings.sarae_google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",  # Google returns a refresh token only on (re)consent
    })


@dataclass
class TokenGrant:
    access_token: str
    expires_in: int
    refresh_token: str | None
    scopes: list[str]


def _token_error(response: httpx.Response, default_code: str) -> GoogleError:
    try:
        error = response.json().get("error", "")
    except ValueError:
        error = ""
    if error == "invalid_grant":
        return GoogleError("STORAGE_TOKEN_REVOKED", "Quyền Google Drive của hệ thống đã bị thu hồi hoặc hết hạn", response.status_code)
    return GoogleError(default_code, f"Google từ chối xác thực ({redact(error or response.status_code)})", response.status_code)


def _token_request(data: dict[str, str], default_code: str) -> TokenGrant:
    try:
        with client() as http:
            r = http.post(TOKEN_URL, data={**data, "client_id": settings.sarae_google_client_id,
                                           "client_secret": settings.sarae_google_client_secret})
    except httpx.HTTPError as exc:
        raise GoogleError("STORAGE_UNREACHABLE", "Không kết nối được Google", None) from exc
    if r.status_code != 200:
        raise _token_error(r, default_code)
    body = r.json()
    return TokenGrant(body["access_token"], int(body.get("expires_in", 3600)), body.get("refresh_token"), body.get("scope", "").split())


def exchange_code(code: str, verifier: str) -> TokenGrant:
    return _token_request({"code": code, "code_verifier": verifier, "grant_type": "authorization_code",
                           "redirect_uri": settings.sarae_google_redirect_uri}, "STORAGE_EXCHANGE_FAILED")


def refresh(refresh_token: str) -> TokenGrant:
    return _token_request({"refresh_token": refresh_token, "grant_type": "refresh_token"}, "STORAGE_REFRESH_FAILED")


def revoke(token: str) -> bool:
    """Best effort: True when Google confirmed (400 = token already invalid)."""
    try:
        with client(timeout=10) as http:
            return http.post(REVOKE_URL, data={"token": token}).status_code in (200, 400)
    except httpx.HTTPError as exc:
        log.warning("google revoke failed: %s", type(exc).__name__)
        return False


# ----- Drive ----------------------------------------------------------------------------------

def drive_error(r: httpx.Response) -> GoogleError:
    reason = ""
    try:
        error = r.json().get("error", {})
        reason = (error.get("errors") or [{}])[0].get("reason", "") or error.get("status", "")
    except (ValueError, AttributeError):
        pass
    if r.status_code == 401:
        return GoogleError("STORAGE_TOKEN_INVALID", "Phiên Google Drive của hệ thống không còn hiệu lực", 401)
    if r.status_code == 404:
        return GoogleError("STORAGE_OBJECT_NOT_FOUND", "Không tìm thấy file trên kho lưu trữ", 404)
    if r.status_code == 429 or reason in ("rateLimitExceeded", "userRateLimitExceeded"):
        return GoogleError("STORAGE_RATE_LIMITED", "Kho lưu trữ đang giới hạn tần suất; thử lại sau", 429)
    if r.status_code == 403 and reason == "storageQuotaExceeded":
        return GoogleError("STORAGE_QUOTA_EXCEEDED", "Kho Google Drive của hệ thống đã hết dung lượng", 403)
    if r.status_code == 403:
        return GoogleError("STORAGE_FORBIDDEN", f"Google Drive từ chối thao tác ({redact(reason)})", 403)
    return GoogleError("STORAGE_PROVIDER_ERROR", f"Google Drive lỗi HTTP {r.status_code}", r.status_code)


@dataclass
class UploadState:
    received: int  # bytes Google has persisted
    file: dict[str, Any] | None  # final file resource when complete


_RANGE = re.compile(r"bytes=0-(\d+)")


def _upload_state(r: httpx.Response) -> UploadState:
    if r.status_code in (200, 201):
        return UploadState(int(r.json().get("size") or 0), r.json())
    if r.status_code == 308:
        match = _RANGE.match(r.headers.get("range", ""))
        return UploadState(int(match.group(1)) + 1 if match else 0, None)
    if r.status_code in (404, 410):
        raise GoogleError("STORAGE_UPLOAD_EXPIRED", "Phiên upload đã hết hạn; hãy upload lại", r.status_code)
    raise drive_error(r)


class Drive:
    """Drive v3 calls with one access token (refreshed by the provider)."""

    def __init__(self, access_token: str):
        self._auth = {"Authorization": f"Bearer {access_token}"}

    def _call(self, method: str, url: str, **kw: Any) -> httpx.Response:
        headers = {**self._auth, **kw.pop("headers", {})}
        try:
            with client(timeout=kw.pop("timeout", 30)) as http:
                return http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise GoogleError("STORAGE_UNREACHABLE", "Không kết nối được Google Drive", None) from exc

    def _json(self, method: str, url: str, **kw: Any) -> dict[str, Any]:
        r = self._call(method, url, **kw)
        if r.status_code in (200, 201):
            return r.json()
        raise drive_error(r)

    def about(self) -> dict[str, Any]:
        return self._json("GET", DRIVE + "/about", params={"fields": "user(displayName,emailAddress,permissionId),storageQuota"})

    def get_file(self, file_id: str, fields: str = FILE_FIELDS) -> dict[str, Any]:
        return self._json("GET", f"{DRIVE}/files/{urllib.parse.quote(file_id, safe='')}", params={"fields": fields})

    def find_folder(self, app_key: str, app_value: str, parent: str | None = None) -> dict[str, Any] | None:
        """A folder this app created and tagged with appProperties (drive.file can list its own files)."""
        q = (f"mimeType = '{FOLDER_MIME}' and trashed = false and "
             f"appProperties has {{ key='{app_key}' and value='{app_value}' }}")
        if parent:
            q += f" and '{parent}' in parents"
        files = self._json("GET", DRIVE + "/files", params={"q": q, "fields": "files(id,name)", "pageSize": "10"}).get("files", [])
        return files[0] if files else None

    def create_folder(self, name: str, parent: str | None, app_properties: dict[str, str]) -> dict[str, Any]:
        meta: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME, "appProperties": app_properties}
        if parent:
            meta["parents"] = [parent]
        return self._json("POST", DRIVE + "/files", params={"fields": "id,name"}, json=meta)

    def delete_file(self, file_id: str):
        r = self._call("DELETE", f"{DRIVE}/files/{urllib.parse.quote(file_id, safe='')}")
        if r.status_code not in (200, 204, 404):
            raise drive_error(r)

    # Resumable upload: https://developers.google.com/drive/api/guides/manage-uploads#resumable
    def begin_upload(self, name: str, parent: str, mime_type: str, size: int, app_properties: dict[str, str]) -> str:
        r = self._call("POST", UPLOAD, params={"uploadType": "resumable", "fields": FILE_FIELDS},
                       headers={"X-Upload-Content-Type": mime_type, "X-Upload-Content-Length": str(size)},
                       json={"name": name, "parents": [parent], "mimeType": mime_type, "appProperties": app_properties})
        if r.status_code != 200 or not r.headers.get("location"):
            raise drive_error(r)
        return r.headers["location"]

    def upload_chunk(self, session_uri: str, start: int, data: bytes | Iterable[bytes], length: int, total: int) -> UploadState:
        """PUT bytes [start, start+length) of `total`. The session URI itself authorizes the upload."""
        headers = {"Content-Length": str(length), "Content-Range": f"bytes {start}-{start + length - 1}/{total}"}
        try:
            with client(timeout=300) as http:
                r = http.put(session_uri, content=data, headers=headers)
        except httpx.HTTPError as exc:
            raise GoogleError("STORAGE_UNREACHABLE", "Mất kết nối khi gửi dữ liệu lên Google Drive", None) from exc
        return _upload_state(r)

    def upload_status(self, session_uri: str, total: int) -> UploadState:
        try:
            with client() as http:
                r = http.put(session_uri, headers={"Content-Length": "0", "Content-Range": f"bytes */{total}"})
        except httpx.HTTPError as exc:
            raise GoogleError("STORAGE_UNREACHABLE", "Không kết nối được Google Drive", None) from exc
        return _upload_state(r)

    def cancel_upload(self, session_uri: str):
        try:
            with client(timeout=10) as http:
                http.delete(session_uri)
        except httpx.HTTPError:
            log.warning("google upload cancel failed (session expires on its own)")

    def thumbnail(self, file_id: str) -> tuple[bytes, str] | None:
        link = self.get_file(file_id, "thumbnailLink").get("thumbnailLink")
        if not link:
            return None
        r = self._call("GET", link, timeout=15)
        if r.status_code != 200 or len(r.content) > 2_000_000:
            return None
        return r.content, r.headers.get("content-type", "image/jpeg")

    def open_media(self, file_id: str, range_header: str | None) -> "MediaStream":
        """files.get?alt=media, forwarding Range so players can seek. The caller must consume or close it."""
        headers = dict(self._auth)
        if range_header:
            headers["Range"] = range_header
        http = client(timeout=60)
        try:
            request = http.build_request("GET", f"{DRIVE}/files/{urllib.parse.quote(file_id, safe='')}",
                                         params={"alt": "media"}, headers=headers)
            response = http.send(request, stream=True)
        except httpx.HTTPError as exc:
            http.close()
            raise GoogleError("STORAGE_UNREACHABLE", "Không kết nối được Google Drive", None) from exc
        if response.status_code not in (200, 206, 416):
            response.read()
            response.close()
            http.close()
            raise drive_error(response)
        return MediaStream(http, response)


class MediaStream:
    def __init__(self, http: httpx.Client, response: httpx.Response):
        self.http = http
        self.response = response

    @property
    def status(self) -> int:
        return self.response.status_code

    def headers(self) -> dict[str, str]:
        keep = ("content-type", "content-length", "content-range", "accept-ranges")
        return {k: v for k, v in self.response.headers.items() if k.lower() in keep}

    def iter(self, chunk: int = 256 * 1024) -> Iterator[bytes]:
        try:
            yield from self.response.iter_bytes(chunk)
        finally:
            self.close()

    def close(self):
        self.response.close()
        self.http.close()


def app_properties(**values: str) -> dict[str, str]:
    """Drive appProperties are limited to 124 bytes per key+value; ids are short."""
    return {k: json.dumps(v)[1:-1][:100] for k, v in values.items()}
