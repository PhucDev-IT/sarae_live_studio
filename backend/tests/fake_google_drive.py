"""In-process fake of the Google OAuth + Drive v3 endpoints Sarae uses (httpx.MockTransport handler).

Behaves like Google where it matters to Sarae: PKCE-checked code exchange, refresh/revoke (invalid_grant), bearer
checks (401), drive.file folders tagged with appProperties, resumable uploads (308 + Range, 404 after cancel),
alt=media with Range (206), md5Checksum, thumbnails. Test switches: `granted_scopes`, `corrupt_uploads`.
"""
import base64
import hashlib
import itertools
import json
import re
import urllib.parse

import httpx

SCOPE_FILE = "https://www.googleapis.com/auth/drive.file"


class FakeGoogle:
    def __init__(self, email="storage@sarae.test", permission_id="perm-1"):
        self.ids = itertools.count(1)
        self.account = {"emailAddress": email, "displayName": "Sarae Storage", "permissionId": permission_id}
        self.granted_scopes = SCOPE_FILE
        self.codes: dict[str, dict] = {}
        self.refresh_tokens: dict[str, bool] = {}  # token -> valid
        self.access_tokens: dict[str, str] = {}  # access -> refresh it came from
        self.revoked_access: set[str] = set()
        self.files: dict[str, dict] = {}
        self.content: dict[str, bytes] = {}
        self.sessions: dict[str, dict] = {}
        self.corrupt_uploads = False
        self.refresh_calls = 0
        self.revoke_calls = 0

    # ----- helpers for tests -----
    def approve(self, authorize_url: str) -> tuple[str, str]:
        """The admin clicks Allow: returns (code, state) as Google would put them on the redirect."""
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(authorize_url).query))
        code = f"code-{next(self.ids)}"
        self.codes[code] = {"challenge": q["code_challenge"], "redirect_uri": q["redirect_uri"]}
        return code, q["state"]

    def revoke_everything(self):
        """The account owner removes Sarae at myaccount.google.com/permissions."""
        for token in self.refresh_tokens:
            self.refresh_tokens[token] = False
        self.revoked_access.update(self.access_tokens)

    def expire_access_tokens(self):
        self.revoked_access.update(self.access_tokens)

    def children(self, parent_id: str) -> list[dict]:
        return [f for f in self.files.values() if parent_id in f.get("parents", []) and not f.get("trashed")]

    def folder_by_prop(self, key: str, value: str) -> dict | None:
        return next((f for f in self.files.values() if f.get("appProperties", {}).get(key) == value), None)

    # ----- transport -----
    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "oauth2.googleapis.com" and url.path == "/token":
            return self._token(dict(urllib.parse.parse_qsl(request.content.decode())))
        if url.host == "oauth2.googleapis.com" and url.path == "/revoke":
            return self._revoke(dict(urllib.parse.parse_qsl(request.content.decode()))["token"])
        if url.host == "lh3.fake":
            return httpx.Response(200, content=b"\xff\xd8thumb", headers={"content-type": "image/jpeg"})
        if url.path.startswith("/upload/drive/v3/files") and "upload_id" in url.params:
            return self._upload_put(request)
        if not self._authorized(request):
            return httpx.Response(401, json={"error": {"code": 401, "status": "UNAUTHENTICATED"}})
        if url.path == "/drive/v3/about":
            return httpx.Response(200, json={"user": self.account, "storageQuota": {"limit": "16106127360", "usage": str(sum(map(len, self.content.values())))}})
        if url.path == "/upload/drive/v3/files" and request.method == "POST":
            return self._upload_start(request)
        if url.path == "/drive/v3/files" and request.method == "GET":
            return self._list(url.params.get("q", ""))
        if url.path == "/drive/v3/files" and request.method == "POST":
            return self._create(json.loads(request.content))
        match = re.fullmatch(r"/drive/v3/files/([^/]+)", url.path)
        if match:
            file_id = urllib.parse.unquote(match.group(1))
            if request.method == "DELETE":
                if self.files.pop(file_id, None) is None:
                    return httpx.Response(404, json={"error": {"code": 404}})
                self.content.pop(file_id, None)
                return httpx.Response(204)
            if file_id not in self.files:
                return httpx.Response(404, json={"error": {"code": 404, "errors": [{"reason": "notFound"}]}})
            if url.params.get("alt") == "media":
                return self._media(file_id, request.headers.get("range"))
            if url.params.get("fields") == "thumbnailLink":
                return httpx.Response(200, json={"thumbnailLink": f"https://lh3.fake/thumb/{file_id}"})
            return httpx.Response(200, json=self.files[file_id])
        return httpx.Response(404, json={"error": {"code": 404, "message": f"fake: no route {request.method} {url.path}"}})

    def _authorized(self, request: httpx.Request) -> bool:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        return token in self.access_tokens and token not in self.revoked_access

    def _issue_access(self, refresh: str) -> str:
        token = f"ya29.fake-{next(self.ids)}"
        self.access_tokens[token] = refresh
        return token

    def _token(self, form: dict) -> httpx.Response:
        if form.get("grant_type") == "authorization_code":
            pending = self.codes.pop(form.get("code", ""), None)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
            if pending is None or pending["challenge"] != challenge or pending["redirect_uri"] != form.get("redirect_uri"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            refresh = f"1//fake-refresh-{next(self.ids)}"
            self.refresh_tokens[refresh] = True
            return httpx.Response(200, json={"access_token": self._issue_access(refresh), "expires_in": 3599,
                                             "refresh_token": refresh, "scope": self.granted_scopes, "token_type": "Bearer"})
        if form.get("grant_type") == "refresh_token":
            self.refresh_calls += 1
            refresh = form.get("refresh_token", "")
            if not self.refresh_tokens.get(refresh):
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
            return httpx.Response(200, json={"access_token": self._issue_access(refresh), "expires_in": 3599,
                                             "scope": self.granted_scopes, "token_type": "Bearer"})
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def _revoke(self, token: str) -> httpx.Response:
        self.revoke_calls += 1
        refresh = self.access_tokens.get(token, token)
        if refresh not in self.refresh_tokens:
            return httpx.Response(400, json={"error": "invalid_token"})
        self.refresh_tokens[refresh] = False
        self.revoked_access.update(a for a, r in self.access_tokens.items() if r == refresh)
        return httpx.Response(200)

    def _new_file(self, meta: dict) -> dict:
        file_id = f"file-{next(self.ids)}"
        record = {"id": file_id, "name": meta.get("name", ""), "mimeType": meta.get("mimeType", "application/octet-stream"),
                  "parents": meta.get("parents", ["root"]), "appProperties": meta.get("appProperties", {}), "trashed": False}
        self.files[file_id] = record
        return record

    def _create(self, meta: dict) -> httpx.Response:
        return httpx.Response(200, json=self._new_file(meta))

    def _list(self, q: str) -> httpx.Response:
        files = [f for f in self.files.values() if not f.get("trashed")]
        prop = re.search(r"appProperties has \{ key='([^']+)' and value='([^']+)' \}", q)
        if prop:
            files = [f for f in files if f.get("appProperties", {}).get(prop.group(1)) == prop.group(2)]
        parent = re.search(r"'([^']+)' in parents", q)
        if parent:
            files = [f for f in files if parent.group(1) in f.get("parents", [])]
        if "mimeType = 'application/vnd.google-apps.folder'" in q:
            files = [f for f in files if f["mimeType"] == "application/vnd.google-apps.folder"]
        return httpx.Response(200, json={"files": files})

    def _upload_start(self, request: httpx.Request) -> httpx.Response:
        meta = json.loads(request.content)
        session = f"sess{next(self.ids)}"
        self.sessions[session] = {"meta": meta, "total": int(request.headers["x-upload-content-length"]),
                                  "data": bytearray(), "cancelled": False, "file": None}
        location = f"https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id={session}"
        return httpx.Response(200, headers={"location": location})

    def _upload_put(self, request: httpx.Request) -> httpx.Response:
        session = self.sessions.get(request.url.params["upload_id"])
        if request.method == "DELETE":
            if session:
                session["cancelled"] = True
            return httpx.Response(499)
        if session is None or session["cancelled"]:
            return httpx.Response(404, json={"error": {"code": 404}})
        if session["file"]:
            return httpx.Response(200, json=session["file"])
        content_range = request.headers.get("content-range", "")
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
        if match and int(match.group(1)) == len(session["data"]):
            session["data"] += request.content
        return self._upload_state(session)

    def append_directly(self, session_uri: str, data: bytes):
        """Bytes that reached Google although Sarae never saw the response (lost reply / Backend restart)."""
        session = self.sessions[urllib.parse.parse_qs(urllib.parse.urlparse(session_uri).query)["upload_id"][0]]
        session["data"] += data

    def _upload_state(self, session: dict) -> httpx.Response:
        data = session["data"]
        if len(data) < session["total"]:
            headers = {"range": f"bytes=0-{len(data) - 1}"} if data else {}
            return httpx.Response(308, headers=headers)
        stored = bytes(data)
        if self.corrupt_uploads:
            stored = bytes([stored[0] ^ 0xFF]) + stored[1:]
        record = self._new_file(session["meta"])
        record.update({"size": str(len(stored)), "md5Checksum": hashlib.md5(stored).hexdigest(), "hasThumbnail": True,
                       "videoMediaMetadata": {"width": 1080, "height": 1920, "durationMillis": "12500"}})
        self.content[record["id"]] = stored
        session["file"] = record
        return httpx.Response(200, json=record)

    def _media(self, file_id: str, range_header: str | None) -> httpx.Response:
        data = self.content.get(file_id, b"")
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header or "")
        if not match:
            return httpx.Response(200, content=data, headers={"content-type": self.files[file_id]["mimeType"],
                                                              "content-length": str(len(data))})
        start = int(match.group(1) or 0)
        end = min(int(match.group(2)) if match.group(2) else len(data) - 1, len(data) - 1)
        if start >= len(data):
            return httpx.Response(416, headers={"content-range": f"bytes */{len(data)}"})
        return httpx.Response(206, content=data[start:end + 1], headers={
            "content-type": self.files[file_id]["mimeType"], "content-range": f"bytes {start}-{end}/{len(data)}",
            "content-length": str(end - start + 1)})
