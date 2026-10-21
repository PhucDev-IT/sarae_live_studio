"""Google Drive OAuth PoC — Phase 1 of the Google Drive Media Library task.

Standalone: does not start FastAPI, does not touch the Sarae database and never
prints or writes access/refresh tokens. It answers, against a REAL Google
account, the questions that decide the OAuth scope:

  C1  User selects an existing folder that already contains videos
  C2  API lists the existing files in that folder
  C3  User adds a new file directly in Google Drive (outside Sarae)
  C4  Sarae can see the new file (files.list + changes API)
  C5  API downloads a private file (Range request + optional full md5 check)
  C6  Refresh token works, then access is rejected after the user revokes

Prerequisites (Google Cloud Console, project of the developer):
  * Enable "Google Drive API" (and "Google Picker API" for --scope file).
  * OAuth client of type "Web application" with authorized redirect URI
    http://127.0.0.1:8765/callback  (and JS origin http://127.0.0.1:8765
    for the Picker page).
  * While the consent screen is in "Testing", add the Google account as a
    test user.

Environment variables (never hardcode credentials):
  GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET
  GOOGLE_PICKER_API_KEY, GOOGLE_CLOUD_PROJECT_NUMBER   (only for --scope file)

Usage (from backend/):
  .venv\\Scripts\\python.exe tools/google_drive_oauth_poc.py --scope file
  .venv\\Scripts\\python.exe tools/google_drive_oauth_poc.py --scope readonly --folder-id <ID>
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
DRIVE = "https://www.googleapis.com/drive/v3"
SCOPES = {
    # Non-sensitive: per-file access (files created by the app or picked via Picker).
    "file": ["https://www.googleapis.com/auth/drive.file"],
    # Restricted: read every file of the account; needs restricted-scope verification.
    # drive.file is kept so Sarae can upload into the folder.
    "readonly": [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.file",
    ],
}
FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id,name,mimeType,size,md5Checksum,modifiedTime,parents,trashed,videoMediaMetadata"
RANGE_BYTES = 1024 * 1024


def stamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{stamp()}] {msg}", flush=True)


# ---------------------------------------------------------------- pure helpers

def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def state_matches(expected: str, received: str | None) -> bool:
    return bool(received) and hmac.compare_digest(expected, received)


def build_authorize_url(client_id: str, redirect_uri: str, scopes: list[str], state: str, challenge: str) -> str:
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # offline + consent: Google only returns a refresh_token on (re)consent.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(query)


def folder_children_query(folder_id: str) -> str:
    safe = folder_id.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{safe}' in parents and trashed = false"


def diff_ids(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, list[str]]:
    old = {f["id"] for f in before}
    new = {f["id"] for f in after}
    return {"added": sorted(new - old), "removed": sorted(old - new)}


def redact(text: str) -> str:
    """Strip anything that looks like a Google token from error text."""
    out = []
    for word in text.split():
        out.append("<redacted>" if word.strip('"\',').startswith(("ya29.", "1//")) else word)
    return " ".join(out)


# ---------------------------------------------------------------- report

@dataclass
class Check:
    code: str
    title: str
    status: str = "NOT_RUN"  # PASS / FAIL / NOT_RUN / MANUAL_SKIPPED
    detail: dict[str, Any] = field(default_factory=dict)


class Report:
    def __init__(self, scope_key: str):
        self.scope_key = scope_key
        self.started_at = stamp()
        self.checks = {c.code: c for c in [
            Check("C1", "Folder selected and readable"),
            Check("C2", "List existing files in folder"),
            Check("C3", "User added new file directly in Drive"),
            Check("C4", "New file visible to Sarae"),
            Check("C5", "Download private file (Range + integrity)"),
            Check("C6a", "Refresh token issues new access token"),
            Check("C6b", "Access rejected after revoke"),
        ]}

    def set(self, code: str, ok: bool | None, **detail: Any) -> None:
        check = self.checks[code]
        check.status = "PASS" if ok else ("FAIL" if ok is False else "MANUAL_SKIPPED")
        check.detail.update(detail)
        log(f"{code} {check.status}: {check.title} {json.dumps(detail, ensure_ascii=False)[:300]}")

    def dump(self, path: str) -> None:
        data = {
            "scope": self.scope_key,
            "scopes_requested": SCOPES[self.scope_key],
            "started_at": self.started_at,
            "finished_at": stamp(),
            "checks": [c.__dict__ for c in self.checks.values()],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        log(f"Report written to {path}")


# ---------------------------------------------------------------- loopback server

class _Inbox:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.callback: dict[str, str] = {}
        self.picked: list[dict[str, Any]] = []
        self.picker_html = ""


def _make_handler(inbox: _Inbox):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:  # keep tokens/codes out of stdout
            pass

        def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8") -> None:
            raw = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            url = urllib.parse.urlparse(self.path)
            if url.path == "/callback":
                inbox.callback = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
                inbox.event.set()
                self._send(200, "<p>Sarae PoC: you can close this tab and return to the terminal.</p>")
            elif url.path == "/picker" and inbox.picker_html:
                self._send(200, inbox.picker_html)
            else:
                self._send(404, "not found", "text/plain")

        def do_POST(self) -> None:
            if self.path != "/picked":
                self._send(404, "not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                inbox.picked = json.loads(self.rfile.read(length) or b"[]")
            except ValueError:
                inbox.picked = []
            inbox.event.set()
            self._send(200, "{}", "application/json")

    return Handler


PICKER_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Sarae Drive Picker PoC</title></head>
<body style="font-family:sans-serif"><p id="s">Loading Google Picker...</p>
<script src="https://apis.google.com/js/api.js"></script>
<script>
const CFG = __CFG__;
function done(items){fetch('/picked',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(items)})
  .then(()=>{document.getElementById('s').textContent='Selection sent. Return to the terminal.';});}
gapi.load('picker', () => {
  const folders = new google.picker.DocsView(google.picker.ViewId.FOLDERS)
      .setIncludeFolders(true).setSelectFolderEnabled(true).setMimeTypes('__FOLDER__');
  const docs = new google.picker.DocsView(google.picker.ViewId.DOCS)
      .setIncludeFolders(true).setSelectFolderEnabled(true);
  new google.picker.PickerBuilder()
    .addView(folders).addView(docs)
    .enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
    .setOAuthToken(CFG.token).setDeveloperKey(CFG.apiKey).setAppId(CFG.appId)
    .setCallback(d => {
      if (d.action === google.picker.Action.PICKED) done(d.docs.map(x => ({id:x.id,name:x.name,mimeType:x.mimeType})));
      if (d.action === google.picker.Action.CANCEL) done([]);
    }).build().setVisible(true);
  document.getElementById('s').textContent='Pick ONE folder that already contains videos.';
});
</script></body></html>"""


# ---------------------------------------------------------------- Drive client

class Drive:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.http = httpx.Client(timeout=60, follow_redirects=True)
        self.access_token = ""
        self.refresh_token = ""

    def exchange(self, code: str, verifier: str) -> dict[str, Any]:
        r = self.http.post(TOKEN_URL, data={
            "code": code, "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier,
        })
        r.raise_for_status()
        body = r.json()
        self.access_token = body["access_token"]
        self.refresh_token = body.get("refresh_token", "")
        return {"granted_scopes": body.get("scope", "").split(), "expires_in": body.get("expires_in"),
                "has_refresh_token": bool(self.refresh_token)}

    def refresh(self) -> tuple[int, str]:
        r = self.http.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": self.refresh_token, "grant_type": "refresh_token",
        })
        if r.status_code == 200:
            self.access_token = r.json()["access_token"]
            return 200, "ok"
        return r.status_code, redact(r.json().get("error", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text)

    def revoke(self) -> int:
        # Revoking the refresh token also invalidates access tokens of the grant.
        r = self.http.post(REVOKE_URL, data={"token": self.refresh_token or self.access_token})
        return r.status_code

    def _get(self, path: str, **kw: Any) -> httpx.Response:
        headers = kw.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"
        return self.http.get(DRIVE + path, headers=headers, **kw)

    def get_file(self, file_id: str) -> httpx.Response:
        return self._get(f"/files/{file_id}", params={"fields": FILE_FIELDS, "supportsAllDrives": "true"})

    def list_children(self, folder_id: str) -> tuple[int, list[dict[str, Any]], str]:
        items: list[dict[str, Any]] = []
        token = None
        while True:
            params = {"q": folder_children_query(folder_id), "fields": f"nextPageToken,files({FILE_FIELDS})",
                      "pageSize": "1000", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if token:
                params["pageToken"] = token
            r = self._get("/files", params=params)
            if r.status_code != 200:
                return r.status_code, items, redact(r.text[:300])
            body = r.json()
            items.extend(body.get("files", []))
            token = body.get("nextPageToken")
            if not token:
                return 200, items, ""

    def start_page_token(self) -> str:
        r = self._get("/changes/startPageToken", params={"supportsAllDrives": "true"})
        r.raise_for_status()
        return r.json()["startPageToken"]

    def list_changes(self, page_token: str) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        while page_token:
            r = self._get("/changes", params={
                "pageToken": page_token, "fields": f"nextPageToken,newStartPageToken,changes(fileId,removed,file({FILE_FIELDS}))",
                "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "pageSize": "1000"})
            r.raise_for_status()
            body = r.json()
            changes.extend(body.get("changes", []))
            page_token = body.get("nextPageToken")
        return changes

    def download_range(self, file_id: str, start: int, end: int) -> httpx.Response:
        return self._get(f"/files/{file_id}", params={"alt": "media", "supportsAllDrives": "true"},
                         headers={"Range": f"bytes={start}-{end}"})

    def download_md5(self, file_id: str) -> tuple[int, int, str]:
        md5 = hashlib.md5()
        size = 0
        with self.http.stream("GET", DRIVE + f"/files/{file_id}",
                              params={"alt": "media", "supportsAllDrives": "true"},
                              headers={"Authorization": f"Bearer {self.access_token}"}) as r:
            if r.status_code != 200:
                return r.status_code, 0, ""
            for chunk in r.iter_bytes(1024 * 1024):  # streamed, never fully in RAM
                md5.update(chunk)
                size += len(chunk)
        return 200, size, md5.hexdigest()


def is_video(f: dict[str, Any]) -> bool:
    return str(f.get("mimeType", "")).startswith("video/")


# ---------------------------------------------------------------- flow

def wait(inbox: _Inbox, seconds: int) -> bool:
    ok = inbox.event.wait(seconds)
    inbox.event.clear()
    return ok


def run(args: argparse.Namespace) -> int:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        log("ERROR missing GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET")
        return 2
    if args.scope == "file" and not (os.environ.get("GOOGLE_PICKER_API_KEY") and os.environ.get("GOOGLE_CLOUD_PROJECT_NUMBER")):
        log("ERROR --scope file needs GOOGLE_PICKER_API_KEY and GOOGLE_CLOUD_PROJECT_NUMBER for Google Picker")
        return 2
    if args.scope == "readonly" and not args.folder_id:
        log("ERROR --scope readonly needs --folder-id (copy the ID from the Drive folder URL)")
        return 2

    report = Report(args.scope)
    redirect_uri = f"http://127.0.0.1:{args.port}/callback"
    inbox = _Inbox()
    server = HTTPServer(("127.0.0.1", args.port), _make_handler(inbox))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    drive = Drive(client_id, client_secret, redirect_uri)
    try:
        # ---- OAuth authorization code + PKCE + state
        state = secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        url = build_authorize_url(client_id, redirect_uri, SCOPES[args.scope], state, challenge)
        log("Opening Google consent screen in the browser...")
        webbrowser.open(url)
        if not wait(inbox, 300):
            log("ERROR consent timed out")
            return 1
        if not state_matches(state, inbox.callback.get("state")):
            log("ERROR state mismatch — callback rejected (CSRF protection)")
            return 1
        if "error" in inbox.callback:
            log(f"ERROR consent refused: {inbox.callback['error']}")
            return 1
        grant = drive.exchange(inbox.callback["code"], verifier)
        inbox.callback = {}
        log(f"Token exchange OK {json.dumps(grant)}")

        # ---- C1 select folder
        folder_id = args.folder_id
        if args.scope == "file":
            cfg = {"token": drive.access_token, "apiKey": os.environ["GOOGLE_PICKER_API_KEY"],
                   "appId": os.environ["GOOGLE_CLOUD_PROJECT_NUMBER"]}
            inbox.picker_html = PICKER_PAGE.replace("__CFG__", json.dumps(cfg)).replace("__FOLDER__", FOLDER_MIME)
            webbrowser.open(f"http://127.0.0.1:{args.port}/picker")
            log("Pick a folder in the browser Picker page...")
            if not wait(inbox, 600) or not inbox.picked:
                report.set("C1", False, reason="no folder picked")
                return 1
            inbox.picker_html = ""  # drop the page that embeds the short-lived token
            folders = [p for p in inbox.picked if p.get("mimeType") == FOLDER_MIME]
            if not folders:
                report.set("C1", False, reason="picked items are not folders", picked=inbox.picked)
                return 1
            folder_id = folders[0]["id"]
        r = drive.get_file(folder_id)
        report.set("C1", r.status_code == 200 and r.json().get("mimeType") == FOLDER_MIME,
                   http=r.status_code, folder_id=folder_id,
                   name=r.json().get("name") if r.status_code == 200 else redact(r.text[:200]))

        # ---- C2 list existing files
        page_token = drive.start_page_token()
        code, before, err = drive.list_children(folder_id)
        videos = [f for f in before if is_video(f)]
        report.set("C2", code == 200 and len(videos) > 0, http=code, error=err,
                   items_visible=len(before), videos_visible=len(videos),
                   sample=[f.get("name") for f in videos[:5]],
                   note="Compare items_visible with what Drive UI shows in this folder")
        if args.expected_count is not None:
            report.checks["C2"].detail["expected_count"] = args.expected_count
            if len(before) != args.expected_count:
                report.set("C2", False, reason="visible count differs from what the user sees in Drive")

        # ---- C3/C4 new file added outside Sarae
        if args.skip_manual:
            report.set("C3", None)
            report.set("C4", None)
        else:
            input("\n>>> Now open drive.google.com, upload a NEW video into this folder, wait until "
                  "the upload finishes, then press Enter here... ")
            report.set("C3", True, confirmed_by="operator")
            code, after, err = drive.list_children(folder_id)
            delta = diff_ids(before, after)
            changes = drive.list_changes(page_token)
            changed_in_folder = [c["fileId"] for c in changes
                                 if folder_id in (c.get("file") or {}).get("parents", [])]
            report.set("C4", bool(delta["added"]), http=code, error=err, list_delta=delta,
                       changes_api_hits_in_folder=changed_in_folder)
            before = after if code == 200 else before
            videos = [f for f in before if is_video(f)]

        # ---- C5 download private file
        target = next((f for f in videos if f.get("md5Checksum")), videos[0] if videos else None)
        if target is None:
            report.set("C5", False, reason="no visible video to download")
        else:
            size = int(target.get("size") or 0)
            r = drive.download_range(target["id"], 0, RANGE_BYTES - 1)
            detail: dict[str, Any] = {"file_id": target["id"], "http": r.status_code,
                                      "content_range": r.headers.get("content-range"), "bytes": len(r.content)}
            ok = r.status_code in (200, 206) and len(r.content) > 0
            if ok and args.full_download and size <= args.max_full_mb * 1024 * 1024:
                status, got, digest = drive.download_md5(target["id"])
                detail.update(full_http=status, full_bytes=got, size_expected=size,
                              md5_match=digest == target.get("md5Checksum"))
                ok = ok and status == 200 and got == size and digest == target.get("md5Checksum")
            report.set("C5", ok, **detail)

        # ---- C6 refresh then revoke
        status, msg = drive.refresh()
        report.set("C6a", status == 200, http=status, error=None if status == 200 else msg)
        revoke_status = drive.revoke()
        time.sleep(3)
        after_revoke = drive.get_file(folder_id).status_code
        refresh_after, refresh_err = drive.refresh()
        report.set("C6b", revoke_status == 200 and after_revoke in (401, 403) and refresh_after != 200,
                   revoke_http=revoke_status, drive_http_after_revoke=after_revoke,
                   refresh_http_after_revoke=refresh_after, refresh_error=refresh_err)
        return 0 if all(c.status in ("PASS", "MANUAL_SKIPPED") for c in report.checks.values()) else 1
    finally:
        report.dump(args.report)
        server.shutdown()
        drive.access_token = drive.refresh_token = ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Google Drive OAuth folder-access PoC for Sarae")
    parser.add_argument("--scope", choices=sorted(SCOPES), required=True)
    parser.add_argument("--folder-id", help="Drive folder ID (required for --scope readonly)")
    parser.add_argument("--expected-count", type=int, help="Number of items the user sees in the folder in Drive UI")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--report", default="google_drive_poc_report.json")
    parser.add_argument("--full-download", action="store_true", help="Also stream the whole file and compare md5")
    parser.add_argument("--max-full-mb", type=int, default=200)
    parser.add_argument("--skip-manual", action="store_true", help="Skip C3/C4 (no manual upload step)")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
